// the "neon" look: what it paints (stills, once per song) and the layer tree core animation moves.
import AppKit
import QuartzCore

extension Painter {
    static func neon(_ out: Plates, cover: CGImage?, P: [SIMD3<Float>], accent: String, rng: inout Rng) {
        // the sky: deep night into the palette at the horizon, a few faint stars, a band of glow on the horizon
        let c = Canvas(240, 150), hy = 150 * horizon
        let sky = CGGradient(colorsSpace: sRGB, colors: [cgc(SIMD3(0.015, 0.012, 0.05)), cgc(mix(SIMD3(0.07, 0.02, 0.12), P[1], 0.32)),
                                                         cgc(mix(SIMD3(0.12, 0.03, 0.14), P[0], 0.45))] as CFArray, locations: [0, 0.75, 1])!
        c.ctx.drawLinearGradient(sky, start: CGPoint(x: 0, y: 150), end: CGPoint(x: 0, y: hy), options: [.drawsAfterEndLocation])
        c.ctx.setBlendMode(.screen)
        c.ctx.saveGState(); c.ctx.scaleBy(x: 1, y: 0.22)
        c.glow(CGPoint(x: 120, y: hy / 0.22), 150, P[1], 0.55); c.ctx.restoreGState()
        c.ctx.setBlendMode(.normal)
        out.base = c.image()
        // the sun: palette gradient, banded in its lower half (the bands widen toward the horizon)
        let s = Canvas(256, 256)
        s.ctx.addEllipse(in: CGRect(x: 8, y: 8, width: 240, height: 240)); s.ctx.clip()
        // always a sunset (a cold cover made a blue, then an olive disc): gold on top, the palette pulled toward rose below
        let gold = SIMD3<Float>(1, 0.84, 0.42), rose = SIMD3<Float>(1, 0.3, 0.55)
        let sg = CGGradient(colorsSpace: sRGB, colors: [cgc(mix(gold, white, 0.2)), cgc(mix(SIMD3(1, 0.6, 0.32), P[2], 0.2)),
                                                        cgc(mix(P[0], rose, 0.6)), cgc(mix(P[1], rose, 0.55))] as CFArray, locations: [0, 0.3, 0.7, 1])!
        s.ctx.drawLinearGradient(sg, start: CGPoint(x: 0, y: 248), end: CGPoint(x: 0, y: 8), options: [])
        s.ctx.setBlendMode(.clear)
        var y: CGFloat = 118, gap: CGFloat = 2
        while y > 0 { s.ctx.fill(CGRect(x: 0, y: y - gap, width: 256, height: gap)); y -= 14 - gap * 0.4; gap += 1.3 }
        out.a = s.image()
        // the floor's grid: a 4×4-cell tile (one tile, many cells: fewer replicator instances)
        let t = Canvas(512, 512), cell: CGFloat = 128
        for pass in 0..<2 {
            t.ctx.setStrokeColor(cgc(mix(P[0], white, pass == 1 ? 0.35 : 0), pass == 0 ? 0.22 : 0.95)); t.ctx.setLineWidth(pass == 0 ? 9 : 2.2)
            for i in 0...4 {
                let v = CGFloat(i) * cell
                t.ctx.move(to: CGPoint(x: v, y: 0)); t.ctx.addLine(to: CGPoint(x: v, y: 512))
                t.ctx.move(to: CGPoint(x: 0, y: v)); t.ctx.addLine(to: CGPoint(x: 512, y: v))
            }
            t.ctx.strokePath()
            if pass == 0 { t.blur(2) }
        }
        out.b = t.image()
        out.c = starTile(512, 46, 0.5...1.0, 0.25...0.7, P, &rng)      // a few stars over the sky, sharp at any size
    }
}

