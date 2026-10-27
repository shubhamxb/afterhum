// the "ember" look: a banked flame at the bottom of the room. warm, slow, one thing moves: sparks rising out of the glow.
// the flicker is two glows on irregular opacity keyframes (no per-frame work); the sparks are two scrolling tiles.
import AppKit
import QuartzCore

extension Painter {
    static func ember(_ out: Plates, cover: CGImage?, P: [SIMD3<Float>], accent: String, rng: inout Rng) {
        let flame = mix(P[0], SIMD3(1, 0.46, 0.14), 0.62), hot = mix(P[2], SIMD3(1, 0.78, 0.4), 0.55)
        let c = Canvas(192, 120)
        c.fill(cgc(SIMD3(0.03, 0.017, 0.016)))
        if let cover { c.cover(cover, zoom: 1.4, alpha: 0.12); c.blur(8) }
        c.ctx.setBlendMode(.screen)
        c.ctx.saveGState(); c.ctx.scaleBy(x: 1, y: 0.55)                                     // a hearth is wide and low, not round
        c.glow(CGPoint(x: 96, y: 4), 120, flame, 0.55); c.glow(CGPoint(x: 96, y: 0), 70, hot, 0.5)
        c.ctx.restoreGState()
        c.glow(CGPoint(x: 28, y: 14), 60, mix(flame, P[1], 0.3), 0.16); c.glow(CGPoint(x: 168, y: 20), 60, mix(flame, P[1], 0.3), 0.12)
        c.ctx.setBlendMode(.normal)
        c.blur(5)
        out.base = c.image()
        // sparks: small hot points with a short vertical streak, plus a few out-of-focus ones close to the glass
        let s = Canvas(512, 512)
        for _ in 0..<34 {
            let p = CGPoint(x: rng.f(0, 512), y: rng.f(0, 512)), r = rng.f(0.9, 2.1), len = rng.f(3, 12), a = rng.f(0.45, 0.95)
            s.wrapped(p, len + r * 4) { q in
                s.glow(q, r * 4.5, flame, a * 0.22)
                s.ctx.setFillColor(cgc(mix(hot, white, 0.35), a)); s.ctx.fillEllipse(in: CGRect(x: q.x - r * 0.6, y: q.y - r * 0.6 - len * 0.3, width: r * 1.2, height: r * 1.2 + len * 0.5))
            }
        }
        out.a = s.image()
        out.b = dust(384, 9, 3.5...7.0, 0.1...0.24, mix(flame, white, 0.2), &rng, soft: true)
    }
}

extension Builder {
    static func ember(_ c: Ctx) {
        let T = c.T, t = T.root, pl = c.pl, P = c.P, size = c.size, o = c.o
        let W = size.width, H = size.height, fps = o.fps, g = CGFloat(o.glow)
        func dur(_ s: Double) -> Double { o.motion > 0 ? s / o.motion : 0 }
        let flame = mix(P[0], SIMD3(1, 0.46, 0.14), 0.62), hot = mix(P[2], SIMD3(1, 0.78, 0.4), 0.55)
        t.addSublayer(T.glow)
        let g0 = glow(flame, 0.5 * g, H * 1.5, at: CGPoint(x: W * 0.5, y: -H * 0.05)), g1 = glow(hot, 0.34 * g, H * 0.8, at: CGPoint(x: W * 0.46, y: -H * 0.02))
        T.breath.addSublayer(g0); T.breath.addSublayer(g1)
        if o.motion > 0 {   // a flame never breathes evenly: irregular keyframes, two periods that don't line up
            for (l, d, v) in [(g0, 4.3, [0.82, 1, 0.72, 0.95, 0.78, 1, 0.86] as [Float]), (g1, 2.9, [0.7, 1, 0.6, 0.9, 0.55, 1] as [Float])] {
                let a = CAKeyframeAnimation(keyPath: "opacity"); a.values = v
                a.keyTimes = (0..<v.count).map { NSNumber(value: Double($0) / Double(v.count - 1)) }
                a.duration = dur(d); a.repeatCount = .infinity; a.calculationMode = .cubic; a.preferredFrameRateRange = Builder.fps(min(fps, 24))
                l.add(a, forKey: "flicker")
            }
        }
        if o.texture {
            let far = tiles(pl.b, 384, cover: size); far.opacity = 0.7; t.addSublayer(far)
            let sparks = tiles(pl.a, 512, cover: size); t.addSublayer(sparks)
            scroll(far, dx: 0, dy: 384, dur(70), fps); scroll(sparks, dx: 0, dy: 512, dur(34), fps)   // up, the near sparks faster
        }
        t.addSublayer(vignette(size, 0.6))
        T.gesture = { drop, s, at in   // a moment: the flame catches
            let flare = glow(hot, 0.7 * s, H * (drop ? 1.5 : 1.0), at: CGPoint(x: W * 0.48, y: H * 0.02)); flare.opacity = 0
            fire(t, flare, opacity: [0, 1, 0.45, 0], times: [0, 0.12, 0.4, 1], dur: drop ? 2.6 : 1.8, max(fps, 24))
        }
    }
}
