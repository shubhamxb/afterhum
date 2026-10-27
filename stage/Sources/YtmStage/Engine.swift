// the engine link: one unix socket to mpv (the player bin/ytm starts), listening for property changes.
// commands go out on the same socket. the stage never plays audio itself; it's a face for the same player the cli drives.
import Foundation
import AppKit

let HOME = FileManager.default.homeDirectoryForCurrentUser
let DATA: URL = {
    if let d = ProcessInfo.processInfo.environment["YTM_DATA"] { return URL(fileURLWithPath: d) }
    return HOME.appendingPathComponent(".local/share/ytm")
}()
/// the cli this app drives. build.sh records where the repo lives in the bundle (Resources/root); YTM_BIN overrides it (tests).
let ROOT: URL? = {
    guard let f = Bundle.main.url(forResource: "root", withExtension: nil),
          let s = try? String(contentsOf: f, encoding: .utf8) else { return nil }
    let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
    return t.isEmpty ? nil : URL(fileURLWithPath: t)
}()
let YTM: String = ProcessInfo.processInfo.environment["YTM_BIN"]
    ?? ROOT?.appendingPathComponent("bin/ytm").path
    ?? HOME.appendingPathComponent(".local/share/ytm/repo/bin/ytm").path

struct Track: Equatable {
    var vid = "", title = "", artist = "", album = "", art = ""
}

/// a mood: ytm itself is the list (19 built in, plus the ones made with "new mood"), so the stage never goes stale
struct ModeInfo: Identifiable, Equatable { let id: String; let group: String; let what: String }

struct QueueItem: Identifiable, Equatable { let id: Int; let title: String; let current: Bool }

final class Player: ObservableObject {
    @Published var track = Track()
    @Published var paused = true
    @Published var duration: Double = 0
    @Published var volume: Double = 60
    @Published var queue: [QueueItem] = []
    @Published var connected = false
    @Published var status = ""
    @Published var mode = "normal"
    @Published var rating = ""                    // "LIKE" | "DISLIKE" | "" for the song playing: the local ledger (lib/ratings.py)
    @Published var modes: [ModeInfo] = Player.fallbackModes

    // time is interpolated between mpv's updates, so the 60 fps stage never sees it stutter
    private var posAt: Double = 0, stampAt = Date()
    var position: Double { paused ? posAt : min(duration > 0 ? duration : .greatestFiniteMagnitude, posAt + Date().timeIntervalSince(stampAt)) }

    // 10-23 leak pass: the old loop lived on a serial queue it never left, so every refreshQueue (q.async) queued up forever and
    // never ran; the playlist request also went out as id 8 while the reply was matched on 7. now: one named reader thread,
    // writes under a lock from any thread, a fixed request id for the playlist, and a reconnect that backs off 1 s → 10 s.
    private var fd: Int32 = -1                    // guarded by lock
    private let lock = NSLock()
    private var reqId = 100                       // guarded by lock; 7 is kept for the playlist
    private let wake = DispatchSemaphore(value: 0) // a nudge: try the socket now (a ytm command just ran, the window came back)
    private var _active = true
    private var queuePending = false              // main thread only: one playlist request per 500 ms
    private var titles: [String: Any] = [:]
    private var titlesStamp = Date.distantPast

    /// live: false for the headless snapshots, which must never knock on the real player's socket
    private var isLive = false
    init(live: Bool = true) { isLive = live; if live { connectLoop(); loadModes() } }

    // ---- moods, likes, shuffle: the stage asks the cli, which keeps the ledger and shapes what plays ----
    static let fallbackModes: [ModeInfo] = [("work", "core", ""), ("focus", "core", ""), ("coding", "work", ""), ("study", "work", ""), ("gym", "move", ""),
                                            ("drive", "move", ""), ("morning", "wind", ""), ("chill", "core", ""), ("energy", "core", ""), ("sleep", "core", "")]
        .map { ModeInfo(id: $0.0, group: $0.1, what: $0.2) }

    static func parseModes(_ out: String) -> [ModeInfo] {
        guard let a = out.firstIndex(of: "["), let b = out.lastIndex(of: "]"),
              let rows = try? JSONSerialization.jsonObject(with: Data(out[a...b].utf8)) as? [[String: Any]] else { return [] }
        return rows.compactMap { r in
            guard let n = r["name"] as? String else { return nil }
            return ModeInfo(id: n, group: r["group"] as? String ?? "core", what: r["what"] as? String ?? "")
        }
    }

    func loadModes() { Accounts.run(["mode", "--json"]) { out in let m = Player.parseModes(out); if !m.isEmpty { self.modes = m } } }

