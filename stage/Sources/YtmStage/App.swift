// ytm.app: the stage, and a small player in the menu bar. `ytm stage` opens it; closing the stage window keeps the app in
// the menu bar with nothing drawing. headless checks, no screen recording, no window:
//   `YtmStage --snapshot out.png --vid <id> --t <s> [--look auto|dusk|rain|neon|stars|paper] [--accent A] [--motion M] [--settings]
//            [--gesture drop|lift] [--at s] [--size WxH] [--hidden] [--hour H]` renders the backdrop (core animation, offscreen) +
//            the glass to png. --look auto picks from the song's real words, cover and analysis (read-only, nothing cached);
//            --hidden draws the glass the way stillness leaves it (faded, just the ambient line),
//   `YtmStage --snapshot-menubar out.png --vid <id> --t <s> [--light] [--look L]` renders the menu-bar panel,
//   both take `--search-fixture hits.json [--query words]`: the search box answered by that file (the shape `ytm search --json`
//            prints), three cards under the stage's field or three rows over the panel's chips. no process, no network,
//   `YtmStage --bench [--vid <id>]` times the pre-render of every look (ms + MB),
//   `YtmStage --moments <vid> [--t s]` prints what the backdrop would schedule from that song's analysis (read-only),
//   `YtmStage --checks` runs the pure checks (song ids from urls, cover trims, cover numbers, the auto look's flicker rules),
//   `YtmStage --autolook cases.json` checks the auto look's rules against the shared table (tests/autolook_cases.json).
import SwiftUI
import AppKit
import Metal
import QuartzCore

@main
enum Entry {
    static func main() {
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--snapshot-menubar"), i + 1 < args.count { MainActor.assumeIsolated { Snapshot.menubar(args, out: args[i + 1]) }; return }
        if let i = args.firstIndex(of: "--moments"), i + 1 < args.count { MainActor.assumeIsolated { Snapshot.moments(args[i + 1], args) }; return }
        if let i = args.firstIndex(of: "--autolook"), i + 1 < args.count { exit(Snapshot.autolook(args[i + 1])) }
        if args.contains("--checks") { exit(Snapshot.checks()) }
        if args.contains("--bench") { MainActor.assumeIsolated { Snapshot.bench(args) }; return }
        if let i = args.firstIndex(of: "--snapshot"), i + 1 < args.count { MainActor.assumeIsolated { Snapshot.run(args, out: args[i + 1]) }; return }
        // an unknown --flag is a typo or an older binary: never fall through to opening the stage window (10-23: a stale
        // build given --moments opened the gui for ten minutes)
        if let bad = args.dropFirst().first(where: { $0.hasPrefix("--") }) {
            FileHandle.standardError.write("YtmStage: unknown \(bad) · headless flags: --snapshot --snapshot-menubar --bench --moments --autolook --checks\n".data(using: .utf8)!)
            exit(2)
        }
        YtmStageApp.main()
    }
}

/// the stage is an appkit window this delegate owns (StageWindowController); swiftui only keeps the menu bar.
/// the last window closing is not the end: the menu bar keeps the app (and its socket to mpv) alive
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ n: Notification) { MainActor.assumeIsolated { StageWindowController.show() } }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    /// a dock click or `open -a ytm` while only the menu bar is up: bring the stage back
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        MainActor.assumeIsolated { StageWindowController.show() }
        return true
    }
}

struct YtmStageApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        let hub = Hub.shared
        MenuBarExtra {
            MenuBarPanel(player: hub.player, cover: hub.cover, prefs: hub.prefs, auto: hub.auto, panel: hub.panel, search: hub.panelSearch)
        } label: {
            Image(systemName: "waveform")
        }
        .menuBarExtraStyle(.window)
    }
}

