// settings: everything is remembered and applied live. the look, accent, motion and moments live in DATA/prefs.json (shared
// with the cli and the terminal ui); the rest is this mac's (UserDefaults).
import SwiftUI
import Foundation
import Darwin
import ServiceManagement

final class StageSettings: ObservableObject {
    let d = UserDefaults.standard
    @Published var profile: String { didSet { d.set(profile, forKey: "profile"); applyProfile() } }
    @Published var fps: Double { didSet { d.set(fps, forKey: "fps") } }
    @Published var texture: Bool { didSet { d.set(texture, forKey: "texture") } }
    @Published var glow: Double { didSet { d.set(glow, forKey: "glow") } }
    @Published var clock: Bool { didSet { d.set(clock, forKey: "clock") } }
    @Published var hideAfter: Double { didSet { d.set(hideAfter, forKey: "hideAfter") } }
    @Published var autoBattery: Bool { didSet { d.set(autoBattery, forKey: "autoBattery") } }
    @Published var open = false

    // 0.8: the frame rate is now the window server's, not a draw loop's, so the numbers can be honest motion rates
    static let profiles: [(String, String, Double)] = [("battery", "battery · 8 fps", 8),
        ("balanced", "balanced · 20 fps, the default", 20), ("smooth", "smooth · 30 fps", 30), ("ultra", "ultra · 60 fps", 60)]

    init() {
        d.register(defaults: ["profile": "balanced", "fps": 20.0, "texture": true, "glow": 1.0, "clock": true, "hideAfter": 4.0, "autoBattery": true])
        if d.integer(forKey: "settingsV") < 8 {   // 0.7's numbers meant a shader's frame rate: start 0.8 on its own default
            for k in ["fps", "profile", "intensity", "particles", "bursts", "horizon", "scene"] { d.removeObject(forKey: k) }
            d.set(8, forKey: "settingsV")
        }
        profile = d.string(forKey: "profile") ?? "balanced"; fps = d.double(forKey: "fps"); texture = d.bool(forKey: "texture")
        glow = d.double(forKey: "glow"); clock = d.bool(forKey: "clock"); hideAfter = d.double(forKey: "hideAfter")
        autoBattery = d.bool(forKey: "autoBattery")
    }

    func applyProfile() { fps = StageSettings.profiles.first { $0.0 == profile }?.2 ?? 20 }

