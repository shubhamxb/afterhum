// the "stars" look: what it paints (stills, once per song) and the layer tree core animation moves.
import AppKit
import QuartzCore

extension Painter {
    static func stars(_ out: Plates, cover: CGImage?, P: [SIMD3<Float>], accent: String, rng: inout Rng) {
        // the nebula: fbm in palette colours on a tiny bitmap; core animation's upscale is the last blur it needs
        let w = 160, h = 100, c = Canvas(w, h)
        let s = Int(rng.next() & 0xffff)
        if let d = c.ctx.data?.assumingMemoryBound(to: UInt8.self) {
            let base = SIMD3<Float>(0.008, 0.01, 0.028)
            for yy in 0..<h { for xx in 0..<w {
                let x = Float(xx) / Float(h) * 2.2, y = Float(yy) / Float(h) * 2.2
                let n = Noise.fbm(x + 3, y, s), m = Noise.fbm(x * 0.7 + 11, y * 0.7, s + 5)
                let band = 1 - min(1, abs(Float(yy) / Float(h) - 0.35 - 0.25 * Float(xx) / Float(w)) * 2.2)   // a milky way, diagonal
                let dens = smooth(0.38, 0.85, n * (0.65 + 0.55 * band))
                // two clouds of colour, not one grey average of them: the second colour takes over in a narrow seam
                var col = base + mix(P[0], P[1], smooth(0.4, 0.6, m)) * dens * 0.6 + P[2] * pow(smooth(0.6, 0.92, n), 2) * 0.26
                col *= 0.9 + 0.2 * band
                let l = lum(col); col = pointwiseMax(SIMD3(repeating: 0), mix(SIMD3(repeating: l), col, 1.5))
                let i = (yy * w + xx) * 4
                d[i] = UInt8(min(255, col.z * 255)); d[i + 1] = UInt8(min(255, col.y * 255)); d[i + 2] = UInt8(min(255, col.x * 255)); d[i + 3] = 255
            } }
        }
        c.blur(1)
        out.base = c.image()
        out.a = starTile(512, 120, 0.45...1.15, 0.3...0.85, P, &rng)
        out.b = starTile(512, 16, 1.0...1.8, 0.7...1.0, P, &rng, halo: true)
        // the shooting star: a tail fading into a bright head
        let sh = Canvas(320, 12)
        let tg = CGGradient(colorsSpace: sRGB, colors: [cgc(mix(P[2], white, 0.6), 0), cgc(mix(P[2], white, 0.7), 0.85)] as CFArray, locations: [0, 1])!
        sh.ctx.saveGState(); sh.ctx.clip(to: CGRect(x: 0, y: 5, width: 314, height: 2))
        sh.ctx.drawLinearGradient(tg, start: .zero, end: CGPoint(x: 314, y: 0), options: []); sh.ctx.restoreGState()
        sh.glow(CGPoint(x: 312, y: 6), 6, white, 1, hard: 0.25)
        out.c = sh.image()
    }
}

extension Builder {
    static func stars(_ c: Ctx) {
        let T = c.T, t = c.T.root
        let pl = c.pl
        let P = c.P
        let size = c.size
        let o = c.o
        let W = c.size.width
        let H = c.size.height
        let m = c.o.motion
        let fps = c.o.fps
        let g = CGFloat(c.o.glow)
        func dur(_ s: Double) -> Double { c.o.motion > 0 ? s / c.o.motion : 0 }
        // the sky turns about a pole off the top right: one big rotating container of star tiles
        let pole = CGPoint(x: W * 0.86, y: H * 1.45)
        let R = hypot(pole.x, pole.y) + 64
        if o.texture {
            let sky = CALayer(); sky.bounds = CGRect(x: 0, y: 0, width: R * 2, height: R * 2); sky.position = pole
            let field = tiles(pl.a, 512, cover: sky.bounds.size); sky.addSublayer(field)
            t.addSublayer(sky)
            let twinkle = CALayer(); twinkle.bounds = sky.bounds; twinkle.position = pole
            twinkle.addSublayer(tiles(pl.b, 512, cover: sky.bounds.size))
            T.breath.addSublayer(twinkle)
            for l in [sky, twinkle] {
                let r = CABasicAnimation(keyPath: "transform.rotation.z"); r.fromValue = 0; r.toValue = -2 * Double.pi
                r.duration = dur(1800); r.repeatCount = .infinity; r.preferredFrameRateRange = Builder.fps(min(fps, 15))
                if m > 0 { l.add(r, forKey: "turn") }
            }
        }
        let neb = glow(P[1], 0.16 * g, H * 1.2, at: CGPoint(x: W * 0.3, y: H * 0.6))
        T.breath.addSublayer(neb)
        t.addSublayer(T.glow)
        t.addSublayer(vignette(size, 0.5))
        T.gesture = { drop, s, _ in
            let st = CALayer(); st.contents = pl.c; st.bounds = CGRect(x: 0, y: 0, width: drop ? 340 : 220, height: 6)
            st.anchorPoint = CGPoint(x: 1, y: 0.5)
            let a = CGPoint(x: W * 0.12, y: H * 0.86), b = CGPoint(x: W * (drop ? 0.62 : 0.42), y: H * (drop ? 0.52 : 0.68))
            st.position = a; st.transform = CATransform3DMakeRotation(atan2(b.y - a.y, b.x - a.x), 0, 0, 1)
            st.opacity = 0
            let mv = CABasicAnimation(keyPath: "position"); mv.fromValue = a; mv.toValue = b
            mv.timingFunction = CAMediaTimingFunction(name: .easeIn)
            fire(t, st, opacity: [0, Float(min(1, 0.5 + s)), Float(min(1, 0.5 + s)), 0], times: [0, 0.1, 0.75, 1],
                 dur: drop ? 1.3 : 0.9, max(fps, 30), extra: [mv])
        }
    }
}
