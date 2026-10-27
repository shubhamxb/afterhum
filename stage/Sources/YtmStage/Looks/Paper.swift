// the "paper" look: what it paints (stills, once per song) and the layer tree core animation moves.
import AppKit
import QuartzCore

extension Painter {
    static func paper(_ out: Plates, cover: CGImage?, P: [SIMD3<Float>], accent: String, rng: inout Rng) {
        // warm dark paper: the palette as a faint wash, a static grain tile, nothing that pulls the eye
        let c = Canvas(192, 120)
        let warm = SIMD3<Float>(0.115, 0.098, 0.082)
        let g = CGGradient(colorsSpace: sRGB, colors: [cgc(mix(warm, P[0] * 0.4, 0.22)), cgc(warm), cgc(mix(warm, P[1] * 0.4, 0.16))] as CFArray, locations: [0, 0.55, 1])!
        c.ctx.drawLinearGradient(g, start: CGPoint(x: 0, y: 120), end: CGPoint(x: 192, y: 0), options: [])
        c.glow(CGPoint(x: 70, y: 80), 90, mix(P[2], SIMD3(1, 0.9, 0.75), 0.6), 0.05)
        c.blur(6)
        out.base = c.image()
        let t = Canvas(256, 256)
        if let d = t.ctx.data?.assumingMemoryBound(to: UInt8.self) {
            for i in stride(from: 0, to: 256 * 256 * 4, by: 4) {
                let v = UInt8(rng.next() & 0xff), a = UInt8(8 + (rng.next() % 9))       // 3–7 % alpha, premultiplied
                let p = UInt8(Int(v) * Int(a) / 255)
                d[i] = p; d[i + 1] = p; d[i + 2] = p; d[i + 3] = a
            }
        }
        out.a = t.image()
    }
}

extension Builder {
    static func paper(_ c: Ctx) {
        let T = c.T, t = c.T.root
        let pl = c.pl
        let P = c.P
        let size = c.size
        let o = c.o
        let W = c.size.width
        let H = c.size.height
        let fps = c.o.fps
        let g = CGFloat(c.o.glow)
        func dur(_ s: Double) -> Double { c.o.motion > 0 ? s / c.o.motion : 0 }
        t.addSublayer(T.glow)
        let light = glow(mix(P[2], SIMD3(1, 0.86, 0.68), 0.7), 0.15 * g, H * 1.4, at: CGPoint(x: W * 0.42, y: H * 0.6))
        T.breath.addSublayer(light)
        drift(light, rx: W * 0.22, ry: H * 0.1, dur(600), phase: 0.3, fps)
        if o.texture { t.addSublayer(tiles(pl.a, 256, cover: size)) }
        t.addSublayer(vignette(size, 0.4))
    }
}