    /// "ytm 0.8.0": the bundle's version (build.sh stamps it from VERSION), else VERSION itself
    static let version: String = {
        if let v = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String, !v.isEmpty { return v }
        let f = (ROOT ?? HOME).appendingPathComponent("VERSION")
        return (try? String(contentsOf: f, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "?"
    }()
}

// ---- prefs: DATA/prefs.json, one file the app, the terminal ui and `ytm look` all share ----
// {"v":1,"look":"auto|dusk|rain|neon|stars|paper","accent":"cover|night|ember|sea|mono","motion":"still|calm|lively","moments":true}
// writers merge (only the keys they change, unknown keys kept) and write atomically; a missing or garbled file is the defaults.

final class Prefs: ObservableObject {
    static let motions = ["still", "calm", "lively"]
    @Published private(set) var look = "auto"    // 0.8.1: the song picks unless someone chose (an 0.8.0 reader sees dusk)
    @Published private(set) var accent = "cover"
    @Published private(set) var motion = "calm"
    @Published private(set) var moments = true
    private var file: URL { DATA.appendingPathComponent("prefs.json") }
    private var stamp: Date?
    private var watch: DispatchSourceFileSystemObject?

    init(watching: Bool = true) { read(); if watching { startWatch() } }
    deinit { watch?.cancel() }

    var lookCase: Look { Look(id: look) }

    func read() {
        let m = (try? file.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
        if m != nil && m == stamp { return }
        stamp = m
        let j = (try? Data(contentsOf: file)).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        let l = (j["look"] as? String).flatMap { Look.ids.contains($0) || $0 == "auto" ? $0 : nil } ?? "auto"
        let a = (j["accent"] as? String).flatMap { Accent.ids.contains($0) ? $0 : nil } ?? "cover"
        let mo = (j["motion"] as? String).flatMap { Prefs.motions.contains($0) ? $0 : nil } ?? "calm"
        let mm = (j["moments"] as? Bool) ?? true
        if l != look { look = l }; if a != accent { accent = a }; if mo != motion { motion = mo }; if mm != moments { moments = mm }
    }

    /// headless snapshots: show a look without writing anyone's prefs
    func preview(look l: String?, accent a: String?) { if let l { look = l }; if let a { accent = a } }

    func set(look v: String) { if v != look { look = v; write(["look": v]) } }
    func set(accent v: String) { if v != accent { accent = v; write(["accent": v]) } }
    func set(motion v: String) { if v != motion { motion = v; write(["motion": v]) } }
    func set(moments v: Bool) { if v != moments { moments = v; write(["moments": v]) } }

    /// read-merge-write through a temp file + rename: a reader never sees half a file, and keys we don't know survive
    private func write(_ kv: [String: Any]) {
        let f = file
        var j = (try? Data(contentsOf: f)).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        j["v"] = 1; for (k, v) in kv { j[k] = v }
        guard let d = try? JSONSerialization.data(withJSONObject: j, options: [.sortedKeys]) else { return }
        try? FileManager.default.createDirectory(at: DATA, withIntermediateDirectories: true)
        let tmp = DATA.appendingPathComponent(".prefs.json.\(getpid()).tmp")
        guard (try? d.write(to: tmp)) != nil else { return }
        if rename(tmp.path, f.path) != 0 { try? FileManager.default.removeItem(at: tmp) }
        stamp = (try? f.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
        if watch == nil { startWatch() }
    }

    /// the DATA folder changes when anyone renames a new prefs.json into it: the kernel tells us, nothing polls
    private func startWatch() {
        let fd = Darwin.open(DATA.path, O_EVTONLY)
        guard fd >= 0 else { return }
        let src = DispatchSource.makeFileSystemObjectSource(fileDescriptor: fd, eventMask: [.write, .rename, .delete], queue: .main)
        src.setEventHandler { [weak self] in self?.read() }
        src.setCancelHandler { Darwin.close(fd) }
        src.resume(); watch = src
    }
}

// ---- open at login (0.8.2): the system's own login item for this app (SMAppService.mainApp, macOS 13+). the switch shows what
// the system says, read fresh each time the panel opens; flipping it is the only thing that registers. a headless snapshot
// never touches it (live: false), and nothing here runs at launch ----

final class LoginItem: ObservableObject {
    @Published private(set) var on = false
    @Published private(set) var note = ""
    @Published private(set) var known = false

    func refresh() { show(SMAppService.mainApp.status) }

    func set(_ want: Bool) {
        let svc = SMAppService.mainApp
        do { if want { try svc.register() } else { try svc.unregister() }; note = "" }
        catch { note = (want ? "couldn't add it: " : "couldn't remove it: ") + error.localizedDescription }
        show(svc.status)
    }

    private func show(_ st: SMAppService.Status) {
        known = true
        on = st == .enabled || st == .requiresApproval
        switch st {
        case .requiresApproval: note = "waiting for you in system settings › general › login items"
        case .notFound: note = "only the installed ytm.app can open at login"
        default: if note.hasPrefix("waiting") || note.hasPrefix("only") { note = "" }
        }
    }

    static func approve() { SMAppService.openSystemSettingsLoginItems() }
}

// ---- accounts: the cli owns the logins (keychain, cookies); the panel only asks and shows ----

struct AccountStatus: Equatable { var loggedIn = false, source = "", account: String? = nil }

final class Accounts: ObservableObject {
    @Published var ytmusic = AccountStatus()
    @Published var spotify = AccountStatus()
    @Published var known = false            // false until one status read came back clean
    @Published var busy: String? = nil      // "ytmusic" / "spotify" while a connect or disconnect runs
    @Published var note = ""
    private var asking = false

    static let services: [(key: String, name: String, icon: String, color: Color)] = [
        ("ytmusic", "youtube music", "play.rectangle.fill", .red), ("spotify", "spotify", "waveform.circle.fill", .green)]

    func status(_ key: String) -> AccountStatus { key == "spotify" ? spotify : ytmusic }

    /// quiet read: runs `ytm auth status --json` itself, so the json never lands in the player's status line
    func refresh() {
        guard !asking else { return }
        asking = true
        Accounts.run(["auth", "status", "--json"]) { out in
            self.asking = false
            guard let j = Accounts.json(out) else { if !self.known { self.note = "can't read the login status yet (needs a newer bin/ytm)." }; return }
            self.ytmusic = Accounts.parse(j["ytmusic"]); self.spotify = Accounts.parse(j["spotify"])
            self.known = true; if self.busy == nil { self.note = "" }
        }
    }

    // youtube music: pick the account first. the list is each browser profile by
    // the name you gave it, from `ytm login --list --json` (no cookies read, no keychain): picking one logs in from there.
    struct Choice: Identifiable { let id: String; let name: String; let detail: String; let signedIn: Bool }
    @Published var choices: [Choice] = []
    @Published var choosing = false

    func connect(_ key: String, _ player: Player?) {
        if key == "spotify" { return act(key, ["spotify", "login"], player) }
        note = "finding your browser profiles…"
        Accounts.run(["login", "--list", "--json"]) { out in
            guard let a = out.firstIndex(of: "["), let b = out.lastIndex(of: "]"),
                  let rows = try? JSONSerialization.jsonObject(with: Data(out[a...b].utf8)) as? [[String: Any]] else {
                self.note = "couldn't list the browser profiles (needs a newer bin/ytm)"; return
            }
            self.choices = rows.compactMap { r in
                guard let src = r["source"] as? String else { return nil }
                let emails = (r["accounts"] as? [String]) ?? []
                return Choice(id: src, name: r["name"] as? String ?? src,
                              detail: ((r["browser"] as? String) ?? "") + (emails.isEmpty ? "" : " · " + emails.joined(separator: ", ")),
                              signedIn: (r["signed_in"] as? Bool) ?? false)
            }
            self.note = self.choices.contains { $0.signedIn } ? "pick the profile with your music account" : "no browser is signed in to youtube music yet"
            self.choosing = true
        }
    }

    func choose(_ c: Choice, _ player: Player?) {
        choosing = false
        act("ytmusic", ["login", c.id], player)
        note = "macOS will ask for \(c.detail.components(separatedBy: " ").first ?? "the browser")'s key: press Allow, not Always Allow"
    }

    func signInElsewhere() {
        choosing = false
        NSWorkspace.shared.open(URL(string: "https://music.youtube.com")!)
        note = "sign in on youtube music in the browser, then press connect again"
    }
    func disconnect(_ key: String, _ player: Player?) { act(key, key == "spotify" ? ["spotify", "logout"] : ["logout"], player) }

    private func act(_ key: String, _ args: [String], _ player: Player?) {
        guard busy == nil else { return }
        busy = key; note = ""
        let done: (String) -> Void = { out in
            self.busy = nil
            self.note = out.components(separatedBy: "\n").last(where: { !$0.isEmpty }) ?? ""
            self.refresh()
        }
        if let p = player { p.ytm(args, done: done) } else { Accounts.run(args, timeout: 300, done) }
    }

    static func parse(_ v: Any?) -> AccountStatus {
        guard let d = v as? [String: Any] else { return AccountStatus() }
        let a = (d["account"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        return AccountStatus(loggedIn: (d["logged_in"] as? Bool) ?? false, source: (d["source"] as? String) ?? "", account: a)
    }

    /// the cli may print a warning before the json: take the outermost {...}
    static func json(_ s: String) -> [String: Any]? {
        guard let a = s.firstIndex(of: "{"), let b = s.lastIndex(of: "}"), a < b,
              let d = String(s[a...b]).data(using: .utf8) else { return nil }
        return (try? JSONSerialization.jsonObject(with: d)) as? [String: Any]
    }

    /// one bin/ytm call off the main thread, killed if it hangs. stdout only; stderr is dropped.
    static func run(_ args: [String], timeout: Double = 20, _ done: @escaping (String) -> Void) {
        DispatchQueue.global(qos: .utility).async {
            let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            p.arguments = ["python3", YTM] + args
            var env = ProcessInfo.processInfo.environment
            env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (env["PATH"] ?? "")
            p.environment = env
            let out = Pipe(); p.standardOutput = out; p.standardError = FileHandle.nullDevice
            guard (try? p.run()) != nil else { DispatchQueue.main.async { done("") }; return }
            let kill = DispatchWorkItem { if p.isRunning { p.terminate() } }
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: kill)
            let data = out.fileHandleForReading.readDataToEndOfFile()
            p.waitUntilExit(); kill.cancel()
            let s = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            DispatchQueue.main.async { done(s) }
        }
    }
}

// ---- battery + heat: what this app itself costs, sampled every 5 s and only while the panel is open ----

enum SelfUsage {
    /// user + system cpu seconds this process has burned since launch
    static func cpuSeconds() -> Double {
        var r = rusage(); getrusage(RUSAGE_SELF, &r)
        return Double(r.ru_utime.tv_sec + r.ru_stime.tv_sec) + Double(r.ru_utime.tv_usec + r.ru_stime.tv_usec) / 1e6
    }

    /// physical footprint in MB: the number activity monitor shows as "memory"
    static func footprintMB() -> Double {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
        let kr = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count) }
        }
        return kr == KERN_SUCCESS ? Double(info.phys_footprint) / 1_048_576 : 0
    }

    /// the highest footprint this process has reached (the kernel's own ledger)
    static func peakMB() -> Double {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
        let kr = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count) }
        }
        return kr == KERN_SUCCESS ? Double(info.ledger_phys_footprint_peak) / 1_048_576 : 0
    }

    static func heat(_ t: ProcessInfo.ThermalState) -> (String, Color) {
        switch t {
        case .nominal: return ("cool", .green)
        case .fair: return ("warm", .yellow)
        case .serious: return ("hot, easing off", .orange)
        case .critical: return ("too hot", .red)
        @unknown default: return ("?", .gray)
        }
    }
}

