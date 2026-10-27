// 0.8.2 search: three results, never more, never one (asked: "show only 2-3 search results, not much, not less": ten is a
// wall, one is no choice). enter asks `ytm search --json`, three cards drop under the field over the stage (an overlay: nothing
// below moves), a click or ↑↓ + enter plays one with `ytm play --vid`, esc lets go. the cli does the searching.
import SwiftUI
import AppKit

struct SearchHit: Identifiable, Equatable {
    let id: String                  // the videoId
    var title = "", artist = "", album = "", duration: Double = 0, art = ""

    /// `ytm search --json` → hits. the cli may print a warning first: the outermost [...] is the answer. rows without an id
    /// are dropped, and never more than three, whatever the cli sends
    static func parse(_ s: String) -> [SearchHit] {
        guard let a = s.firstIndex(of: "["), let b = s.lastIndex(of: "]"), a < b, let d = String(s[a...b]).data(using: .utf8),
              let rows = (try? JSONSerialization.jsonObject(with: d)) as? [[String: Any]] else { return [] }
        var seen = Set<String>()
        return Array(rows.compactMap { r -> SearchHit? in
            guard let v = r["videoId"] as? String, v.count == 11, seen.insert(v).inserted else { return nil }
            let dur = (r["duration"] as? Double) ?? (r["duration"] as? Int).map(Double.init) ?? 0
            return SearchHit(id: v, title: r["title"] as? String ?? "", artist: r["artist"] as? String ?? "",
                             album: r["album"] as? String ?? "", duration: dur, art: r["art"] as? String ?? "")
        }.prefix(SearchState.N))
    }

    /// "artist · 3:35"; an hour-long mix says 1:02:05, not 62:05
    var line: String {
        let d = Int(duration), len = d >= 3600 ? String(format: "%d:%02d:%02d", d / 3600, d / 60 % 60, d % 60) : d > 0 ? clock(duration) : ""
        return [artist, len].filter { !$0.isEmpty }.joined(separator: " · ")
    }
}

/// one search box's results (the stage has one, the menu-bar panel another). main thread, except the process itself
final class SearchState: ObservableObject {
    static let N = 3                                // bin/ytm's SEARCH_N: the one number the whole thing agrees on
    @Published private(set) var hits: [SearchHit] = []
    @Published private(set) var loading = false
    @Published private(set) var note = ""
    @Published var sel = 0
    @Published private(set) var thumbs: [String: NSImage] = [:]   // only the shown hits' covers: three small images at most
    private(set) var asked = ""                     // the words the hits answer: enter on the same words plays, new words search

    private let lock = NSLock()
    private var proc: Process?                      // guarded by lock: the one request in flight
    private var want = 0                            // guarded by lock: the newest request; an older one is killed on sight
    private var gen = 0                             // main only

    var open: Bool { loading || !hits.isEmpty || !note.isEmpty }

    /// headless: show these as if the cli had answered, no process
    func fixture(_ h: [SearchHit], for q: String, thumbs t: [String: NSImage]) { hits = Array(h.prefix(SearchState.N)); asked = q; thumbs = t; sel = 0 }