@MainActor enum Snapshot {
    /// --checks: the logic behind 0.8.1's fixes that runs without a player, a network or a window. -> exit code
    nonisolated static func checks() -> Int32 {
        var bad = 0
        func check(_ name: String, _ ok: Bool, _ detail: String = "") { if !ok { bad += 1 }; print("\(ok ? "ok  " : "FAIL") \(name)  \(detail)") }
        // moods come from ytm itself: the parse, and that the fallback (before ytm answers) is grouped and never empty
        let pm = Player.parseModes("warning\n[{\"name\":\"coding\",\"group\":\"work\",\"what\":\"x\"},{\"name\":\"pottery\",\"group\":\"mine\"},{\"group\":\"core\"}]\ntrailing")
        check("modes: the json is read past a warning, a row without a name is dropped", pm.map(\.id) == ["coding", "pottery"] && pm[1].group == "mine" && pm[1].what == "", pm.map(\.id).joined(separator: ","))
        check("modes: garbage parses to nothing, so the fallback stays", Player.parseModes("nope").isEmpty && Player.parseModes("").isEmpty)
        check("modes: the fallback has every group but yours, and each mode has an icon or a default", !Player.fallbackModes.isEmpty && Set(Player.fallbackModes.map(\.group)).isSuperset(of: ["core", "work", "move", "wind"]))
        check("modes: every group the cli can send is shown", Set(MODE_GROUPS.map { $0.0 }) == ["work", "move", "wind", "core", "mine"])
        let sw = SurfState(); sw.newSeeds = "ambient,  slow jazz; rain\npiano -x"
        check("surf: seed words keep letters and commas, drop the rest and any leading dash", sw.seedWords == ["ambient,", "slow", "jazz", "rain", "piano", "x"], sw.seedWords.joined(separator: "|"))
        sw.newName = "pottery"; check("surf: a good mood name passes", sw.nameOK)
        for bad in ["", "A", "x", "Bad Name", "9lives", "this-name-is-too-long"] { sw.newName = bad; check("surf: '\(bad)' is not a mood name", !sw.nameOK) }
        // song ids: a query item, 11 characters, nothing for a gap between songs
        for (p, want) in [("https://music.youtube.com/watch?v=zqKrpw36xnU&pp=ygUv=x", "zqKrpw36xnU"), ("", ""), ("/tmp/a.mp3", ""),
                          ("https://youtu.be/zqKrpw36xnU", ""), ("https://www.youtube.com/watch?list=x&v=zqKrpw36xnU", "zqKrpw36xnU")] {
            check("vid(\(p.isEmpty ? "empty" : p))", Player.vid(p) == want, Player.vid(p))
        }
        // covers: w×h with black bars of `bar` rows top and bottom (the rest a colour), through Cover.trim
        func img(_ w: Int, _ h: Int, bar: Int, rgb: (UInt8, UInt8, UInt8) = (200, 120, 60)) -> CGImage {
            var px = [UInt8](repeating: 0, count: w * h * 4)
            for y in bar..<max(bar, h - bar) { for x in 0..<w { let i = (y * w + x) * 4; px[i] = rgb.0; px[i + 1] = rgb.1; px[i + 2] = rgb.2; px[i + 3] = 255 } }
            for y in 0..<h where y < bar || y >= h - bar { for x in 0..<w { px[(y * w + x) * 4 + 3] = 255 } }
            let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: CGColorSpaceCreateDeviceRGB(),
                                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
            return ctx.makeImage()!
        }
        let sq = Cover.trim(img(544, 544, bar: 119)), hq = Cover.trim(img(480, 360, bar: 45))
        check("square 16:9 thumbnail loses its bars", sq.width == 544 && abs(sq.height - 306) <= 12, "\(sq.width)×\(sq.height)")
        check("4:3 hqdefault (the stand-in) loses its bars", hq.width == 480 && abs(hq.height - 270) <= 12, "\(hq.width)×\(hq.height)")
        let black = Cover.trim(img(544, 544, bar: 272)), plain = Cover.trim(img(544, 544, bar: 0)), tiny = Cover.trim(img(120, 120, bar: 40))
        check("an all-black cover stays whole", black.height == 544, "\(black.height)")
        check("a cover with no bars stays whole", plain.height == 544, "\(plain.height)")
        check("never a crop under 64 px", tiny.height == 120, "\(tiny.width)×\(tiny.height)")
        // the cover's numbers: pale, warm grey, dark electric
        let pale = Cover.read(img(64, 64, bar: 0, rgb: (215, 215, 205))).1!, warm = Cover.read(img(64, 64, bar: 0, rgb: (110, 85, 70))).1!
        let elec = Cover.read(img(64, 64, bar: 0, rgb: (40, 230, 240))).1!
        check("pale cover reads pale", pale.lum > 0.6 && pale.sat < 0.25 && pale.colourful == 0, "\(pale)")
        check("brown cover reads warm", warm.warm == 1 && warm.colourful == 1 && warm.sat < 0.4, "\(warm)")
        check("cyan cover reads electric", elec.neonpx == 1 && elec.cool == 1, "\(elec)")
        // the flicker rules: the words pick at once, a later cover only within 8 s (or a weak pick turning firm)
        let a = AutoPick(persist: false)
        a.track(Track(vid: "K8v_DaCcORQ", title: "Midnight City", artist: "M83"), at: 0, hour: 12)
        check("track change: words pick at once", a.pick.look == .neon && a.from == "words", a.pick.reason)
        let paleCover = CoverStats(lum: 0.8, sat: 0.1, colourful: 0.05, warm: 0.5, cool: 0.5, dark: 0, neonpx: 0)
        for (pos, want) in [(30.0, Look.dusk), (2.0, Look.paper)] {   // 'focus' + a pale cover is paper, but only for a young song
            let f = AutoPick(persist: false)
            f.track(Track(vid: "focus000001", title: "Focus"), at: 0, hour: 12)
            f.cover(paleCover, vid: "focus000001", at: pos)
            check("cover at \(Int(pos)) s → \(want.id)", f.pick.look == want, f.pick.reason)
        }
        let b = AutoPick(persist: false)
        b.track(Track(vid: "od6AI4m0nN8", title: "Run", artist: "Arman Cekin"), at: 0, hour: 12)
        b.cover(CoverStats(lum: 0.276, sat: 0.148, colourful: 0.133, warm: 1, cool: 0, dark: 0.005, neonpx: 0), vid: "od6AI4m0nN8", at: 2)
        check("early cover: refined, grey cover gets ember", b.pick.look == .dusk && b.pick.accent == "ember" && b.from == "words+cover", "\(b.pick)")
        b.cover(CoverStats(), vid: "someoneelse", at: 1)
        check("another song's cover is ignored", b.pick.accent == "ember")
        b.track(Track(vid: "x_VETA9GTqA", title: "Cyberverse (Sped Up)", artist: "Navjaxx"), at: 0, hour: 12)
        check("old cover still up: the old song's pick", b.pick(for: "od6AI4m0nN8").accent == "ember" && b.pick(for: "x_VETA9GTqA").look == .neon)
        check("work mode is not the pick's call", AutoLook.decide(LookSignals(mode: "work")).look == .paper && a.pick.look == .neon)
        // 0.8.2 search: never more than three, whatever the cli sends; a warning line before the json is fine; junk is nothing
        func row(_ v: String, _ t: String = "t") -> String { "{\"videoId\":\"\(v)\",\"title\":\"\(t)\",\"artist\":\"a\",\"album\":\"b\",\"duration\":185,\"art\":\"\"}" }
        let five = "[" + ["aaaaaaaaaa1", "aaaaaaaaaa2", "aaaaaaaaaa3", "aaaaaaaaaa4", "aaaaaaaaaa5"].map { row($0) }.joined(separator: ",") + "]"
        let p5 = SearchHit.parse(five)
        check("search: five from the cli → three shown", p5.count == 3 && p5.map(\.id) == ["aaaaaaaaaa1", "aaaaaaaaaa2", "aaaaaaaaaa3"], "\(p5.count)")
        let warned = SearchHit.parse("ytm: anonymous for 10 min\n[" + row("bbbbbbbbbb1", "Run [live]") + "]")
        check("search: warning line first, brackets in a title", warned.count == 1 && warned[0].title == "Run [live]" && warned[0].duration == 185, "\(warned)")
        let mixed = SearchHit.parse("[" + [row("cccccccccc1"), "{\"title\":\"no id\"}", row("short"), row("cccccccccc1"), row("cccccccccc2")].joined(separator: ",") + "]")
        check("search: rows without a real id or twice are dropped", mixed.map(\.id) == ["cccccccccc1", "cccccccccc2"], "\(mixed.map(\.id))")
        check("search: junk, an error, nothing → no hits", SearchHit.parse("").isEmpty && SearchHit.parse("ytm: nothing for 'x'").isEmpty && SearchHit.parse("[1,2]").isEmpty)
        check("search: the card's second line", SearchHit(id: "x", artist: "ODESZA", duration: 215).line == "ODESZA · 3:35" && SearchHit(id: "x").line == "" && SearchHit(id: "x", duration: 3725).line == "1:02:05")
        let st = SearchState(); st.fixture(p5, for: "q", thumbs: [:])
        check("search: ↓ ↓ ↓ wraps to the first", st.move(1) && st.move(1) && st.move(1) && st.sel == 0 && st.move(-1) && st.sel == 2)
        st.close(); check("search: esc empties the box", !st.open && !st.move(1) && st.asked.isEmpty)
        // 0.8.2 wake: one nudge only for a song that's 'playing' with mpv's clock standing still
        check("wake: still clock while playing is stuck", Player.stuck(83.2, 83.2, paused: false, vid: "zqKrpw36xnU"))
        check("wake: a moving clock, a pause or no song is not", !Player.stuck(83.2, 93.1, paused: false, vid: "zqKrpw36xnU")
              && !Player.stuck(83.2, 83.2, paused: true, vid: "zqKrpw36xnU") && !Player.stuck(0, 0, paused: false, vid: ""))
        print(bad == 0 ? "checks: all ok" : "checks: \(bad) failed")
        return bad == 0 ? 0 : 1
    }

