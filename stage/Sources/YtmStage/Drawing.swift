// drawing kit: small premultiplied bitmaps, a deterministic rng, noise, blur. what every look paints with.
import AppKit
import QuartzCore
import Accelerate

// ---- pre-rendering: core graphics into small bitmaps, vImage for blur. soft things small; core animation scales them ----

struct PlateKey: Hashable { let vid: String; let look: Look; let accent: String }

/// one look's stills. base fills the window; a/b/c are tiles or sprites, depending on the look
final class Plates {
    var base: CGImage?, a: CGImage?, b: CGImage?, c: CGImage?
    var bytes: Int { [base, a, b, c].reduce(0) { $0 + ($1.map { $0.bytesPerRow * $0.height } ?? 0) } }
}

/// a deterministic little rng (splitmix64): the same song draws the same dust every time
struct Rng {
    var s: UInt64
    init(_ seed: String) { var h: UInt64 = 1469598103934665603; for b in seed.utf8 { h = (h ^ UInt64(b)) &* 1099511628211 }; s = h }
    mutating func next() -> UInt64 { s &+= 0x9E3779B97F4A7C15; var z = s; z = (z ^ (z >> 30)) &* 0xBF58476D1CE4E5B9; z = (z ^ (z >> 27)) &* 0x94D049BB133111EB; return z ^ (z >> 31) }
    mutating func f(_ lo: CGFloat = 0, _ hi: CGFloat = 1) -> CGFloat { lo + (hi - lo) * CGFloat(Double(next() >> 11) / Double(1 << 53)) }
}

let sRGB = CGColorSpace(name: CGColorSpace.sRGB)!

func cgc(_ c: SIMD3<Float>, _ a: CGFloat = 1, _ k: Float = 1) -> CGColor {
    CGColor(colorSpace: sRGB, components: [CGFloat(min(1, c.x * k)), CGFloat(min(1, c.y * k)), CGFloat(min(1, c.z * k)), a])!
}
func mix(_ a: SIMD3<Float>, _ b: SIMD3<Float>, _ t: Float) -> SIMD3<Float> { a + (b - a) * t }
let white = SIMD3<Float>(1, 1, 1)

