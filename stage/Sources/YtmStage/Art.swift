// the cover: fetched once (shared cache with the terminal ui), and its three colours for the light.
import AppKit
import CoreImage

final class Cover: ObservableObject {
    @Published var image: NSImage?
    @Published var palette: [SIMD3<Float>] = Cover.night
    @Published private(set) var ready = 0          // bumps once the image + palette for `loadedVid` are in place
    var cgImage: CGImage?
    private(set) var stats: CoverStats?             // the auto look's numbers for `loadedVid` (nil: no cover)
    private(set) var vid = ""                       // the song asked for
    private(set) var loadedVid = ""                 // the song whose image is here now

    /// headless snapshots: a cover straight from disk, no fetch
    func set(vid v: String, file: URL) {
        vid = v; loadedVid = v
        guard let img = NSImage(contentsOf: file) else { return }
        var r = CGRect(origin: .zero, size: img.size)
        if let raw = img.cgImage(forProposedRect: &r, context: nil, hints: nil) {
            let cg = Cover.trim(raw); image = NSImage(cgImage: cg, size: .zero); cgImage = cg; (palette, stats) = Cover.read(cg)
        }
    }

    /// video thumbnails come square with the 16:9 frame letterboxed in black: cut the bars off, so the cover on the glass is
    /// the picture and the backdrop's colour field isn't a third black. a 48-row probe, once per song; the file stays as it is
    static func trim(_ cg: CGImage) -> CGImage {
        let w = 16, h = 48
        var px = [UInt8](repeating: 0, count: w * h * 4)
        guard let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                                  space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return cg }
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
        // a bar row is near-black all the way across (memory row 0 is the image's top)
        func bar(_ y: Int) -> Bool { (0..<w).allSatisfy { x in let i = (y * w + x) * 4; return max(px[i], px[i + 1], px[i + 2]) < 14 } }
        var top = 0, bot = 0
        while top < h / 2 && bar(top) { top += 1 }
        while bot < h / 2 && bar(h - 1 - bot) { bot += 1 }
        guard top >= 3, bot >= 3, abs(top - bot) <= 2 else { return cg }     // only real, even letterboxing
        // and only bars around a 16:9 frame: the square music thumbnail (27 of 48 rows left) or the 4:3 video one (hqdefault,
        // 36 of 48). a dark album or a logo on black has even "bars" too, and would be cut to a sliver (10-23: 480×10)
        let kept = Double(cg.height) * Double(h - top - bot) / Double(h)
        guard kept >= 64, (1.6...2.0).contains(Double(cg.width) / kept) else { return cg }
        let k = CGFloat(cg.height) / CGFloat(h), y0 = (CGFloat(top) + 0.5) * k, y1 = CGFloat(cg.height) - (CGFloat(bot) + 0.5) * k
        return cg.cropping(to: CGRect(x: 0, y: y0, width: CGFloat(cg.width), height: y1 - y0).integral) ?? cg
    }

    static let night: [SIMD3<Float>] = [SIMD3(0.28, 0.70, 0.85), SIMD3(0.55, 0.42, 1.0), SIMD3(0.91, 0.56, 0.71)]

    private var asked = ""                          // the art url last tried for `vid`

    /// 0.8.1: radio rows had no art url at all, so most songs drew nothing.
    /// now: no url → the video's own thumbnail; a url that shows up later (titles.json caught up) gets one more go; between
    /// songs and on idle the last cover stays up; a miss is retried once, 5 s on, instead of never for that song
    func load(_ t: Track, retry: Bool = true) {
        let again = t.vid == vid && !t.art.isEmpty && t.art != asked
        guard t.vid != vid || again, !t.vid.isEmpty else { return }
        vid = t.vid; asked = t.art
        let dir = DATA.appendingPathComponent("art"); try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let f = dir.appendingPathComponent("\(t.vid).jpg")
        DispatchQueue.global(qos: .userInitiated).async {
            // a 10 s fetch (a stalled one never holds a worker for good), written whole: the terminal ui reads this folder too.
            // `again` replaces a stand-in thumbnail with the real cover; a failed upgrade keeps the file that's there
            let had = FileManager.default.fileExists(atPath: f.path)
            if again || !had {
                let d = (t.art.isEmpty ? nil : URL(string: t.art).flatMap(Cover.fetch))
                    ?? (had ? nil : URL(string: "https://i.ytimg.com/vi/\(t.vid)/hqdefault.jpg").flatMap(Cover.fetch))
                if let d { try? d.write(to: f, options: .atomic) }
            }
            let img = NSImage(contentsOf: f)
            var cg: CGImage? = nil
            if let img { var r = CGRect(origin: .zero, size: img.size); cg = img.cgImage(forProposedRect: &r, context: nil, hints: nil).map(Cover.trim) }
            let read = cg.map(Cover.read)
            let shown = cg.map { NSImage(cgImage: $0, size: .zero) } ?? img
            DispatchQueue.main.async {
                guard self.vid == t.vid else { return }
                guard shown != nil else {
                    // nothing at all (offline, a 404): the placeholder, and one more try in 5 s if this is still the song
                    if !again { self.image = nil; self.cgImage = nil; self.palette = Cover.night; self.stats = nil; self.loadedVid = t.vid; self.ready += 1 }
                    if retry { DispatchQueue.main.asyncAfter(deadline: .now() + 5) { if self.vid == t.vid && self.cgImage == nil { self.vid = ""; self.load(t, retry: false) } } }
                    return
                }
                self.image = shown; self.cgImage = cg; self.palette = read?.0 ?? Cover.night; self.stats = read?.1
                self.loadedVid = t.vid; self.ready += 1
            }
        }
    }

    private static let session: URLSession = {
        let c = URLSessionConfiguration.ephemeral; c.timeoutIntervalForRequest = 10; c.timeoutIntervalForResource = 15
        c.urlCache = nil; return URLSession(configuration: c)
    }()

    /// one try, no retries: a miss just leaves the night palette until the next song
    static func fetch(_ u: URL) -> Data? {
        var out: Data?; let done = DispatchSemaphore(value: 0)
        session.dataTask(with: u) { d, r, _ in
            if let d, (r as? HTTPURLResponse)?.statusCode == 200, d.count < 8 << 20 { out = d }
            done.signal()
        }.resume()
        _ = done.wait(timeout: .now() + 16)
        return out
    }

    static func palette(_ cg: CGImage) -> [SIMD3<Float>] { read(cg).0 }

    /// three colours: the most common colourful one, a second one with a different hue, and the brightest spark,
    /// each lifted so it glows on a dark stage. and, from the same 32×32 pass, the numbers the auto look reads
    /// (mirrors cover_signals in ytm/lib/autolook.py)
    static func read(_ cg: CGImage) -> ([SIMD3<Float>], CoverStats?) {
        let w = 32, h = 32
        var px = [UInt8](repeating: 0, count: w * h * 4)
        guard let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                                  space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return (night, nil) }
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
        func hsv(_ c: SIMD3<Float>) -> (h: Float, s: Float, v: Float) {
            let mx = max(c.x, c.y, c.z), mn = min(c.x, c.y, c.z), d = mx - mn
            var h: Float = 0
            if d > 0 { h = mx == c.x ? (c.y - c.z) / d : (mx == c.y ? 2 + (c.z - c.x) / d : 4 + (c.x - c.y) / d); h = (h / 6).truncatingRemainder(dividingBy: 1); if h < 0 { h += 1 } }
            return (h, mx > 0 ? d / mx : 0, mx)
        }
        var buckets: [Int: (n: Int, r: Float, g: Float, b: Float)] = [:]
        var st = CoverStats(), col = 0, warm = 0, cool = 0
        for i in stride(from: 0, to: px.count, by: 4) {
            let r = Float(px[i]) / 255, g = Float(px[i + 1]) / 255, b = Float(px[i + 2]) / 255
            let key = (Int(r * 5) << 6) | (Int(g * 5) << 3) | Int(b * 5)
            var e = buckets[key] ?? (0, 0, 0, 0); e.n += 1; e.r += r; e.g += g; e.b += b; buckets[key] = e
            let (hu, sa, va) = hsv(SIMD3(r, g, b)), l = Double(0.2126 * r + 0.7152 * g + 0.0722 * b)
            st.lum += l; st.sat += Double(sa); if l < 0.15 { st.dark += 1 }
            if sa > 0.25 { col += 1; if hu < 0.17 || hu > 0.9 { warm += 1 }; if hu > 0.45 && hu < 0.75 { cool += 1 } }
            if sa > 0.55 && va > 0.6 && ((hu > 0.75 && hu < 0.95) || (hu > 0.45 && hu < 0.55)) { st.neonpx += 1 }   // magenta, cyan
        }
        let n = Double(w * h)
        st.lum /= n; st.sat /= n; st.dark /= n; st.neonpx /= n; st.colourful = Double(col) / n
        st.warm = col > 0 ? Double(warm) / Double(col) : 0.5; st.cool = col > 0 ? Double(cool) / Double(col) : 0.5
        func lift(_ c: SIMD3<Float>, _ smin: Float = 0.45, _ vmin: Float = 0.85) -> SIMD3<Float> {
            let (h, s, v) = hsv(c); let S = max(s, smin), V = max(v, vmin)
            let i = Int(h * 6) % 6, f = h * 6 - Float(Int(h * 6)), p = V * (1 - S), q = V * (1 - f * S), t = V * (1 - (1 - f) * S)
            switch i { case 0: return SIMD3(V, t, p); case 1: return SIMD3(q, V, p); case 2: return SIMD3(p, V, t)
                       case 3: return SIMD3(p, q, V); case 4: return SIMD3(t, p, V); default: return SIMD3(V, p, q) }
        }
        let cols = buckets.values.map { (n: $0.n, c: SIMD3($0.r, $0.g, $0.b) / Float($0.n)) }
        guard !cols.isEmpty else { return (night, st) }
        let ranked = cols.sorted { Float($0.n) * (0.25 + hsv($0.c).s) > Float($1.n) * (0.25 + hsv($1.c).s) }
        let main = ranked[0].c
        let second = ranked.dropFirst().first { abs(hsv($0.c).h - hsv(main).h) > 0.08 }?.c ?? (ranked.count > 1 ? ranked[1].c : main)
        let spark = cols.max { hsv($0.c).v * (0.4 + hsv($0.c).s) < hsv($1.c).v * (0.4 + hsv($1.c).s) }!.c
        return ([lift(main), lift(second), lift(spark, 0.35, 0.95)], st)
    }
}