    /// --autolook: every row of the shared table through AutoLook.decide; the python mirror reads the same file. -> exit code
    nonisolated static func autolook(_ path: String) -> Int32 {
        guard let d = FileManager.default.contents(atPath: path),
              let rows = (try? JSONSerialization.jsonObject(with: d)) as? [[String: Any]] else { print("autolook: can't read \(path)"); return 2 }
        var bad = 0
        for r in rows {
            let g = r["signals"] as? [String: Any] ?? [:], e = r["expect"] as? [String: String] ?? [:]
            var s = LookSignals()
            s.title = g["title"] as? String ?? ""; s.artist = g["artist"] as? String ?? ""; s.album = g["album"] as? String ?? ""
            s.mode = g["mode"] as? String ?? "normal"; s.hour = g["hour"] as? Int; s.length = g["length"] as? Double ?? 0
            s.bpm = g["bpm"] as? Double; s.tilt = g["tilt"] as? Double; s.density = g["density"] as? Double
            s.onset = g["onset"] as? Double; s.quiet = g["quiet"] as? Double; s.drops = g["drops"] as? Int ?? 0
            if let lum = g["lum"] as? Double {
                s.cover(CoverStats(lum: lum, sat: g["sat"] as? Double ?? 0, colourful: g["colourful"] as? Double ?? 0, warm: g["warm"] as? Double ?? 0,
                                   cool: g["cool"] as? Double ?? 0, dark: g["dark"] as? Double ?? 0, neonpx: g["neonpx"] as? Double ?? 0))
            }
            let p = AutoLook.decide(s), conf = r["conf"] as? Double
            let ok = p.look.id == e["look"] && p.accent == e["accent"] && p.motion == e["motion"] && p.reason == e["reason"]
                && (conf.map { abs($0 - p.conf) < 0.006 } ?? true)
            if !ok { bad += 1; print("MISMATCH \(r["vid"] ?? "?"): got \(p.look.id) \(p.accent) \(p.motion) \(p.conf) '\(p.reason)' want \(e) \(conf ?? -1)") }
        }
        print("autolook: \(rows.count) cases, \(bad) mismatches")
        return bad == 0 ? 0 : 1
    }