final class SelfMeter: ObservableObject {
    @Published var cpu: Double? = nil       // percent of one core, nil until the first 5 s window closes
    @Published var mem = 0.0
    @Published var thermal = ProcessInfo.processInfo.thermalState
    @Published var battery = false
    private var timer: Timer?
    private var lastCPU = 0.0, lastAt = Date()

    func start() {
        guard timer == nil else { return }
        lastCPU = SelfUsage.cpuSeconds(); lastAt = Date(); cpu = nil; sample()
        let t = Timer(timeInterval: 5, repeats: true) { [weak self] _ in self?.sample() }
        t.tolerance = 1; RunLoop.main.add(t, forMode: .common); timer = t
    }

    func stop() { timer?.invalidate(); timer = nil }
    deinit { timer?.invalidate() }

    private func sample() {
        let c = SelfUsage.cpuSeconds(), now = Date(), dt = now.timeIntervalSince(lastAt)
        if dt > 0.5 { cpu = max(0, (c - lastCPU) / dt * 100) }
        lastCPU = c; lastAt = now
        mem = SelfUsage.footprintMB(); thermal = ProcessInfo.processInfo.thermalState; battery = Power.onBattery
    }
}

struct SettingsPanel: View {
    @ObservedObject var s: StageSettings
    @ObservedObject var prefs: Prefs
    @ObservedObject var auto: AutoPick
    @ObservedObject var cover: Cover
    var player: Player? = nil                // connect / disconnect go through the player's ytm(...) when given
    var live = true                          // false in a headless snapshot: never ask bin/ytm (or the keychain) anything
    @StateObject var accounts = Accounts()
    @StateObject var meter = SelfMeter()
    @StateObject var login = LoginItem()

