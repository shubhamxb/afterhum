// the "tide" look: a night sea under a pale moon. two layers of wave glints slide past at different speeds, a path of light lies
// on the water below the moon and shimmers. the stage's coolest, quietest look.
import AppKit
import QuartzCore

extension Painter {
    static let tideHorizon: CGFloat = 0.38   // the sea's far edge, as a fraction of the height from the bottom
    static let moonX: CGFloat = 0.64         // the moon and its path, as a fraction of the width

    static func tide(_ out: Plates, cover: CGImage?, P: [SIMD3<Float>], accent: String, rng: inout Rng) {
        let c = Canvas(240, 150), hy = 150 * tideHorizon
        let skyTop = SIMD3<Float>(0.012, 0.02, 0.05), skyLow = mix(SIMD3(0.04, 0.07, 0.12), P[1], 0.2)
        let sky = CGGradient(colorsSpace: sRGB, colors: [cgc(skyTop), cgc(skyLow)] as CFArray, locations: [0, 1])!
        c.ctx.drawLinearGradient(sky, start: CGPoint(x: 0, y: 150), end: CGPoint(x: 0, y: hy), options: [.drawsAfterEndLocation])
        let sea = CGGradient(colorsSpace: sRGB, colors: [cgc(mix(SIMD3(0.025, 0.05, 0.085), P[0], 0.1)), cgc(SIMD3(0.006, 0.014, 0.03))] as CFArray, locations: [0, 1])!
        c.ctx.saveGState(); c.ctx.clip(to: CGRect(x: 0, y: 0, width: 240, height: hy))
        c.ctx.drawLinearGradient(sea, start: CGPoint(x: 0, y: hy), end: .zero, options: []); c.ctx.restoreGState()
        if let cover { c.cover(cover, zoom: 1.6, alpha: 0.07); c.blur(8) }
        c.ctx.setBlendMode(.screen)
        c.glow(CGPoint(x: 240 * moonX, y: hy + 36), 70, mix(SIMD3(0.7, 0.78, 1), P[2], 0.2), 0.2)      // the moon's halo on the sky
        c.glow(CGPoint(x: 240 * moonX, y: hy), 60, mix(SIMD3(0.7, 0.78, 1), P[1], 0.2), 0.2, hard: 0.1)   // and where it meets the sea
        c.ctx.setBlendMode(.normal)
        c.blur(2)
        out.base = c.image()
        // wave glints: short horizontal strokes of cool light, a seamless tile in x and y
        let t = Canvas(512, 512); t.ctx.setLineCap(.round)      // square: Builder.tiles lays square tiles
        for _ in 0..<420 {
            let len = rng.f(8, 46), p = CGPoint(x: rng.f(0, 512), y: rng.f(0, 512)), a = rng.f(0.1, 0.4)
            t.wrapped(p, len) { q in
                t.ctx.setStrokeColor(cgc(mix(SIMD3(0.6, 0.72, 0.95), P[0], 0.25), a)); t.ctx.setLineWidth(rng.f(0.8, 1.8))
                t.ctx.move(to: q); t.ctx.addLine(to: CGPoint(x: q.x + len, y: q.y + rng.f(-0.6, 0.6))); t.ctx.strokePath()
            }
        }
        t.blur(1)
        out.a = t.image()
        // the path of light: stacked soft glints, widest and brightest at the horizon, thinning toward the viewer
        let m = Canvas(64, 256)
        for i in 0..<90 {
            let f = CGFloat(i) / 90, y = 256 * (1 - pow(f, 0.8)), w = rng.f(3, 26) * (1.1 - f * 0.55), a = rng.f(0.12, 0.5) * (1 - f * 0.5)
            m.ctx.setFillColor(cgc(mix(SIMD3(0.82, 0.88, 1), white, 0.3), a))
            m.ctx.fillEllipse(in: CGRect(x: 32 - w / 2 + rng.f(-4, 4), y: y, width: w, height: rng.f(1.2, 3)))
        }
        m.blur(1)
        out.b = m.image()
        // the moon: a pale disc, a hair of warmth at its edge
        let d = Canvas(128, 128)
        d.glow(CGPoint(x: 64, y: 64), 58, SIMD3(0.7, 0.78, 1), 0.2, hard: 0.2)
        d.ctx.setFillColor(cgc(SIMD3(0.93, 0.95, 1), 0.95)); d.ctx.fillEllipse(in: CGRect(x: 40, y: 40, width: 48, height: 48))
        d.ctx.setFillColor(cgc(SIMD3(0.7, 0.74, 0.86), 0.28)); d.ctx.fillEllipse(in: CGRect(x: 54, y: 58, width: 11, height: 9))   // a sea on the moon
        d.ctx.fillEllipse(in: CGRect(x: 66, y: 48, width: 7, height: 6))
        d.blur(1)
        out.c = d.image()
    }
}

