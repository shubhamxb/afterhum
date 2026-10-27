// the "aurora" look: slow curtains of light over a still night sky. calm, cool, nothing sharp: a look for focus and late hours.
// two translucent curtain sprites drift against each other and breathe; a faint star tile sits under them.
import AppKit
import QuartzCore

extension Painter {
    /// a curtain: a row of soft vertical streaks whose height and brightness wander (sine sums + fbm), in the palette colour `col`
    private static func curtain(_ w: Int, _ h: Int, _ col: SIMD3<Float>, _ edge: SIMD3<Float>, seed: Int, rng: inout Rng) -> CGImage? {
        let c = Canvas(w, h)
        let p1 = Float(rng.f(0, 6.28)), p2 = Float(rng.f(0, 6.28)), p3 = Float(rng.f(0, 6.28))
        c.ctx.setBlendMode(.screen)
        for x in 0..<w {
            let fx = Float(x)
            let top = Float(h) * (0.62 + 0.10 * sin(fx * 0.013 + p1) + 0.05 * sin(fx * 0.041 + p2))     // where the curtain's hem hangs (y up)
            let len = Float(h) * (0.34 + 0.12 * sin(fx * 0.021 + p3) + 0.06 * sin(fx * 0.07 + p1))
            let n = Noise.fbm(fx * 0.012, 3.5, seed)
            let rays = 0.6 + 0.4 * Noise.fbm(fx * 0.16, 9.5, seed + 3)           // fine vertical rays inside the broad swell
            let a = CGFloat((0.2 + 0.7 * smooth(0.25, 0.75, n)) * rays)
            let g = CGGradient(colorsSpace: sRGB, colors: [cgc(edge, 0), cgc(col, a), cgc(mix(col, edge, 0.5), a * 0.35), cgc(edge, 0)] as CFArray,
                               locations: [0, 0.12, 0.55, 1])!
            c.ctx.saveGState(); c.ctx.clip(to: CGRect(x: x, y: 0, width: 1, height: h))
            c.ctx.drawLinearGradient(g, start: CGPoint(x: 0, y: CGFloat(top)), end: CGPoint(x: 0, y: CGFloat(top - len)), options: [])
            c.ctx.restoreGState()
        }
        c.ctx.setBlendMode(.normal)
        c.blur(2)
        return c.image()
    }

    static func aurora(_ out: Plates, cover: CGImage?, P: [SIMD3<Float>], accent: String, rng: inout Rng) {
        // the sky: deep, slightly lifted at the horizon, a breath of the palette; the cover only tints it
        let c = Canvas(192, 120)
        let top = SIMD3<Float>(0.008, 0.012, 0.03), low = mix(SIMD3(0.02, 0.05, 0.07), P[1], 0.14)
        let sky = CGGradient(colorsSpace: sRGB, colors: [cgc(top), cgc(mix(top, low, 0.5)), cgc(low)] as CFArray, locations: [0, 0.55, 1])!
        c.ctx.drawLinearGradient(sky, start: CGPoint(x: 0, y: 120), end: .zero, options: [])
        if let cover { c.cover(cover, zoom: 1.5, alpha: 0.08); c.blur(8) }
        // a low ridge of hills: the sky needs a floor, or the curtains float
        c.ctx.setFillColor(cgc(SIMD3(0.004, 0.008, 0.014)))
        let ridge = CGMutablePath(); ridge.move(to: .zero)
        for x in stride(from: 0, through: 192, by: 4) {
            let xf = Float(x)
            let wob: Float = sin(xf * 0.05 + 1) + 0.6 * sin(xf * 0.13)
            ridge.addLine(to: CGPoint(x: CGFloat(x), y: 11 + 6 * CGFloat(wob)))
        }
        ridge.addLine(to: CGPoint(x: 192, y: 0)); ridge.closeSubpath(); c.ctx.addPath(ridge); c.ctx.fillPath()
        c.blur(1)
        out.base = c.image()
        // an aurora is green and violet whatever the cover: the palette only leans them
        let green = mix(P[0], SIMD3(0.25, 1, 0.62), 0.78), violet = mix(P[1], SIMD3(0.58, 0.34, 1), 0.72)
        out.a = curtain(640, 280, green, mix(green, SIMD3(0, 0.05, 0.1), 0.8), seed: Int(rng.next() & 0xffff), rng: &rng)
        out.b = curtain(512, 240, violet, mix(violet, SIMD3(0, 0.02, 0.08), 0.8), seed: Int(rng.next() & 0xffff), rng: &rng)
        out.c = starTile(512, 70, 0.4...0.95, 0.2...0.6, P, &rng)
    }
}

extension Builder {
    static func aurora(_ c: Ctx) {
        let T = c.T, t = T.root, pl = c.pl, P = c.P, size = c.size, o = c.o
        let W = size.width, H = size.height, fps = o.fps, g = CGFloat(o.glow)
        func dur(_ s: Double) -> Double { o.motion > 0 ? s / o.motion : 0 }
        if o.texture { let stars = tiles(pl.c, 512, cover: size); stars.opacity = 0.7; t.addSublayer(stars) }
        t.addSublayer(T.glow)
        T.breath.addSublayer(glow(mix(P[0], SIMD3(0.25, 1, 0.62), 0.7), 0.22 * g, H * 1.3, at: CGPoint(x: W * 0.4, y: H * 0.78)))
        // the curtains: wider than the window, so a drift never shows an edge. the far one is violet, higher and slower
        func sprite(_ img: CGImage?, _ w: CGFloat, _ h: CGFloat, _ x: CGFloat, _ y: CGFloat, _ op: Float) -> CALayer {
            let l = CALayer(); l.contents = img; l.bounds = CGRect(x: 0, y: 0, width: w, height: h); l.position = CGPoint(x: x, y: y)
            l.opacity = op; l.magnificationFilter = .linear; l.compositingFilter = "screenBlendMode"; return l
        }
        let far = sprite(pl.b, W * 1.5, H * 0.85, W * 0.55, H * 0.66, 0.8), near = sprite(pl.a, W * 1.7, H * 1.0, W * 0.45, H * 0.6, 0.95)
        t.addSublayer(far); t.addSublayer(near)
        drift(far, rx: W * 0.07, ry: H * 0.02, dur(110), phase: 0.3, fps); drift(near, rx: W * 0.09, ry: H * 0.025, dur(80), phase: 0, fps)
        if o.motion > 0 {   // the light swells and fades, never all at once
            for (l, d, lo) in [(near, 9.0, 0.55), (far, 13.0, 0.5)] as [(CALayer, Double, Float)] {
                let a = CABasicAnimation(keyPath: "opacity"); a.fromValue = lo; a.toValue = l.opacity; a.duration = dur(d)
                a.autoreverses = true; a.repeatCount = .infinity; a.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
                a.preferredFrameRateRange = Builder.fps(min(fps, 15)); l.add(a, forKey: "swell")
            }
        }
        t.addSublayer(vignette(size, 0.5))
        T.gesture = { drop, s, at in   // a moment: the curtains flare, a wash of light rolls across the top
            let bloom = glow(mix(P[2], SIMD3(0.7, 1, 0.9), 0.5), 0.5 * s, H * (drop ? 1.4 : 1.0), at: CGPoint(x: at.x, y: H * 0.8)); bloom.opacity = 0
            fire(t, bloom, opacity: [0, 1, 0.5, 0], times: [0, 0.2, 0.5, 1], dur: drop ? 3.4 : 2.4, fps)
        }
    }
}
