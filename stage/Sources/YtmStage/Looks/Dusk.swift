// the "dusk" look: what it paints (stills, once per song) and the layer tree core animation moves.
import AppKit
import QuartzCore

extension Painter {
    static func dusk(_ out: Plates, cover: CGImage?, P: [SIMD3<Float>], accent: String, rng: inout Rng) {
        let recolour = accent != "cover"
        // the cover as a soft colour field (recoloured through the accent when one is chosen). 10-23 evening: it read as
        // dark blue with stars, too close to stargaze. now the cover's own colours carry it: less darkening, more
        // saturation, and the palette pooled in three soft lights so even a dark cover leaves colour on the stage
        let c = Canvas(192, 120)
        let dark = SIMD3<Float>(0.025, 0.022, 0.05)
        c.fill(cgc(dark))
        if cover != nil { c.cover(cover, zoom: 1.05) } else { c.glow(CGPoint(x: 50, y: 70), 120, P[0], 1); c.glow(CGPoint(x: 150, y: 40), 110, P[1], 1) }
        c.blur(7)
        c.map { px in
            var v = px
            if recolour { let l = lum(px); v = l < 0.5 ? mix(dark, P[1] * 0.8, l * 2) : mix(P[1] * 0.8, P[0], (l - 0.5) * 2) }
            let l = lum(v); v = mix(SIMD3(repeating: l), v, 1.7)              // the cover's colour, louder than the cover
            v = SIMD3(pow(max(0, v.x), 0.85), pow(max(0, v.y), 0.85), pow(max(0, v.z), 0.85))   // lift the shadows a touch
            return v * 0.62 + dark * 0.5
        }
        c.ctx.setBlendMode(.screen)
        c.glow(CGPoint(x: 40, y: 30), 95, P[2], 0.32)       // a warm pool low left (the spark)
        c.glow(CGPoint(x: 150, y: 92), 90, P[1], 0.26)      // the second colour high right
        c.glow(CGPoint(x: 120, y: 20), 80, P[0], 0.22)
        c.ctx.setBlendMode(.normal)
        c.blur(4)
        out.base = c.image()
        // dust in the beam: soft warm motes, out of focus (bokeh, not stars). few near, a few more far, different tile
        // sizes so the repeat never lines up
        out.a = dust(512, 9, 4.5...8.0, 0.16...0.32, mix(P[2], white, 0.35), &rng, soft: true)
        out.b = dust(384, 20, 1.4...2.4, 0.12...0.26, mix(P[0], white, 0.5), &rng, soft: true)
    }
}

extension Builder {
    static func dusk(_ c: Ctx) {
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
        let g0 = glow(P[0], 0.5 * g, H * 1.3, at: CGPoint(x: W * 0.3, y: H * 0.6))
        let g1 = glow(P[1], 0.44 * g, H * 1.15, at: CGPoint(x: W * 0.74, y: H * 0.36))
        let g2 = glow(P[2], 0.26 * g, H * 0.9, at: CGPoint(x: W * 0.55, y: H * 0.12))
        T.breath.addSublayer(g0); T.breath.addSublayer(g1); T.breath.addSublayer(g2)
        drift(g0, rx: W * 0.16, ry: H * 0.12, dur(70), phase: 0, fps); drift(g1, rx: W * 0.14, ry: H * 0.15, dur(95), phase: 0.5, fps)
        drift(g2, rx: W * 0.2, ry: H * 0.05, dur(130), phase: 0.25, fps)
        if o.texture {
            let far = tiles(pl.b, 384, cover: size); far.opacity = 0.7; t.addSublayer(far)
            let near = tiles(pl.a, 512, cover: size); t.addSublayer(near)
            scroll(far, dx: 0, dy: 384, dur(120), fps); scroll(near, dx: 0, dy: 512, dur(80), fps)   // up, the far dust slower
        }
        t.addSublayer(vignette(size, 0.5))
        T.gesture = { drop, s, at in
            let ring = CAShapeLayer(), r: CGFloat = 175
            ring.path = CGPath(ellipseIn: CGRect(x: -r, y: -r, width: r * 2, height: r * 2), transform: nil)
            ring.position = at; ring.fillColor = nil; ring.strokeColor = cgc(mix(P[2], white, 0.25), 0.55); ring.lineWidth = 1.6
            ring.shadowColor = cgc(P[2]); ring.shadowOpacity = 0.8; ring.shadowRadius = 14; ring.shadowOffset = .zero   // a soft ring, not a boom
            ring.compositingFilter = "screenBlendMode"
            let bloom = glow(P[2], 0.55 * s, H * (drop ? 1.3 : 0.9), at: at)
            bloom.opacity = 0
            fire(t, bloom, opacity: [0, 1, 0.55, 0], times: [0, 0.18, 0.45, 1], dur: drop ? 3.0 : 2.2, fps)
            if drop {
                ring.opacity = 0
                let sc = CABasicAnimation(keyPath: "transform.scale"); sc.fromValue = 1; sc.toValue = 3.6
                sc.timingFunction = CAMediaTimingFunction(controlPoints: 0.15, 0.6, 0.3, 1)
                fire(t, ring, opacity: [0, Float(0.75 * s), 0], times: [0, 0.1, 1], dur: 3.4, fps, extra: [sc])
            }
        }
    }
}