    /// --bench: the pre-render of every look, timed. this is all the cpu a song change costs now (10-23: "too heavy").
    static func bench(_ args: [String]) {
        let cover = Cover(), vid = pickVid(args)
        cover.set(vid: vid, file: ART.appendingPathComponent("\(vid).jpg"))
        let size = CGSize(width: 1360, height: 860)
        print("bench · vid \(vid) · \(cover.cgImage.map { "\($0.width)×\($0.height) cover" } ?? "no cover")")
        let base = SelfUsage.footprintMB()
        for acc in ["cover", value(args, "--accent") ?? "ember"] {
            for lk in Look.allCases {
                let pal = Accent.palette(acc, cover: cover.palette)
                let t0 = CACurrentMediaTime()
                let pl = Painter.render(lk, cover: cover.cgImage, pal: pal, accent: acc, seed: vid)
                let t1 = CACurrentMediaTime()
                let tree = Builder.build(lk, pl, pal, size, Builder.Opts())
                let t2 = CACurrentMediaTime()
                print(String(format: "%-6@ %-5@ · paint %6.1f ms · build %5.2f ms · stills %4.2f MB · footprint %5.1f MB (peak %5.1f)",
                             acc as NSString, lk.id as NSString, (t1 - t0) * 1000, (t2 - t1) * 1000, Double(pl.bytes) / 1_048_576,
                             SelfUsage.footprintMB(), SelfUsage.peakMB()))
                _ = tree
            }
        }
        print(String(format: "baseline footprint %.1f MB", base))
    }

