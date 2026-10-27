// the "rain" look: what it paints (stills, once per song) and the layer tree core animation moves.
import AppKit
import QuartzCore

extension Painter {
    static func rain(_ out: Plates, cover: CGImage?, P: [SIMD3<Float>], accent: String, rng: inout Rng) {
        // a city out of focus behind the glass: cool dark base, palette bokeh weighted to the bottom
        let c = Canvas(240, 150)
        let g = CGGradient(colorsSpace: sRGB, colors: [cgc(SIMD3(0.035, 0.045, 0.085)), cgc(SIMD3(0.06, 0.07, 0.11))] as CFArray, locations: [0, 1])!
        c.ctx.drawLinearGradient(g, start: CGPoint(x: 0, y: 150), end: .zero, options: [])
        if let cover { c.cover(cover, zoom: 1.4, alpha: 0.22); c.blur(8) }
        c.map { mix($0, SIMD3(lum($0) * 0.7, lum($0) * 0.85, lum($0) * 1.15), 0.6) }    // colder
        c.ctx.setBlendMode(.screen)
        for i in 0..<46 {
            let y = 150 * pow(rng.f(), 1.9), x = rng.f(-10, 250)
            let r = rng.f(4, 15) * (1.15 - y / 150 * 0.5)
            let col = P[i % 3] * Float(rng.f(0.75, 1))
            c.ctx.setFillColor(cgc(col, rng.f(0.22, 0.55)))
            c.ctx.fillEllipse(in: CGRect(x: x - r, y: y - r, width: r * 2, height: r * 2))
        }
        c.ctx.setBlendMode(.normal)
        c.blur(1)
        out.base = c.image()
        // the rain: thin slanted streaks, a seamless tile
        let t = Canvas(256, 256); t.ctx.setLineCap(.round)
        for _ in 0..<70 {
            let len = rng.f(16, 48), p = CGPoint(x: rng.f(0, 256), y: rng.f(0, 256)), a = rng.f(0.07, 0.2)
            t.wrapped(p, len) { q in
                t.ctx.setStrokeColor(cgc(SIMD3(0.78, 0.85, 0.98), a)); t.ctx.setLineWidth(rng.f(0.7, 1.2))
                t.ctx.move(to: q); t.ctx.addLine(to: CGPoint(x: q.x - len * 0.12, y: q.y - len)); t.ctx.strokePath()
            }
        }
        out.a = t.image()
        // drops on the glass: little lenses, dark rim + a light catch
        let d = Canvas(320, 320)
        for _ in 0..<24 {
            let r = rng.f(2.2, 6), p = CGPoint(x: rng.f(0, 320), y: rng.f(0, 320))
            d.wrapped(p, r * 2) { q in
                d.ctx.setFillColor(cgc(SIMD3(0.65, 0.72, 0.85), 0.14))
                d.ctx.fillEllipse(in: CGRect(x: q.x - r, y: q.y - r * 1.25, width: r * 2, height: r * 2.5))
                d.ctx.setStrokeColor(cgc(SIMD3(0.02, 0.03, 0.06), 0.25)); d.ctx.setLineWidth(0.8)
                d.ctx.strokeEllipse(in: CGRect(x: q.x - r, y: q.y - r * 1.25, width: r * 2, height: r * 2.5))
                d.glow(CGPoint(x: q.x - r * 0.3, y: q.y + r * 0.45), r * 0.55, SIMD3(0.95, 0.97, 1), 0.7, hard: 0.3)
            }
        }
        out.b = d.image()
    }
}

extension Builder {
    static func rain(_ c: Ctx) {
        let T = c.T, t = c.T.root
        let pl = c.pl
        let P = c.P
        let size = c.size
        let o = c.o
        let W = c.size.width
        let H = c.size.height
        let full = CGRect(origin: .zero, size: c.size)
        let fps = c.o.fps
        let g = CGFloat(c.o.glow)
        func dur(_ s: Double) -> Double { c.o.motion > 0 ? s / c.o.motion : 0 }
        t.addSublayer(T.glow)
        let g0 = glow(P[0], 0.22 * g, H * 0.9, at: CGPoint(x: W * 0.25, y: H * 0.1))
        let g1 = glow(P[1], 0.2 * g, H * 0.8, at: CGPoint(x: W * 0.78, y: H * 0.18))
        T.breath.addSublayer(g0); T.breath.addSublayer(g1)
        drift(g0, rx: W * 0.05, ry: H * 0.03, dur(40), phase: 0.2, fps); drift(g1, rx: W * 0.04, ry: H * 0.03, dur(52), phase: 0.7, fps)
        if o.texture {
            let drops = tiles(pl.b, 320, cover: size); drops.opacity = 0.85; t.addSublayer(drops)
            scroll(drops, dx: 0, dy: -320, dur(70), fps)
            let rain = tiles(pl.a, 256, cover: size); rain.opacity = 0.9; t.addSublayer(rain)
            scroll(rain, dx: 0, dy: -256, dur(1.5), fps)                       // down; a tile is 256 pt of rain
        }
        t.addSublayer(vignette(size, 0.55))
        let flash = CAGradientLayer(); flash.frame = full; flash.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]
        flash.colors = [cgc(mix(SIMD3(0.82, 0.87, 1), P[2], 0.2), 0.75), cgc(SIMD3(0.82, 0.87, 1), 0)]
        flash.startPoint = CGPoint(x: 0.5, y: 1); flash.endPoint = CGPoint(x: 0.5, y: 0.25); flash.opacity = 0
        flash.compositingFilter = "screenBlendMode"; t.addSublayer(flash)
        T.gesture = { drop, s, _ in
            let v: [Float] = drop ? [0, Float(0.6 * s), 0.08, Float(0.42 * s), 0] : [0, Float(0.22 * s), 0]
            let k: [NSNumber] = drop ? [0, 0.05, 0.16, 0.24, 1] : [0, 0.15, 1]
            let a = CAKeyframeAnimation(keyPath: "opacity"); a.values = v; a.keyTimes = k; a.duration = drop ? 1.5 : 1.0
            a.preferredFrameRateRange = Builder.fps(max(fps, 30))       // a flicker needs the frames, briefly
            flash.add(a, forKey: "flash")
        }
    }
}