    func run(_ words: String) {
        let q = words.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else { close(); return }
        guard SearchState.cliReady() else { stop(); hits = []; loading = false; note = "needs a newer bin/ytm (ytm search --json)"; return }
        stop(); gen += 1
        let g = gen
        lock.lock(); want = g; lock.unlock()
        loading = true; note = ""; asked = q; hits = []; thumbs = [:]; sel = 0
        let args = ["search", "--json"] + q.split(whereSeparator: \.isWhitespace).map(String.init)
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            p.arguments = ["python3", YTM] + args
            var env = ProcessInfo.processInfo.environment
            env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (env["PATH"] ?? "")
            p.environment = env
            let out = Pipe(); p.standardOutput = out; p.standardError = FileHandle.nullDevice
            var s = "", started = false
            if (try? p.run()) != nil {
                started = true
                // a newer query got in while this one was starting: it goes now, before it costs a network round trip
                self.lock.lock(); if self.want == g { self.proc = p } else { p.terminate() }; self.lock.unlock()
                let kill = DispatchWorkItem { if p.isRunning { p.terminate() } }   // 15 s and it's gone: a stuck search holds nothing
                DispatchQueue.global().asyncAfter(deadline: .now() + 15, execute: kill)
                let d = out.fileHandleForReading.readDataToEndOfFile()   // read before waiting: a full pipe never deadlocks
                p.waitUntilExit(); kill.cancel()
                s = String(decoding: d, as: UTF8.self)
                self.lock.lock(); if self.proc === p { self.proc = nil }; self.lock.unlock()
            }
            try? out.fileHandleForReading.close()
            let found = SearchHit.parse(s), slow = started && p.terminationReason == .uncaughtSignal
            DispatchQueue.main.async {
                guard g == self.gen else { return }    // a newer query (or esc) owns the box now
                self.loading = false; self.hits = found; self.sel = 0
                self.note = !found.isEmpty ? "" : !started ? "couldn't start bin/ytm" : slow ? "search took too long · try again" : "nothing for ‘\(q)’"
                self.loadThumbs(found, g)
            }
        }
    }

    /// esc, a play, or the glass going away: the box is empty again and any request still running is stopped
    func close() { stop(); gen += 1; loading = false; hits = []; note = ""; thumbs = [:]; sel = 0; asked = "" }

    private func stop() {
        lock.lock(); let p = proc; proc = nil; want = -1; lock.unlock()
        if let p, p.isRunning { p.terminate() }
    }

    /// ↑↓: true when there were results to move through
    func move(_ d: Int) -> Bool {
        guard !hits.isEmpty else { return false }
        sel = (sel + d + hits.count) % hits.count; return true
    }

    var chosen: SearchHit? { hits.indices.contains(sel) ? hits[sel] : nil }

    /// the shared cover cache first (read only: a 226 px stand-in written there would become the stage's cover), else the
    /// hit's own url, one try each, 10 s. never kept past the next search
    private func loadThumbs(_ hs: [SearchHit], _ g: Int) {
        for h in hs {
            DispatchQueue.global(qos: .utility).async { [weak self] in
                let f = DATA.appendingPathComponent("art/\(h.id).jpg")
                let img = NSImage(contentsOf: f) ?? (h.art.isEmpty ? nil : URL(string: h.art).flatMap(Cover.fetch).flatMap(NSImage.init(data:)))
                guard let img else { return }
                DispatchQueue.main.async { if let self, self.gen == g { self.thumbs[h.id] = img } }
            }
        }
    }

    /// the app may be newer than the cli it drives: an old bin/ytm would read `search --json words` as words to play.
    /// SEARCH_N is the python side's marker. read again only when the file changes
    private static var readyStamp: Date?, ready = false
    static func cliReady() -> Bool {
        let m = (try? URL(fileURLWithPath: YTM).resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
        if let m, m == readyStamp { return ready }
        readyStamp = m
        ready = ((try? String(contentsOfFile: YTM, encoding: .utf8)) ?? "").contains("SEARCH_N")
        return ready
    }

    /// what plays a hit: `ytm play --vid` (the song, then its radio, the same as play), or on an older cli the words
    static func playArgs(_ h: SearchHit) -> [String] { cliReady() ? ["play", "--vid", h.id] : ["play", "\(h.title) \(h.artist)"] }
}