    /// the thumbs on the song playing. the heart flips at once and the cli confirms (it also thumbs the account when signed in)
    func like() { let on = rating == "LIKE"; rating = on ? "" : "LIKE"; ytm([on ? "unrate" : "like"]) { _ in self.readRating() } }
    func dislike() { let on = rating == "DISLIKE"; rating = on ? "" : "DISLIKE"; ytm([on ? "unrate" : "dislike"]) { _ in self.readRating() } }
    func shuffleQueue() { ytm(["shuffle"]) }

    /// this song's verdict from DATA/ratings.json, read off the main thread (the ledger can hold thousands)
    func readRating() {
        guard isLive else { return }
        let v = track.vid
        DispatchQueue.global(qos: .utility).async {
            var r = ""
            if !v.isEmpty, let d = try? Data(contentsOf: DATA.appendingPathComponent("ratings.json")),
               let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any], let e = j[v] as? [String: Any] { r = (e["r"] as? String) ?? "" }
            DispatchQueue.main.async { if self.track.vid == v { self.rating = r } }
        }
    }

    private var sockPath: String { DATA.appendingPathComponent("mpv.sock").path }

    /// false when the window and the menu bar panel are both closed: the app stops knocking on a missing socket until it's true
    /// again (a socket that's already open stays open). 0.8.2: and time-pos goes quiet. mpv sends it ~17 times a second, each a
    /// hop through the main thread, for a position nobody is looking at; it's interpolated anyway, and observed again on show
    /// (mpv answers an observe with the current value at once, so the line is right the moment it's seen)
    var active: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _active }
        set {
            lock.lock(); let was = _active; _active = newValue; lock.unlock()
            guard newValue != was else { return }
            if newValue { send(["observe_property", Player.timePosId, "time-pos"]); wake.signal() }
            else { send(["unobserve_property", Player.timePosId]) }
        }
    }
    static let timePosId = 2                      // its observe id in subscribe(): the order there is the contract

    private func connectLoop() {
        let t = Thread { [weak self] in
            var delay = 1.0
            while let self = self {
                if let s = self.open() { delay = 1; self.subscribe(); self.readLoop(s); continue }
                let woke = self.active ? self.wake.wait(timeout: .now() + delay) : self.wake.wait(timeout: .distantFuture)
                delay = woke == .success ? 1 : min(10, delay * 2)   // no player: 1, 2, 4, 8, 10, 10 … seconds between knocks
            }
        }
        t.name = "ytm.mpv"; t.qualityOfService = .utility; t.start()
    }

    private func open() -> Int32? {
        let s = socket(AF_UNIX, SOCK_STREAM, 0); guard s >= 0 else { return nil }
        var one: Int32 = 1   // a write to a socket mpv just closed must not SIGPIPE the whole app
        setsockopt(s, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout<Int32>.size))
        var addr = sockaddr_un(); addr.sun_family = sa_family_t(AF_UNIX)
        let path = sockPath
        guard path.utf8.count < MemoryLayout.size(ofValue: addr.sun_path) else { Darwin.close(s); return nil }
        withUnsafeMutablePointer(to: &addr.sun_path) { p in
            p.withMemoryRebound(to: CChar.self, capacity: 104) { strncpy($0, path, 103) }
        }
        let ok = withUnsafePointer(to: &addr) { p in
            p.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(s, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        if ok != 0 { Darwin.close(s); return nil }
        lock.lock(); fd = s; lock.unlock()
        DispatchQueue.main.async { self.connected = true; self.refreshQueue() }
        return s
    }

    private func subscribe() {
        let on = active
        for (i, p) in ["pause", "time-pos", "duration", "path", "volume", "playlist-pos", "idle-active", "media-title"].enumerated()
            where p != "time-pos" || on {      // a reconnect while nobody's looking leaves time-pos for the next show
            send(["observe_property", i + 1, p])
        }
    }

    func send(_ cmd: [Any], id: Int? = nil) {
        lock.lock(); defer { lock.unlock() }
        guard fd >= 0 else { return }
        var rid = id ?? 0
        if id == nil { reqId += 1; rid = reqId }
        guard let data = try? JSONSerialization.data(withJSONObject: ["command": cmd, "request_id": rid]) else { return }
        var line = data; line.append(0x0a)
        line.withUnsafeBytes { _ = Darwin.write(fd, $0.baseAddress, line.count) }
    }

    private func readLoop(_ s: Int32) {
        var buf = Data(); var chunk = [UInt8](repeating: 0, count: 65536)
        while true {
            let n = Darwin.read(s, &chunk, chunk.count)
            if n < 0 && errno == EINTR { continue }
            if n <= 0 { break }
            buf.append(chunk, count: n)
            var start = buf.startIndex
            while let nl = buf[start...].firstIndex(of: 0x0a) {
                if let m = try? JSONSerialization.jsonObject(with: buf.subdata(in: start..<nl)) as? [String: Any] { handle(m) }
                start = nl + 1
            }
            if start > buf.startIndex { buf = buf.subdata(in: start..<buf.endIndex) }   // one right-sized copy per read, not a shift per line
            if buf.count > 8 << 20 { buf = Data() }   // 8 MB with no newline isn't mpv talking: drop it rather than grow forever
        }
        lock.lock(); if fd == s { Darwin.close(s); fd = -1 }; lock.unlock()
        DispatchQueue.main.async { self.connected = false }
    }

    private func handle(_ m: [String: Any]) {
        if let ev = m["event"] as? String, ev == "property-change", let name = m["name"] as? String {
            let v = m["data"]
            DispatchQueue.main.async { self.apply(name, v) }
        } else if let rid = m["request_id"] as? Int, rid == 7, let pl = m["data"] as? [[String: Any]] {
            DispatchQueue.main.async { self.setQueue(pl) }
        } else if let rid = m["request_id"] as? Int, rid == 8 {
            let t = m["data"] as? Double
            DispatchQueue.main.async { let f = self.probeDone; self.probeDone = nil; f?(t) }
        }
    }

    private func apply(_ name: String, _ v: Any?) {
        switch name {
        case "pause": posAt = position; stampAt = Date(); paused = (v as? Bool) ?? true; readMode()
        case "time-pos": if let t = v as? Double { posAt = t; stampAt = Date() }
        case "duration": duration = (v as? Double) ?? 0
        case "volume": volume = (v as? Double) ?? volume
        case "path":
            // between two songs mpv reports no path at all: that's not a song, so the old one (and its cover) stays until the
            // next arrives. idle-active is the real "nothing playing"
            let vid = Player.vid((v as? String) ?? "")
            if !vid.isEmpty && vid != track.vid {
                track = lookup(vid); posAt = 0; stampAt = Date()
                // titles.json can land after the path (a radio burst rewrites it 25 times): one more look for the art in 3 s
                if track.art.isEmpty { DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self.artLate(vid) } }
            }
            refreshQueue(); readMode(); readRating()
        case "playlist-pos": refreshQueue()
        case "idle-active": if (v as? Bool) == true { track = Track(); rating = "" }; readMode()
        case "media-title":
            if track.title.isEmpty, let t = v as? String { track.title = t.components(separatedBy: " – ").last ?? t }
        default: break
        }
    }

    // the mode file used to be read on every property change, time-pos included (many a second); now only when it can matter
    private func readMode() {
        if let m = try? String(contentsOf: DATA.appendingPathComponent("mode"), encoding: .utf8) { mode = m.trimmingCharacters(in: .whitespacesAndNewlines) }
    }

    /// main thread only. a path change and a playlist-pos change arrive together, and a radio load brings a burst of both:
    /// they all fold into one playlist request, sent 500 ms after the first
    func refreshQueue() {
        guard !queuePending else { return }
        queuePending = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.queuePending = false; self.send(["get_property", "playlist"], id: 7) }
    }

    /// the 11-character id from a watch url, read as a query item (not "whatever follows the last v="), "" when there's none
    static func vid(_ path: String) -> String {
        let v = URLComponents(string: path)?.queryItems?.first { $0.name == "v" }?.value ?? ""
        return v.count == 11 ? v : ""
    }

    private func artLate(_ vid: String) {
        guard track.vid == vid, track.art.isEmpty else { return }
        let t = lookup(vid)
        if !t.art.isEmpty { track.art = t.art }   // the cover sees a new art url for the same song and fetches it once
    }

    private func setQueue(_ pl: [[String: Any]]) {
        loadTitles()
        queue = pl.enumerated().map { i, e in
            let f = (e["filename"] as? String) ?? ""
            let vid = Player.vid(f)
            var name = (e["title"] as? String) ?? vid
            if let d = titles[vid] as? [String: Any], let l = d["label"] as? String { name = l } else if let l = titles[vid] as? String { name = l }
            return QueueItem(id: i, title: name, current: (e["current"] as? Bool) ?? false)
        }
    }

    private func loadTitles() {
        let f = DATA.appendingPathComponent("titles.json")
        let m = (try? f.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
        if m > titlesStamp, let d = try? Data(contentsOf: f), let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] {
            titles = j; titlesStamp = m
        }
    }

    private func lookup(_ vid: String) -> Track {
        loadTitles()
        var t = Track(); t.vid = vid
        if let d = titles[vid] as? [String: Any] {
            t.title = d["title"] as? String ?? ""; t.artist = d["artist"] as? String ?? ""
            t.album = d["album"] as? String ?? ""; t.art = d["art"] as? String ?? ""
        } else if let l = titles[vid] as? String {
            let parts = l.components(separatedBy: " – "); t.artist = parts.first ?? ""; t.title = parts.last ?? l
        }
        return t
    }

    // ---- 0.8.2: waking from sleep. a stream that was mid-song when the lid closed can come back "playing" with nothing moving
    // (the connection died under it). after a wake: ask mpv where it is, again 10 s later; not paused, a song loaded, the same
    // second both times → one gentle nudge, a seek to where it already is (mpv reopens the stream there, nothing skips).
    // once per wake, never a loop: a newer wake or no answer ends it. ids: 8 is the probe, kept like 7 for the playlist

    private var wakeGen = 0                       // main only
    private var probeDone: ((Double?) -> Void)?   // main only: the one probe waiting for mpv's answer

    func didWake() {
        wakeGen += 1; let g = wakeGen
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) {   // let the network and the socket settle first
            guard g == self.wakeGen, self.connected else { return }
            self.probe { a in
                guard g == self.wakeGen, let a else { return }
                DispatchQueue.main.asyncAfter(deadline: .now() + 10) {
                    guard g == self.wakeGen else { return }
                    self.probe { b in
                        guard g == self.wakeGen, let b, Player.stuck(a, b, paused: self.paused, vid: self.track.vid) else { return }
                        self.wakeGen += 1                 // spent: this wake gets no second nudge
                        NSLog("ytm: woke stuck at %.1f s (10 s, no movement) · one seek in place", b)
                        self.status = "woke up stuck · nudged the stream"
                        self.seek(b)
                    }
                }
            }
        }
    }

    /// stuck = playing a song, and mpv's own clock sat still across the 10 s between the two looks
    static func stuck(_ a: Double, _ b: Double, paused: Bool, vid: String) -> Bool { !paused && !vid.isEmpty && abs(b - a) < 0.5 }

    /// one get_property time-pos; nil if mpv says nothing within 2 s (a dead socket)
    private func probe(_ done: @escaping (Double?) -> Void) {
        probeDone = done
        send(["get_property", "time-pos"], id: 8)
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { if let f = self.probeDone { self.probeDone = nil; f(nil) } }
    }

    // ---- controls ----
    func togglePause() { send(["cycle", "pause"]) }
    func next() { send(["playlist-next", "force"]); send(["set_property", "pause", false]) }
    func back() { send(["playlist-prev", "force"]); send(["set_property", "pause", false]) }
    func seek(_ t: Double) { send(["seek", t, "absolute"]); posAt = t; stampAt = Date() }
    func setVolume(_ v: Double) { send(["set_property", "volume", max(0, min(100, v))]) }
    func jump(_ i: Int) { send(["playlist-play-index", i]); send(["set_property", "pause", false]) }

    /// the cli does the thinking (search, radio, work mode, the log's intent); the stage just asks it.
    /// output is read before waiting (a chatty command can fill the 64 KB pipe and then both sides wait forever), the read end
    /// is closed after, and a command that hangs is stopped after `timeout` seconds
    func ytm(_ args: [String], timeout: Double = 300, done: ((String) -> Void)? = nil) {
        status = "…"
        DispatchQueue.global(qos: .userInitiated).async {
            let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            p.arguments = ["python3", YTM] + args
            var env = ProcessInfo.processInfo.environment
            env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (env["PATH"] ?? "")
            p.environment = env
            let out = Pipe(); p.standardOutput = out; p.standardError = out
            var s = "couldn't start bin/ytm"
            if (try? p.run()) != nil {
                let stop = DispatchWorkItem { if p.isRunning { p.terminate() } }
                DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: stop)
                let d = out.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit(); stop.cancel()
                s = String(decoding: d, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            }
            try? out.fileHandleForReading.close()   // Process already closed our copy of the write end at launch
            DispatchQueue.main.async { self.status = s.components(separatedBy: "\n").last ?? ""; self.readMode(); done?(s) }
            self.wake.signal()   // the command may have just started mpv: look for the socket now, not in up to 10 s
        }
    }
}