/// a premultiplied bgra bitmap: what core animation takes without converting
final class Canvas {
    let w: Int, h: Int, ctx: CGContext
    init(_ w: Int, _ h: Int) {
        self.w = w; self.h = h
        ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: sRGB,
                        bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)!
        ctx.interpolationQuality = .medium
    }
    func image() -> CGImage? { ctx.makeImage() }
    func fill(_ c: CGColor) { ctx.setFillColor(c); ctx.fill(CGRect(x: 0, y: 0, width: w, height: h)) }

    /// three box passes ≈ a gaussian. channel order doesn't matter to vImage, and premultiplied pixels blur correctly
    func blur(_ r: Int) {
        guard r > 0, let data = ctx.data else { return }
        let k = UInt32(r * 2 + 1)
        var src = vImage_Buffer(data: data, height: vImagePixelCount(h), width: vImagePixelCount(w), rowBytes: ctx.bytesPerRow)
        guard let tmp = malloc(ctx.bytesPerRow * h) else { return }
        defer { free(tmp) }
        var dst = vImage_Buffer(data: tmp, height: src.height, width: src.width, rowBytes: ctx.bytesPerRow)
        for _ in 0..<3 {
            vImageBoxConvolve_ARGB8888(&src, &dst, nil, 0, 0, k, k, nil, vImage_Flags(kvImageEdgeExtend))
            swap(&src.data, &dst.data)
        }
        if src.data != data { memcpy(data, src.data, ctx.bytesPerRow * h) }
    }

    /// a soft disc: colour at the centre, nothing at the edge
    func glow(_ p: CGPoint, _ r: CGFloat, _ c: SIMD3<Float>, _ a: CGFloat, hard: CGFloat = 0) {
        let g = CGGradient(colorsSpace: sRGB, colors: [cgc(c, a), cgc(c, a), cgc(c, 0)] as CFArray, locations: [0, hard, 1])!
        ctx.drawRadialGradient(g, startCenter: p, startRadius: 0, endCenter: p, endRadius: r, options: [])
    }

    /// draws something once per copy that touches the tile, so it wraps seamlessly at every edge
    func wrapped(_ p: CGPoint, _ reach: CGFloat, _ draw: (CGPoint) -> Void) {
        for dx in [-1, 0, 1] { for dy in [-1, 0, 1] {
            let q = CGPoint(x: p.x + CGFloat(dx * w), y: p.y + CGFloat(dy * h))
            if q.x + reach < 0 || q.x - reach > CGFloat(w) || q.y + reach < 0 || q.y - reach > CGFloat(h) { continue }
            draw(q)
        } }
    }

    /// the cover, aspect-filled and slightly zoomed
    func cover(_ img: CGImage?, zoom: CGFloat = 1.15, alpha: CGFloat = 1) {
        guard let img else { return }
        let s = max(CGFloat(w) / CGFloat(img.width), CGFloat(h) / CGFloat(img.height)) * zoom
        let iw = CGFloat(img.width) * s, ih = CGFloat(img.height) * s
        ctx.saveGState(); ctx.setAlpha(alpha)
        ctx.draw(img, in: CGRect(x: (CGFloat(w) - iw) / 2, y: (CGFloat(h) - ih) / 2, width: iw, height: ih))
        ctx.restoreGState()
    }

    /// every pixel through f(r, g, b) → (r, g, b), alpha kept. small canvases only (this is the cpu, once per song)
    func map(_ f: (SIMD3<Float>) -> SIMD3<Float>) {
        guard let d = ctx.data?.assumingMemoryBound(to: UInt8.self) else { return }
        for i in stride(from: 0, to: w * h * 4, by: 4) {
            let c = f(SIMD3(Float(d[i + 2]), Float(d[i + 1]), Float(d[i])) / 255)
            d[i + 2] = UInt8(max(0, min(255, c.x * 255))); d[i + 1] = UInt8(max(0, min(255, c.y * 255))); d[i] = UInt8(max(0, min(255, c.z * 255)))
        }
    }
}

func lum(_ c: SIMD3<Float>) -> Float { 0.2126 * c.x + 0.7152 * c.y + 0.0722 * c.z }
func smooth(_ a: Float, _ b: Float, _ x: Float) -> Float { let t = max(0, min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t) }

/// value noise + fbm, for the stargaze nebula (160×100 once per song: ~0.1 ms of maths, not 60 frames a second of it)
enum Noise {
    static func hash(_ x: Int, _ y: Int, _ s: Int) -> Float {
        var h = UInt32(truncatingIfNeeded: x &* 374761393 &+ y &* 668265263 &+ s &* 982451653)
        h = (h ^ (h >> 13)) &* 1274126177; h ^= h >> 16
        return Float(h & 0xffff) / 65535
    }
    static func value(_ x: Float, _ y: Float, _ s: Int) -> Float {
        let ix = Int(floor(x)), iy = Int(floor(y)), fx = x - floor(x), fy = y - floor(y)
        let ux = fx * fx * (3 - 2 * fx), uy = fy * fy * (3 - 2 * fy)
        let a = hash(ix, iy, s), b = hash(ix + 1, iy, s), c = hash(ix, iy + 1, s), d = hash(ix + 1, iy + 1, s)
        return (a + (b - a) * ux) + ((c + (d - c) * ux) - (a + (b - a) * ux)) * uy
    }
    static func fbm(_ x: Float, _ y: Float, _ s: Int) -> Float {
        var v: Float = 0, amp: Float = 0.5, fx = x, fy = y
        for o in 0..<5 { v += amp * value(fx, fy, s + o * 17); fx = fx * 2.03 + 1.7; fy = fy * 2.03 + 9.2; amp *= 0.5 }
        return v / 0.97
    }
}