    var body: some View {
        // fits → plain; too tall for the window → the same content scrolls inside the panel (indicator shown, so the
        // accounts at the bottom are never just cut off)
        ViewThatFits(in: .vertical) {
            content
            ScrollView(.vertical, showsIndicators: true) { content }.scrollIndicators(.visible)
        }
        .toggleStyle(.switch).font(.system(size: 13, design: .rounded)).foregroundStyle(.white)
        .frame(width: 380)
        // opaque: the title and the up-next list sit right behind it, and at 0.96 the 42 pt title still ghosted through (10-23)
        .background(Color(red: 0.035, green: 0.04, blue: 0.075), in: RoundedRectangle(cornerRadius: 26, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 26, style: .continuous).strokeBorder(.white.opacity(0.09), lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 26, style: .continuous))
        .shadow(color: .black.opacity(0.45), radius: 30)
        .padding(.bottom, 24)
        .onAppear { meter.start(); if live { accounts.refresh(); login.refresh() } else { accounts.known = true } }   // headless: draw the rows, ask nothing
        .onDisappear { meter.stop() }
    }

    var content: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("settings").font(.system(size: 20, weight: .semibold, design: .rounded))
                Spacer()
                Button { withAnimation(.easeOut(duration: 0.25)) { s.open = false } } label: { Image(systemName: "xmark").font(.system(size: 13, weight: .bold)) }.buttonStyle(.plain)
            }
            section("the look", "sparkles") {
                Picker("", selection: Binding(get: { prefs.look }, set: { prefs.set(look: $0) })) {
                    Image(systemName: "wand.and.stars").help("auto · the song picks").tag("auto")
                    ForEach(Look.allCases, id: \.self) { Image(systemName: $0.icon).help($0.name).tag($0.id) }
                }.pickerStyle(.segmented).labelsHidden()
                // auto says what it picked and why ("auto · neon drive · 129 bpm, heavy bass"), the tooltip too
                Text((prefs.look == "auto" ? "auto · " + auto.pick.reason : prefs.lookCase.name) + (player?.mode == "work" && prefs.look == "auto" ? " · work mode shows paper" : ""))
                    .font(.system(size: 11)).opacity(0.55).lineLimit(2).fixedSize(horizontal: false, vertical: true)
                    .help(prefs.look == "auto" ? auto.pick.reason : prefs.lookCase.name)
                swatches
                Picker("", selection: Binding(get: { prefs.motion }, set: { prefs.set(motion: $0) })) {
                    ForEach(Prefs.motions, id: \.self) { Text($0).tag($0) }
                }.pickerStyle(.segmented).labelsHidden()
                toggle("react to drops", Binding(get: { prefs.moments }, set: { prefs.set(moments: $0) }))
                toggle("texture · dust, rain, stars, grid", $s.texture)
                slider("glow", $s.glow, 0.3...1.6, String(format: "%.1f×", s.glow))
            }
            section("performance", "gauge.with.dots.needle.33percent") {
                Picker("", selection: $s.profile) { ForEach(StageSettings.profiles, id: \.0) { Text($0.0).tag($0.0) } }.pickerStyle(.segmented).labelsHidden()
                slider("frame rate", $s.fps, 4...60, "\(Int(s.fps)) fps")
                toggle("battery saver when unplugged", $s.autoBattery)
                usage
            }
            section("interface", "rectangle.on.rectangle") {
                slider("hide glass after", $s.hideAfter, 2...30, "\(Int(s.hideAfter)) s")
                toggle("clock + date", $s.clock)
                toggle("open at login", Binding(get: { login.on }, set: { v in if live { login.set(v) } })).disabled(!live || !login.known)
                if !login.note.isEmpty {
                    Button { if login.note.hasPrefix("waiting") { LoginItem.approve() } } label: {
                        Text(login.note).font(.system(size: 11)).opacity(0.55).lineLimit(2).multilineTextAlignment(.leading).fixedSize(horizontal: false, vertical: true)
                    }.buttonStyle(.plain)
                }
            }
            section("accounts", "person.crop.circle") {
                ForEach(Accounts.services, id: \.key) { svc in
                    account(svc.key, svc.name, svc.icon, svc.color)
                    if svc.key == "ytmusic" { chooser }
                }
                if !accounts.note.isEmpty {
                    Text(accounts.note).font(.system(size: 11)).opacity(0.55).lineLimit(2).truncationMode(.tail)
                }
                Text("better picks, smarter work mode, real reports. kept only in this mac's keychain.")
                    .font(.system(size: 11)).opacity(0.45).fixedSize(horizontal: false, vertical: true)
            }
            Text("ytm \(StageSettings.version)").font(.system(size: 11, design: .monospaced)).opacity(0.35)
                .frame(maxWidth: .infinity, alignment: .center)
        }
        .padding(22)
    }

    /// label left, switch right, one row: a bare Toggle in a leading stack drew its switch over a long label (10-23)
    func toggle(_ name: String, _ on: Binding<Bool>) -> some View {
        HStack(spacing: 12) {
            Text(name).lineLimit(1)
            Spacer(minLength: 8)
            Toggle(name, isOn: on).labelsHidden().controlSize(.small)
        }.frame(maxWidth: .infinity)
    }

    /// the accent: the cover's own colours, or one of four fixed palettes. three dots each, the chosen one ringed
    var swatches: some View {
        HStack(spacing: 8) {
            ForEach(Accent.ids, id: \.self) { id in
                let pal = Accent.palette(id, cover: cover.palette), on = prefs.accent == id
                Button { prefs.set(accent: id) } label: {
                    VStack(spacing: 5) {
                        HStack(spacing: -5) {
                            ForEach(0..<3, id: \.self) { i in
                                Circle().fill(Color(red: Double(pal[i].x), green: Double(pal[i].y), blue: Double(pal[i].z)))
                                    .frame(width: 15, height: 15).overlay(Circle().strokeBorder(.black.opacity(0.25), lineWidth: 1))
                            }
                        }
                        Text(id).font(.system(size: 10, weight: .medium, design: .rounded)).opacity(on ? 0.95 : 0.55).lineLimit(1)
                    }
                    .frame(maxWidth: .infinity).padding(.vertical, 6)
                    .background(Color.white.opacity(on ? 0.14 : 0.04), in: RoundedRectangle(cornerRadius: 9, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 9, style: .continuous).strokeBorder(.white.opacity(on ? 0.35 : 0), lineWidth: 1))
                    .contentShape(Rectangle())
                }.buttonStyle(.plain).help(id == "cover" ? "the cover's own colours" : id)
            }
        }
    }

    /// what this app costs, sampled every 5 s and only while the panel is open
    var usage: some View {
        let (heat, tint) = SelfUsage.heat(meter.thermal)
        return HStack(spacing: 6) {
            Circle().fill(tint).frame(width: 7, height: 7)
            Text("mac is \(heat)").lineLimit(1)
            Spacer(minLength: 6)
            Text("\(meter.cpu.map { String(format: "%.0f%%", $0) } ?? "…") cpu · \(String(format: "%.0f", meter.mem)) MB · \(meter.battery ? "battery" : "plugged in")")
                .font(.system(size: 11, design: .monospaced)).opacity(0.6).lineLimit(1)
        }.help("this app's own cpu and memory, checked every 5 s while this panel is open")
    }

    func account(_ key: String, _ name: String, _ icon: String, _ color: Color) -> some View {
        let st = accounts.status(key), busy = accounts.busy == key
        let detail = !accounts.known ? "checking…" : st.loggedIn ? (st.account ?? "connected") + (st.source.isEmpty ? "" : " · \(st.source)") : "not connected"
        return HStack(spacing: 8) {
            Image(systemName: icon).foregroundStyle(color)
            VStack(alignment: .leading, spacing: 1) {
                Text(name)
                Text(busy ? (st.loggedIn ? "disconnecting…" : "connecting… finish in the browser") : detail)
                    .font(.system(size: 11)).opacity(0.5).lineLimit(1).truncationMode(.middle)
            }
            Spacer(minLength: 6)
            if busy { ProgressView().controlSize(.small) }
            else if accounts.known && !(key == "ytmusic" && accounts.choosing) {
                Button(st.loggedIn ? "disconnect" : "connect") {
                    if st.loggedIn { accounts.disconnect(key, player) } else { accounts.connect(key, player) }
                }
                .buttonStyle(.plain).font(.system(size: 12, weight: .medium, design: .rounded))
                .padding(.horizontal, 10).padding(.vertical, 5)
                .background(Color.white.opacity(st.loggedIn ? 0.08 : 0.16), in: Capsule())
                .disabled(accounts.busy != nil)
            }
        }
    }

    /// the account chooser, under the youtube music row while it's open
    @ViewBuilder var chooser: some View {
        if accounts.choosing {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(accounts.choices.filter(\.signedIn)) { c in
                    Button { accounts.choose(c, player) } label: {
                        HStack(spacing: 8) {
                            Image(systemName: "person.crop.circle").opacity(0.7)
                            VStack(alignment: .leading, spacing: 0) {
                                Text(c.name).font(.system(size: 12, weight: .medium, design: .rounded))
                                Text(c.detail).font(.system(size: 10)).opacity(0.5).lineLimit(1).truncationMode(.middle)
                            }
                            Spacer(minLength: 0)
                        }
                        .padding(.horizontal, 10).padding(.vertical, 6)
                        .background(Color.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 9, style: .continuous)).contentShape(Rectangle())
                    }.buttonStyle(.plain)
                }
                HStack {
                    Button("another account: sign in in the browser") { accounts.signInElsewhere() }.buttonStyle(.link).font(.system(size: 11))
                    Spacer()
                    Button("cancel") { accounts.choosing = false; accounts.note = "" }.buttonStyle(.plain).font(.system(size: 11)).opacity(0.6)
                }
            }
            .padding(.leading, 24)
        }
    }

    @ViewBuilder func section<C: View>(_ title: String, _ icon: String, @ViewBuilder _ c: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            Label(title, systemImage: icon).font(.system(size: 12, weight: .semibold, design: .rounded)).opacity(0.6)
            c()
        }
    }

    /// name, slider, value on one line
    func slider(_ name: String, _ v: Binding<Double>, _ r: ClosedRange<Double>, _ shown: String) -> some View {
        HStack(spacing: 10) {
            Text(name).lineLimit(1).fixedSize()
            Slider(value: v, in: r).tint(Color(red: 0.91, green: 0.56, blue: 0.71)).controlSize(.small)
            Text(shown).font(.system(size: 12, design: .monospaced)).opacity(0.6).lineLimit(1).fixedSize().frame(minWidth: 40, alignment: .trailing)
        }
    }
}