extension Builder {
    static func neon(_ c: Ctx) {
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
        // the sun sets behind the cover (10-23: centred, it sat right behind the title). the cover is opaque, so what shows
        // is the sun's rim around the album and its bands either side: the album on the horizon, the words on clear sky.
        // capped so even a drop's 1.1× swell stops 8 pt short of the title column: half the cover plus the gap to the words
        // (wide 170 + 44, compact 130 + 32; 225 assumed wide and sat 50 pt behind the title at 1100×900)
        let reach = o.artW > 0 ? o.artW / 2 + (o.artW < 300 ? 32 : 44) : 170 + 44
        let hy = H * Painter.horizon, sunR = min(H * 0.26, (reach - 8) / 1.1), sx = o.art.x > 0 ? o.art.x : 56 + 170
        let sunGlow = glow(mix(mix(P[2], P[0], 0.4), SIMD3(1, 0.5, 0.5), 0.45), 0.45 * g, sunR * 3.4, at: CGPoint(x: sx, y: hy + sunR * 0.5))
        T.breath.addSublayer(sunGlow); t.addSublayer(T.glow)
        let sun = image(pl.a, CGRect(x: sx - sunR, y: hy - sunR * 0.5, width: sunR * 2, height: sunR * 2), fill: false)
        sun.opacity = 0.9
        sun.autoresizingMask = []
        if o.texture {
            let sky = CALayer(); sky.frame = CGRect(x: 0, y: hy + H * 0.12, width: W, height: H - hy - H * 0.12); sky.masksToBounds = true
            let st = tiles(pl.c, 512, cover: sky.bounds.size); st.opacity = 0.8; sky.addSublayer(st)
            t.insertSublayer(sky, below: T.glow)
        }
        t.addSublayer(sun)
        // the floor: a plane laid flat (rotated 90° about x) under a perspective whose vanishing point sits on the horizon
        let floor = CALayer(); floor.frame = CGRect(x: 0, y: 0, width: W, height: hy); floor.masksToBounds = true
        floor.backgroundColor = cgc(SIMD3(0.025, 0.012, 0.05))
        floor.anchorPoint = CGPoint(x: 0.5, y: 1); floor.position = CGPoint(x: W / 2, y: hy)
        var p = CATransform3DIdentity; p.m34 = -1 / 420; floor.sublayerTransform = p
        t.addSublayer(floor)
        if o.texture {
            let cell: CGFloat = 512, depth: CGFloat = 9000
            let plane = CALayer()
            plane.bounds = CGRect(x: 0, y: 0, width: W * 3, height: depth)
            plane.anchorPoint = CGPoint(x: 0.5, y: 0); plane.position = CGPoint(x: W / 2, y: 0)
            plane.transform = CATransform3DMakeRotation(-.pi / 2, 1, 0, 0)
            let grid = tiles(pl.b, cell, cover: CGSize(width: W * 3, height: depth))
            grid.frame.origin = CGPoint(x: -cell, y: 0)   // never far behind the camera: the scroll only goes one cell toward it
            plane.addSublayer(grid); floor.addSublayer(plane)
            scroll(grid, dx: 0, dy: -cell / 4, dur(1.4), fps)                // one grid cell toward you
        }
        // haze over the far floor hides the grid's shimmer at the horizon
        let haze = CAGradientLayer(); haze.frame = CGRect(x: 0, y: 0, width: W, height: hy)
        haze.colors = [cgc(mix(SIMD3(0.06, 0.02, 0.1), P[1], 0.25), 1), cgc(SIMD3(0.04, 0.015, 0.07), 0.6), cgc(.zero, 0)]
        haze.locations = [0, 0.3, 1]; haze.startPoint = CGPoint(x: 0.5, y: 1); haze.endPoint = CGPoint(x: 0.5, y: 0)
        floor.addSublayer(haze)
        let line = CALayer(); line.frame = CGRect(x: 0, y: hy - 1, width: W, height: 2); line.backgroundColor = cgc(mix(P[1], white, 0.3), 0.85)
        line.shadowColor = cgc(P[1]); line.shadowOpacity = 1; line.shadowRadius = 6; line.shadowOffset = .zero
        line.shadowPath = CGPath(rect: line.bounds, transform: nil)
        t.addSublayer(line)
        t.addSublayer(vignette(size, 0.5))
        T.gesture = { drop, s, _ in
            let sc = CAKeyframeAnimation(keyPath: "transform.scale"); sc.values = [1, drop ? 1.1 : 1.05, 1]; sc.keyTimes = [0, 0.2, 1]
            sc.duration = drop ? 2.4 : 1.6; sc.timingFunctions = [CAMediaTimingFunction(name: .easeOut), CAMediaTimingFunction(name: .easeInEaseOut)]
            sc.preferredFrameRateRange = Builder.fps(fps)
            sun.add(sc, forKey: "flare")
            let flare = glow(mix(P[2], white, 0.3), 0.7 * s, sunR * 6, at: sunGlow.position); flare.opacity = 0
            fire(t, flare, opacity: [0, 1, 0], times: [0, 0.15, 1], dur: drop ? 2.4 : 1.6, fps, below: sun)
        }
    }
}