extension Builder {
    static func tide(_ c: Ctx) {
        let T = c.T, t = T.root, pl = c.pl, P = c.P, size = c.size, o = c.o
        let W = size.width, H = size.height, fps = o.fps, g = CGFloat(o.glow)
        func dur(_ s: Double) -> Double { o.motion > 0 ? s / o.motion : 0 }
        let hy = H * Painter.tideHorizon, mx = W * Painter.moonX, moonR = H * 0.046
        let moon = image(pl.c, CGRect(x: mx - moonR * 2, y: hy + H * 0.24 - moonR * 2, width: moonR * 4, height: moonR * 4), fill: false); moon.autoresizingMask = []
        t.addSublayer(moon)
        t.addSublayer(T.glow)
        let halo = glow(mix(SIMD3(0.7, 0.78, 1), P[2], 0.25), 0.2 * g, H * 1.0, at: CGPoint(x: mx, y: hy + H * 0.24)); T.breath.addSublayer(halo)
        let sea = CALayer(); sea.frame = CGRect(x: 0, y: 0, width: W, height: hy); sea.masksToBounds = true
        sea.autoresizingMask = [.layerWidthSizable]; t.addSublayer(sea)
        if o.texture {
            // two depths of the same glints: the far ones small and slow, the near ones large and quicker. both slide sideways
            let far = tiles(pl.a, 256, cover: sea.bounds.size); far.opacity = 0.55; sea.addSublayer(far)
            let near = tiles(pl.a, 512, cover: sea.bounds.size); near.opacity = 0.8; sea.addSublayer(near)
            scroll(far, dx: -256, dy: 0, dur(46), fps); scroll(near, dx: -512, dy: 0, dur(40), fps)
        }
        let path = image(pl.b, CGRect(x: mx - H * 0.1, y: 0, width: H * 0.2, height: hy), fill: false); path.autoresizingMask = []
        path.opacity = 0.9; path.compositingFilter = "screenBlendMode"; sea.addSublayer(path)
        if o.motion > 0 {   // the path shimmers: a slow side-to-side wobble and an opacity shiver
            let sw = CABasicAnimation(keyPath: "transform.scale.x"); sw.fromValue = 0.9; sw.toValue = 1.15; sw.duration = dur(7)
            sw.autoreverses = true; sw.repeatCount = .infinity; sw.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
            sw.preferredFrameRateRange = Builder.fps(min(fps, 15)); path.add(sw, forKey: "wobble")
            let sh = CABasicAnimation(keyPath: "opacity"); sh.fromValue = 0.65; sh.toValue = 1.0; sh.duration = dur(3.1)
            sh.autoreverses = true; sh.repeatCount = .infinity; sh.preferredFrameRateRange = Builder.fps(min(fps, 15)); path.add(sh, forKey: "shiver")
        }
        let line = CALayer(); line.frame = CGRect(x: 0, y: hy - 0.5, width: W, height: 1); line.backgroundColor = cgc(mix(SIMD3(0.55, 0.68, 0.95), P[1], 0.2), 0.35)
        t.addSublayer(line)
        t.addSublayer(vignette(size, 0.5))
        T.gesture = { drop, s, _ in   // a moment: the moon brightens and the water answers
            let sc = CAKeyframeAnimation(keyPath: "transform.scale"); sc.values = [1, drop ? 1.12 : 1.05, 1]; sc.keyTimes = [0, 0.25, 1]
            sc.duration = drop ? 2.4 : 1.6; sc.preferredFrameRateRange = Builder.fps(fps); moon.add(sc, forKey: "flare")
            let flare = glow(white, 0.5 * s, moonR * 9, at: halo.position); flare.opacity = 0
            fire(t, flare, opacity: [0, 1, 0], times: [0, 0.18, 1], dur: drop ? 2.4 : 1.6, fps, below: moon)
        }
    }
}