    /// --moments: the song's analysis as the backdrop sees it, and what it would schedule from a few positions
    static func moments(_ vid: String, _ args: [String]) {
        guard let a = SongAnalysis(vid: vid) else { print("no analysis for \(vid) (needs analysis/<vid>.json + .u8)"); exit(1) }
        print(String(format: "%@ · %.1f bpm · %d beats · %d moments · kept %.1f KB", vid as NSString, a.bpm, a.beats.count, a.moments.count, Double(a.bytes) / 1024))
        for m in a.moments { print(String(format: "  %@ at %6.2f s · strength %.2f", (m.kind > 0.75 ? "drop" : "lift") as NSString, m.t, m.s)) }
        let ts: [Double] = value(args, "--t").flatMap { Double($0) }.map { [$0] } ?? [0, 20, 60, 83, 120]
        for t in ts {
            let br = Schedule.breath(a, pos: t).map { String(format: "bar starts %+.3f s", $0) } ?? "no breath"
            let mo = Schedule.moment(a, pos: t).map { m in
                String(format: "next %@ in %.2f s (s %.2f)%@", (m.drop ? "drop" : "lift") as NSString, m.at, Double(m.s),
                       (m.leanAt.map { String(format: ", lean in at +%.2f s", $0) } ?? "") as NSString)
            } ?? "no more moments"
            print(String(format: "  t %6.1f · intensity %.2f · %@ · %@", t, a.intensity(at: t), br as NSString, mo as NSString))
        }
    }

    static let ART = HOME.appendingPathComponent(".local/share/ytm/art")   // read-only: real covers are fine to look at

    static func pickVid(_ args: [String]) -> String {
        if let v = value(args, "--vid") { return v }
        if FileManager.default.fileExists(atPath: ART.appendingPathComponent("PZIr-tzmK4M.jpg").path) { return "PZIr-tzmK4M" }
        let any = (try? FileManager.default.contentsOfDirectory(atPath: ART.path))?.sorted().first { $0.hasSuffix(".jpg") }
        return any.map { String($0.dropLast(4)) } ?? ""
    }