/// the three cards. `stage`: the dark glass over the backdrop; else the menu-bar panel's compact rows (light or dark)
struct SearchResults: View {
    @ObservedObject var search: SearchState
    var stage = true
    let play: (SearchHit) -> Void
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        VStack(alignment: .leading, spacing: stage ? 4 : 2) {
            if search.loading {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small).scaleEffect(stage ? 0.8 : 0.7).frame(width: 16, height: 16)
                    Text("searching ‘\(search.asked)’…").lineLimit(1).truncationMode(.middle)
                }.font(.system(size: stage ? 13 : 11, design: .rounded)).foregroundStyle(fg.opacity(0.6)).padding(stage ? 10 : 6)
            }
            ForEach(Array(search.hits.enumerated()), id: \.element.id) { i, h in
                Button { play(h) } label: { row(h, on: i == search.sel) }
                    .buttonStyle(.plain).onHover { if $0 { search.sel = i } }.help("\(h.title) · \(h.line)")
            }
            if !search.note.isEmpty {
                Text(search.note).font(.system(size: stage ? 13 : 11, design: .rounded)).foregroundStyle(fg.opacity(0.55))
                    .lineLimit(1).truncationMode(.middle).padding(stage ? 10 : 6)
            }
        }
        .padding(stage ? 6 : 4)
        .frame(width: stage ? 400 : nil, alignment: .leading).frame(maxWidth: stage ? nil : .infinity, alignment: .leading)
        // solid, not glass: the 42 pt title sits right under the cards (the settings panel learned this 10-23)
        .background(bg, in: RoundedRectangle(cornerRadius: stage ? 18 : 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: stage ? 18 : 10, style: .continuous).strokeBorder(fg.opacity(stage ? 0.1 : 0.12), lineWidth: 1))
        .shadow(color: .black.opacity(stage ? 0.45 : 0.25), radius: stage ? 24 : 10, y: stage ? 10 : 4)
    }

    var dark: Bool { stage || scheme == .dark }
    var fg: Color { dark ? .white : .black }
    var bg: Color { stage ? Color(red: 0.035, green: 0.04, blue: 0.075).opacity(0.97) : dark ? Color(white: 0.17) : Color(white: 0.975) }

    func row(_ h: SearchHit, on: Bool) -> some View {
        let side: CGFloat = stage ? 46 : 30
        return HStack(spacing: stage ? 12 : 9) {
            Group {
                if let img = search.thumbs[h.id] { Image(nsImage: img).resizable().interpolation(.medium).scaledToFill() }
                else { ZStack { fg.opacity(0.08); Image(systemName: "music.note").font(.system(size: side * 0.36)).foregroundStyle(fg.opacity(0.35)) } }
            }
            .frame(width: side, height: side).clipShape(RoundedRectangle(cornerRadius: stage ? 9 : 6, style: .continuous))
            VStack(alignment: .leading, spacing: stage ? 3 : 1) {
                Text(h.title.isEmpty ? h.id : h.title).font(.system(size: stage ? 14 : 12, weight: .semibold, design: .rounded))
                    .foregroundStyle(fg.opacity(0.95)).lineLimit(1)
                Text(h.line).font(.system(size: stage ? 12 : 10.5, design: .rounded)).foregroundStyle(fg.opacity(0.58)).lineLimit(1)
            }
            Spacer(minLength: 0)
            Image(systemName: "return").font(.system(size: stage ? 11 : 9, weight: .semibold)).foregroundStyle(fg.opacity(on ? 0.5 : 0))
        }
        .padding(stage ? 8 : 5)
        .background(fg.opacity(on ? (stage ? 0.12 : 0.1) : 0), in: RoundedRectangle(cornerRadius: stage ? 12 : 7, style: .continuous))
        .contentShape(Rectangle())
    }
}

/// ↑↓ and esc for a search box, seen before the text field eats them (the field editor uses ↑↓ for the caret). only while
/// there are results, or esc while the box has focus; every other key goes on as it was
struct SearchKeys: NSViewRepresentable {
    let search: SearchState
    let play: (SearchHit) -> Void
    let dismiss: () -> Void
    func makeNSView(context: Context) -> NSView {
        let v = NSView(), c = context.coordinator
        c.monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak v, weak c] e in
            guard let v, let c, e.window === v.window, !e.modifierFlags.contains(.command) else { return e }
            let typing = e.window?.firstResponder is NSTextView
            switch e.keyCode {
            case 125: return c.keys?.search.move(1) == true ? nil : e        // ↓
            case 126: return c.keys?.search.move(-1) == true ? nil : e       // ↑
            case 53: if c.keys?.search.open == true || typing { c.keys?.dismiss(); e.window?.makeFirstResponder(nil); return nil }; return e   // esc
            case 36, 76:                                                     // return: the field's own enter decides while typing
                if !typing, let k = c.keys, let h = k.search.chosen { k.play(h); return nil }; return e
            default: return e
            }
        }
        c.keys = self
        return v
    }
    func updateNSView(_ v: NSView, context: Context) { context.coordinator.keys = self }   // fresh closures every update
    static func dismantleNSView(_ v: NSView, coordinator: C) { if let m = coordinator.monitor { NSEvent.removeMonitor(m) }; coordinator.monitor = nil }
    func makeCoordinator() -> C { C() }
    final class C { var monitor: Any?; var keys: SearchKeys? }
}
