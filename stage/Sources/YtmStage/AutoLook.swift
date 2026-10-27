// auto look v1 (0.8.1): what the song is →
// which look it gets. pure and deterministic: the same signals give the same pick, and the pick says why ("neon drive · 129 bpm,
// heavy bass, a wall of sound"). a manual look always wins; work mode is always paper. offline: words, the cover's pixels and
// the analysis already on disk, nothing fetched. mirrors ytm/lib/autolook.py rule for rule; ytm/tests/autolook_cases.json
// (real songs) is the table both sides must agree on.
import Foundation

struct LookSignals {
    var title = "", artist = "", album = ""
    var mode = "normal", hour: Int? = nil, length: Double = 0
    // the analysis (nil until bin/ytm analyze has run). bands are normalised per song, so these are shapes, not loudness
    var bpm: Double? = nil, tilt: Double? = nil, density: Double? = nil, onset: Double? = nil, quiet: Double? = nil, drops = 0
    // the cover, 32×32 (nil until it loads)
    var lum: Double? = nil, sat = 0.0, colourful = 0.0, warm = 0.0, cool = 0.0, dark = 0.0, neonpx = 0.0
}

struct LookPick: Equatable { let look: Look, accent: String, motion: String, conf: Double, reason: String }

enum AutoLook {
    static let rulesV = 1
    // strong (3): genre names that mean one look, read anywhere, artist too ("harrylofi"). weak (1 each, 2 at most): moods that
    // lean, read in the title + album only ("moon.walken" and "lost sky" are names, not skies)
    static let words: [(Look, strong: String, weak: String)] = [
        (.rain, #"lo-?fi|chillhop|jazzhop|beats to (?:study|relax|sleep)|lofi hip hop"#,
                #"\b(?:rain\w*|study|cozy|cafe|coffee|sleep\w*|chill|window)\b"#),
        (.neon, #"synthwave|retrowave|outrun(?!n)|cyber\w*|darksynth|vaporwave|phonk|nightcall"#,
                #"\b(?:neon|night ?drive|drive|midnight|after ?dark|80s|turbo|city)\b"#),
        (.stars, #"ambient|star ?gaz\w*|space ?music|interstellar|nebula|cosmos|cosmic|meditation|drone(?!s)"#,
                 #"\b(?:stars?|galaxy|moon\w*|orbit|astral|sky|floating|dream\w*)\b"#),
        (.dusk, #"acoustic|piano|orchestra\w*|trailer score"#,
                #"\b(?:soundtrack|\bost\b|score|sunset|summer|golden|dusk|sunrise|beach|tropical|guitar|strings)\b"#),
        (.paper, #"deep work|concentration|productivity|focus music|music for (?:focus|studying|work)"#,
                 #"\b(?:focus|classical|background music)\b"#),
    ]
    private static let rx: [(Look, NSRegularExpression, NSRegularExpression)] = words.map {
        ($0.0, try! NSRegularExpression(pattern: $0.strong), try! NSRegularExpression(pattern: $0.weak))
    }

    static func decide(_ s: LookSignals) -> LookPick {
        if s.mode == "work" { return LookPick(look: .paper, accent: accent(.paper, s), motion: "calm", conf: 1, reason: "paper · work mode") }
        var pts: [Look: Double] = Dictionary(uniqueKeysWithValues: Look.allCases.map { ($0, $0 == .dusk ? 1 : 0) })   // dusk is the floor: thin evidence lands there
        var why: [Look: [String]] = [:]
        func add(_ l: Look, _ p: Double, _ w: String) { pts[l, default: 0] += p; why[l, default: []].append(w) }

        let named = "\(s.title) \(s.album)".lowercased(), any = "\(named) \(s.artist)".lowercased()
        for (l, strong, weak) in rx {
            if let m = strong.firstMatch(in: any, range: NSRange(any.startIndex..., in: any)), let r = Range(m.range, in: any) {
                add(l, 3, "'\(any[r])'"); continue
            }
            var hit: [String] = []
            for m in weak.matches(in: named, range: NSRange(named.startIndex..., in: named)) {
                if let r = Range(m.range, in: named), !hit.contains(String(named[r])) { hit.append(String(named[r])) }
                if hit.count == 2 { break }
            }
            if !hit.isEmpty { add(l, Double(hit.count), hit.map { "'\($0)'" }.joined(separator: " ")) }
        }
        if s.length >= 1200 { add(.rain, 0.5, "an hour-long mix") }        // 20 min+: a stream, almost always a lofi / focus bed

        if let b = s.bpm {
            if b >= 118 { add(.neon, 1.5, "\(Int(b.rounded())) bpm") }
            else if b <= 90 { add(.rain, 0.5, "\(Int(b.rounded())) bpm"); add(.stars, 0.5, "\(Int(b.rounded())) bpm") }
        }
        if let t = s.tilt { if t >= 0.35 { add(.neon, 1, "heavy bass") } else if t <= 0.24 { add(.dusk, 0.5, "bright, open top end") } }
        if let o = s.onset, o <= 0.14 { add(.stars, 1, "sparse, long notes") }
        if let q = s.quiet, q >= 0.30 { add(.stars, 0.5, "lots of quiet") }
        if let d = s.density, let q = s.quiet, d >= 0.55, q <= 0.10 { add(.neon, 1, "a wall of sound") }
        if s.drops >= 2 { add(.neon, 0.5, "\(s.drops) drops") }

        if let lum = s.lum {
            if s.neonpx >= 0.05 || (s.sat >= 0.6 && s.cool >= 0.7 && s.dark >= 0.6) { add(.neon, 1, "a dark electric cover") }
            else if s.dark >= 0.7 && s.cool >= 0.6 { add(.stars, 1, "a night-sky cover") }
            if s.warm >= 0.8 && lum >= 0.25 { add(.dusk, 1, "a warm cover") }
            if lum >= 0.6 && s.sat <= 0.25 { add(.paper, 1, "a pale cover") }
            else if s.sat <= 0.3 && s.cool >= 0.5 && lum < 0.5 { add(.rain, 0.5, "a grey-blue cover") }
        }
        if let h = s.hour {
            if h >= 21 || h < 5 { add(.stars, 0.25, "late"); add(.neon, 0.25, "late") } else if (17..<20).contains(h) { add(.dusk, 0.25, "evening") }
        }

        let order = Look.allCases.filter { $0.autoPicks }.sorted { (pts[$0]!, -$0.rawValue) > (pts[$1]!, -$1.rawValue) }   // ties: dusk, rain, neon, stars
        var top = order[0]; let next = order[1], margin = pts[top]! - pts[next]!
        var conf = max(0, min(1, pts[top]! / 4 * min(1, margin / 1.5)))
        if top != .dusk && pts[top]! - pts[.dusk]! < 0.75 { top = .dusk; conf = 0 }   // barely past the floor is not a pick
        let r = (why[top] ?? []).prefix(3)
        var reason = "\(top.name) · " + (r.isEmpty ? "nothing stood out" : r.joined(separator: ", "))
        if top != .dusk && margin < 0.5 && next != .dusk { reason += " · a close call with \(next.name)" }
        return LookPick(look: top, accent: accent(top, s), motion: motion(top, s), conf: (conf * 100).rounded() / 100, reason: reason)
    }

    /// the cover's colours when it has any; a grey cover gets the look's own set (no cover yet: it arrives, then speaks)
    static func accent(_ l: Look, _ s: LookSignals) -> String {
        if s.lum == nil || s.colourful >= 0.3 { return "cover" }
        return [.dusk: "ember", .rain: "sea", .neon: "night", .stars: "night", .paper: "mono", .aurora: "sea", .ember: "ember", .tide: "sea"][l]!
    }

    /// never "still" on its own: still is a choice (reduced motion), not a mood
    static func motion(_ l: Look, _ s: LookSignals) -> String {
        l != .paper && (s.bpm ?? 0) >= 118 && (s.density ?? 0) >= 0.45 ? "lively" : "calm"
    }
}

/// the cover's 32×32 numbers for the rules, read in the same pass as its palette (Cover.read)
struct CoverStats: Equatable { var lum = 0.0, sat = 0.0, colourful = 0.0, warm = 0.0, cool = 0.0, dark = 0.0, neonpx = 0.0 }

extension LookSignals {
    mutating func words(_ t: Track) { title = t.title; artist = t.artist; album = t.album }
    mutating func cover(_ c: CoverStats) {
        lum = c.lum; sat = c.sat; colourful = c.colourful; warm = c.warm; cool = c.cool; dark = c.dark; neonpx = c.neonpx
    }
    mutating func audio(_ a: SongAnalysis) { bpm = a.bpm; tilt = a.tilt; density = a.density; onset = a.onset; quiet = a.quiet; drops = a.drops }
}

/// the auto look's pick for the playing song (the hub owns one). instant from the words at a track change, refined as the
/// cover and the analysis land, but only while the song is young (8 s) or when a weak pick turns firm: a look that swaps
/// mid-song for a shade more evidence reads as a glitch. the better pick waits for the next play, in DATA/looks.json
/// (the cli and the terminal ui read it). work mode is the backdrop's call (always paper), not the pick's
final class AutoPick: ObservableObject {
    @Published private(set) var pick = AutoLook.decide(LookSignals())
    private(set) var signals = LookSignals()
    private(set) var vid = ""
    private var prev = (vid: "", pick: AutoLook.decide(LookSignals()))   // the last song's, while its cover is still up
    private var hasCover = false, hasAudio = false, shownN = 1
    private let persist: Bool
    private var cache: [String: [String: Any]]?
    private static let io = DispatchQueue(label: "ytm.looks", qos: .utility)
    private var file: URL { DATA.appendingPathComponent("looks.json") }

    /// persist false: headless snapshots, which must never write anyone's looks.json
    init(persist: Bool = true) { self.persist = persist }

    var from: String { (["words"] + (hasCover ? ["cover"] : []) + (hasAudio ? ["audio"] : [])).joined(separator: "+") }
    private var evidence: Int { 1 + (hasCover ? 1 : 0) + (hasAudio ? 1 : 0) }

    /// the pick that belongs to the song whose cover is on screen: the backdrop changes look and cover in one crossfade
    func pick(for v: String) -> LookPick { v == prev.vid && v != vid ? prev.pick : pick }

    func track(_ t: Track, at pos: Double, hour: Int = Calendar.current.component(.hour, from: Date())) {
        guard !t.vid.isEmpty else { return }
        if t.vid != vid {
            if !vid.isEmpty { prev = (vid, pick) }
            vid = t.vid; hasCover = false; hasAudio = false
            signals = LookSignals(); signals.hour = hour; signals.words(t)
            // the last play's pick, when it saw more than words: the same song opens on the same look
            if let (p, n) = cached(t.vid), n > 1 { pick = p; shownN = n } else { pick = AutoLook.decide(signals); shownN = 1 }
            save(AutoLook.decide(signals))
            return
        }
        // the same song, its words filled in late (mpv's media-title for a song titles.json didn't know)
        if (t.title, t.artist, t.album) != (signals.title, signals.artist, signals.album) { signals.words(t); refine(pos) }
    }

    func cover(_ c: Cover, at pos: Double) { if let st = c.stats { cover(st, vid: c.loadedVid, at: pos) } }

    func cover(_ st: CoverStats, vid v: String, at pos: Double) {
        guard !vid.isEmpty, v == vid else { return }
        signals.cover(st); hasCover = true; refine(pos)      // again on a stand-in → real cover upgrade: new numbers, same evidence
    }

    func audio(_ a: SongAnalysis, at pos: Double) {
        guard a.vid == vid, !hasAudio else { return }
        signals.audio(a); hasAudio = true; refine(pos)
    }

    func length(_ d: Double, at pos: Double) {
        guard !vid.isEmpty, d != signals.length else { return }
        signals.length = d; refine(pos)
    }

    private func refine(_ pos: Double) {
        let p = AutoLook.decide(signals), n = evidence
        if p == pick { shownN = max(shownN, n) }
        else if (n >= shownN && pos < 8) || (pick.conf < 0.3 && p.conf >= 0.6) { pick = p; shownN = n }
        save(p)
    }

    // ---- DATA/looks.json: {vid: {look, accent, motion, conf, reason, rules, from, at}}, the newest 1500 kept past 2000 ----

    private func load() -> [String: [String: Any]] {
        if let c = cache { return c }
        let j = (try? Data(contentsOf: file)).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: [String: Any]] } ?? [:]
        cache = j; return j
    }

    private func cached(_ v: String) -> (LookPick, Int)? {
        guard persist, let e = load()[v], e["rules"] as? Int == AutoLook.rulesV, let l = e["look"] as? String, Look.ids.contains(l) else { return nil }
        let p = LookPick(look: Look(id: l), accent: e["accent"] as? String ?? "cover", motion: e["motion"] as? String ?? "calm",
                         conf: e["conf"] as? Double ?? 0, reason: e["reason"] as? String ?? "")
        return (p, (e["from"] as? String ?? "words").split(separator: "+").count)
    }

    /// a pick replaces the cached one only when it rests on more evidence (or the rules changed)
    private func save(_ p: LookPick) {
        guard persist, !vid.isEmpty else { return }
        var c = load()
        if let e = c[vid], e["rules"] as? Int == AutoLook.rulesV {
            // equal evidence: only the pick on screen replaces it, and only when it differs (the real cover after a radio
            // stand-in, another hour). else the app shows one look and the terminal ui another, every play
            let had = (e["from"] as? String ?? "").split(separator: "+").count
            let same = e["look"] as? String == p.look.id && e["accent"] as? String == p.accent && e["motion"] as? String == p.motion
            if had > evidence || (had == evidence && (p != pick || same)) { return }
        }
        c[vid] = ["look": p.look.id, "accent": p.accent, "motion": p.motion, "conf": p.conf, "reason": p.reason,
                  "rules": AutoLook.rulesV, "from": from, "at": Int(Date().timeIntervalSince1970)]
        if c.count > 2000 {
            let keep = c.sorted { ($0.value["at"] as? Int ?? 0) > ($1.value["at"] as? Int ?? 0) }.prefix(1500)
            c = Dictionary(uniqueKeysWithValues: keep.map { ($0.key, $0.value) })
        }
        cache = c
        let f = file
        guard let d = try? JSONSerialization.data(withJSONObject: c, options: [.sortedKeys]) else { return }
        AutoPick.io.async {   // whole or not at all: the cli and the terminal ui read it any time
            let tmp = DATA.appendingPathComponent(".looks.json.\(getpid()).tmp")
            guard (try? d.write(to: tmp)) != nil else { return }
            if rename(tmp.path, f.path) != 0 { try? FileManager.default.removeItem(at: tmp) }
        }
    }
}