    static func value(_ args: [String], _ k: String) -> String? { args.firstIndex(of: k).flatMap { $0 + 1 < args.count ? args[$0 + 1] : nil } }

    static func run(_ args: [String], out: String) {
        let vid = pickVid(args)
        let t = Double(value(args, "--t") ?? "50") ?? 50
        // --size WxH: the window's size (the default is the stage's first-launch size; 980×620 is its smallest)
        let wh = (value(args, "--size") ?? "").split(separator: "x").compactMap { Double($0) }
        let size = wh.count == 2 ? CGSize(width: wh[0], height: wh[1]) : CGSize(width: 1360, height: 860)
        let cover = Cover(); cover.set(vid: vid, file: ART.appendingPathComponent("\(vid).jpg"))
        // --look auto: the same pick the app would make for this song, from what's on disk, saved nowhere
        let auto = AutoPick(persist: false), tr = track(vid, args)
        auto.track(tr, at: 0, hour: value(args, "--hour").flatMap { Int($0) } ?? Calendar.current.component(.hour, from: Date()))
        auto.cover(cover, at: 0); if let a = SongAnalysis(vid: vid) { auto.audio(a, at: 0) }
        let want = value(args, "--look") ?? "auto", isAuto = want == "auto"
        let look = isAuto ? auto.pick.look : Look(id: want)
        let accent = value(args, "--accent") ?? (isAuto ? auto.pick.accent : "cover")
        // the glass first, drawn alone (clear background): it reports where the cover sits, which neon drive's sun and the
        // gestures need. then the backdrop under it: the same two layers the window has
        let player = Player(live: false)
        player.track = tr
        player.duration = 182; player.paused = false; player.connected = true; player.seek(t)
        let ui = UIState(); ui.showUI = !args.contains("--hidden")
        if let a = SongAnalysis(vid: vid) { ui.bpm = Int(a.bpm.rounded()) }
        let st = StageSettings(); st.open = args.contains("--settings")
        let surf = SurfState(); surf.open = args.contains("--surf")
        if args.contains("--surf") {   // fixtures: the drawer asks nothing in a snapshot
            player.modes = Player.fallbackModes + ["design", "writing", "reading", "cafe", "run", "cook", "gaming", "party", "winddown"].map { ModeInfo(id: $0, group: ["design": "work", "writing": "work", "reading": "work", "cafe": "work", "run": "move", "cook": "move", "gaming": "move", "party": "move", "winddown": "wind"][$0] ?? "core", what: "") } + [ModeInfo(id: "pottery", group: "mine", what: "")]
            surf.outputs = [OutDev(id: 1, name: "WH-1000XM4", on: false), OutDev(id: 2, name: "MacBook Air Speakers", on: false)]
            surf.cacheFiles = 128; surf.cacheMB = 737; surf.cacheCap = 1024
            surf.playlists = [PlaylistRef(id: "a", title: "Night drive", count: 120), PlaylistRef(id: "b", title: "Favourites", count: 148), PlaylistRef(id: "c", title: "Study beats", count: 86)]
        }
        if args.contains("--liked") { player.rating = "LIKE" }
        if let m = value(args, "--mode") { player.mode = m }
        let prefs = Prefs(watching: false); prefs.preview(look: isAuto ? "auto" : look.id, accent: value(args, "--accent") ?? "cover")
        let search = fixture(args); ui.query = search.asked
        let view = RootView(player: player, cover: cover, prefs: prefs, auto: auto, snapshot: true, ui: ui, settings: st, surf: surf, search: search)
            .frame(width: size.width, height: size.height).environment(\.colorScheme, .dark)
        guard let glass = hosted(view, size: size) else { print("glass failed"); exit(1) }
        let f = ArtSpot.frame, spot = f.width > 0 ? CGPoint(x: f.midX, y: size.height - f.midY) : CGPoint(x: 56 + 170, y: size.height / 2)
        var o = Builder.Opts(); o.motion = Motion.factor(value(args, "--motion") ?? "calm"); o.art = spot; o.artW = f.width
        let tree = BackdropView.headless(look, cover: cover, accent: accent, size: size, opts: o)
        // a moment in flight, to check the gestures: --gesture drop|lift, drawn --at seconds after it lands
        if let g = value(args, "--gesture") { tree.gesture?(g == "drop", 0.9, spot) }
        let at = Double(value(args, "--at") ?? "0.6") ?? 0.6
        let stageURL = URL(fileURLWithPath: out).deletingPathExtension().appendingPathExtension("stage.png")
        guard let bg = renderLayers(tree.root, size: size, at: CACurrentMediaTime() + at) else { print("render failed"); exit(1) }
        write(bg, stageURL)
        guard let ctx = CGContext(data: nil, width: Int(size.width), height: Int(size.height), bitsPerComponent: 8,
                                  bytesPerRow: 0, space: sRGB, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { print("glass failed"); exit(1) }
        let r = CGRect(origin: .zero, size: size)
        ctx.draw(bg, in: r); ctx.draw(glass, in: r)
        if let full = ctx.makeImage() { write(full, URL(fileURLWithPath: out)) }
        print("snapshot: \(out) · stage only: \(stageURL.path) · \(look.id) · accent \(accent) · vid \(vid)"
              + (isAuto ? " · auto (\(auto.from), conf \(auto.pick.conf)): \(auto.pick.reason)" : ""))
    }

    /// the layer tree through core animation's own renderer, into an offscreen metal texture: compositing filters, 3d and
    /// animations all as the window server would draw them (layer.render(in:) skips all three). no shaders are compiled.
    static func renderLayers(_ layer: CALayer, size: CGSize, at time: CFTimeInterval) -> CGImage? {
        guard let dev = MTLCreateSystemDefaultDevice(), let q = dev.makeCommandQueue() else { return nil }
        let w = Int(size.width), h = Int(size.height)
        let d = MTLTextureDescriptor.texture2DDescriptor(pixelFormat: .bgra8Unorm, width: w, height: h, mipmapped: false)
        d.usage = [.renderTarget, .shaderRead, .shaderWrite]; d.storageMode = .shared
        guard let tex = dev.makeTexture(descriptor: d) else { return nil }
        let r = CARenderer(mtlTexture: tex, options: [kCARendererColorSpace: sRGB, kCARendererMetalCommandQueue: q])
        let host = CALayer(); host.frame = CGRect(origin: .zero, size: size); host.backgroundColor = CGColor(gray: 0.02, alpha: 1)
        host.addSublayer(layer)
        r.layer = host; r.bounds = host.bounds
        CATransaction.flush()                                   // after the renderer has the layer: it commits into its context
        r.beginFrame(atTime: time, timeStamp: nil); r.addUpdate(r.bounds); r.render(); r.endFrame()
        if let cb = q.makeCommandBuffer() { cb.commit(); cb.waitUntilCompleted() }   // same queue: waits for the frame
        var px = [UInt8](repeating: 0, count: w * h * 4)
        tex.getBytes(&px, bytesPerRow: w * 4, from: MTLRegionMake2D(0, 0, w, h), mipmapLevel: 0)
        guard let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: sRGB,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue),
              let raw = ctx.makeImage(), let out = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0, space: sRGB,
                                                            bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue) else { return nil }
        // the texture's first row is the layer's bottom (core animation counts up): turn it the right way for a png
        out.translateBy(x: 0, y: CGFloat(h)); out.scaleBy(x: 1, y: -1); out.draw(raw, in: CGRect(x: 0, y: 0, width: w, height: h))
        return out.makeImage()
    }

    /// swiftui drawn by appkit itself (an NSHostingView in a window that is never shown), so the real controls render:
    /// ImageRenderer leaves text fields, sliders and switches as placeholders
    static func hosted<V: View>(_ v: V, size: CGSize) -> CGImage? {
        _ = NSApplication.shared
        let w = NSWindow(contentRect: CGRect(origin: CGPoint(x: -20000, y: -20000), size: size), styleMask: .borderless, backing: .buffered, defer: true)
        w.isOpaque = false; w.backgroundColor = .clear; w.appearance = NSAppearance(named: .darkAqua)
        let h = NSHostingView(rootView: v); h.frame = CGRect(origin: .zero, size: size)
        w.contentView = h
        h.layoutSubtreeIfNeeded()
        RunLoop.main.run(until: Date().addingTimeInterval(0.15))   // one pass of swiftui's update, offscreen
        guard let rep = h.bitmapImageRepForCachingDisplay(in: h.bounds) else { return nil }
        h.cacheDisplay(in: h.bounds, to: rep)
        return rep.cgImage
    }

    /// the song's real title from titles.json (read-only), else --title / --artist / --album
    static func track(_ vid: String, _ args: [String]) -> Track {
        let f = HOME.appendingPathComponent(".local/share/ytm/titles.json")
        let d = ((try? Data(contentsOf: f)).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] })?[vid] as? [String: Any] ?? [:]
        return Track(vid: vid, title: value(args, "--title") ?? d["title"] as? String ?? "Loyal", artist: value(args, "--artist") ?? d["artist"] as? String ?? "ODESZA",
                     album: value(args, "--album") ?? d["album"] as? String ?? "A Moment Apart", art: "")
    }

    /// --search-fixture: a search box already answered, from a file (covers from the real art folder, read only, or none)
    static func fixture(_ args: [String]) -> SearchState {
        let s = SearchState()
        guard let f = value(args, "--search-fixture") else { return s }
        guard let d = FileManager.default.contents(atPath: f) else { print("search fixture: can't read \(f)"); exit(2) }
        let hits = SearchHit.parse(String(decoding: d, as: UTF8.self))
        var th: [String: NSImage] = [:]
        for h in hits { if let img = NSImage(contentsOf: ART.appendingPathComponent("\(h.id).jpg")) { th[h.id] = img } }
        s.fixture(hits, for: value(args, "--query") ?? "night drive", thumbs: th)
        return s
    }

    static func write(_ img: CGImage, _ url: URL) {
        guard let dest = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil) else { return }
        CGImageDestinationAddImage(dest, img, nil); CGImageDestinationFinalize(dest)
    }

    /// the menu-bar panel, drawn offscreen to a png (2x), dark unless --light
    static func menubar(_ args: [String], out: String) {
        let vid = pickVid(args)
        let t = Double(value(args, "--t") ?? "50") ?? 50
        let cover = Cover(); cover.set(vid: vid, file: ART.appendingPathComponent("\(vid).jpg"))
        let player = Player(live: false)
        player.track = track(vid, args)
        player.duration = 207; player.paused = args.contains("--paused"); player.connected = true; player.volume = 60
        if args.contains("--work") { player.mode = "work" }
        let prefs = Prefs(watching: false); prefs.preview(look: value(args, "--look") ?? "auto", accent: nil)
        let auto = AutoPick(persist: false); auto.track(player.track, at: 0); auto.cover(cover, at: 0)
        if let a = SongAnalysis(vid: vid) { auto.audio(a, at: 0) }
        let light = args.contains("--light")
        let search = fixture(args), panel = PanelState(); panel.query = search.asked
        let view = MenuBarPanel(player: player, cover: cover, prefs: prefs, auto: auto, panel: panel, search: search, snapshot: true, fixedPos: t)
            .background(light ? Color(white: 0.96) : Color(white: 0.13), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .environment(\.colorScheme, light ? .light : .dark)
        let ir = ImageRenderer(content: view); ir.scale = 2
        guard let cg = ir.cgImage, let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: out) as CFURL, "public.png" as CFString, 1, nil) else {
            print("render failed"); exit(1)
        }
        CGImageDestinationAddImage(dest, cg, nil); CGImageDestinationFinalize(dest)
        print("snapshot-menubar: \(out) · \(cg.width)×\(cg.height)")
    }
}
