// the layer tree: core animation does the moving. Builder holds the shared pieces (glow, tiles, scroll, drift, vignette, one-shot
// sprites); each look's own tree is in Looks/<Name>.swift.
import AppKit
import QuartzCore

// ---- the layer tree ----

/// the handles one look's tree exposes: the glow (intensity + breath + lean live here) and the gesture for a moment
final class LookTree {
    let root = CALayer()
    var glow = CALayer(), breath = CALayer(), lean = CALayer()   // glow ⊃ lean ⊃ breath ⊃ the glows
    var gesture: ((_ drop: Bool, _ s: CGFloat, _ origin: CGPoint) -> Void)?
    var plates: Plates?
}

enum Builder {
    static func fps(_ f: Double) -> CAFrameRateRange {
        let p = Float(max(4, min(120, f)))
        return CAFrameRateRange(minimum: max(4, p / 2), maximum: p, preferred: p)
    }

    static func image(_ img: CGImage?, _ frame: CGRect, fill: Bool = true) -> CALayer {
        let l = CALayer(); l.frame = frame; l.contents = img
        l.contentsGravity = fill ? .resizeAspectFill : .resize
        l.magnificationFilter = .linear; l.minificationFilter = .trilinear; l.masksToBounds = fill
        l.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]
        return l
    }

    static func glow(_ c: SIMD3<Float>, _ a: CGFloat, _ d: CGFloat, at p: CGPoint, screen: Bool = true) -> CAGradientLayer {
        let g = CAGradientLayer(); g.type = .radial
        g.colors = [cgc(c, a), cgc(c, a * 0.42), cgc(c, a * 0.1), cgc(c, 0)]; g.locations = [0, 0.35, 0.7, 1]
        g.startPoint = CGPoint(x: 0.5, y: 0.5); g.endPoint = CGPoint(x: 1, y: 1)
        g.bounds = CGRect(x: 0, y: 0, width: d, height: d); g.position = p
        if screen { g.compositingFilter = "screenBlendMode" }
        return g
    }

    /// a seamless field of one tile: two nested replicators covering `size` plus one tile, so a one-tile scroll never shows an edge
    static func tiles(_ img: CGImage?, _ t: CGFloat, cover size: CGSize, scale: CGFloat = 1) -> CALayer {
        let nx = Int(ceil(size.width / t)) + 2, ny = Int(ceil(size.height / t)) + 2
        let tile = CALayer(); tile.frame = CGRect(x: 0, y: 0, width: t, height: t); tile.contents = img
        tile.contentsScale = scale; tile.magnificationFilter = .linear; tile.minificationFilter = .trilinear
        let row = CAReplicatorLayer(); row.frame = CGRect(x: 0, y: 0, width: t * CGFloat(nx), height: t)
        row.instanceCount = nx; row.instanceTransform = CATransform3DMakeTranslation(t, 0, 0); row.addSublayer(tile)
        let all = CAReplicatorLayer(); all.frame = CGRect(x: -t, y: -t, width: t * CGFloat(nx), height: t * CGFloat(ny))
        all.instanceCount = ny; all.instanceTransform = CATransform3DMakeTranslation(0, t, 0); all.addSublayer(row)
        return all
    }

    static func scroll(_ l: CALayer, dx: CGFloat, dy: CGFloat, _ dur: Double, _ fps: Double) {
        guard dur > 0 else { return }
        let a = CABasicAnimation(keyPath: "position")
        a.fromValue = l.position; a.toValue = CGPoint(x: l.position.x + dx, y: l.position.y + dy)
        a.duration = dur; a.repeatCount = .infinity; a.timingFunction = CAMediaTimingFunction(name: .linear)
        a.preferredFrameRateRange = Builder.fps(fps)
        l.add(a, forKey: "scroll")
    }

    /// a slow elliptical drift around the layer's own position, starting `phase` (0…1) of the way round
    static func drift(_ l: CALayer, rx: CGFloat, ry: CGFloat, _ dur: Double, phase: Double, _ fps: Double) {
        guard dur > 0 else { return }
        let c = l.position, path = CGMutablePath()
        path.addEllipse(in: CGRect(x: c.x - rx, y: c.y - ry, width: rx * 2, height: ry * 2))
        let a = CAKeyframeAnimation(keyPath: "position"); a.path = path; a.calculationMode = .paced
        a.duration = dur; a.repeatCount = .infinity; a.timeOffset = phase * dur
        a.preferredFrameRateRange = Builder.fps(fps)
        l.add(a, forKey: "drift")
    }

    static func vignette(_ size: CGSize, _ a: CGFloat) -> CALayer {
        let v = CAGradientLayer(); v.type = .radial
        v.colors = [cgc(.zero, 0), cgc(.zero, a * 0.35), cgc(.zero, a)]; v.locations = [0.45, 0.75, 1]
        v.startPoint = CGPoint(x: 0.5, y: 0.5); v.endPoint = CGPoint(x: 1.08, y: 1.12)
        v.frame = CGRect(origin: .zero, size: size); v.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]
        return v
    }

    struct Opts { var motion = 1.0, fps = 20.0, texture = true, glow = 1.0, gestures = true, art = CGPoint.zero, artW: CGFloat = 0 }   // art: the cover's centre, layer coords; artW its width (zero = the default layout's)

    /// one look's whole tree from its stills. size is the window's; everything soft is scaled by core animation
    static func build(_ look: Look, _ pl: Plates, _ P: [SIMD3<Float>], _ size: CGSize, _ o: Opts) -> LookTree {
        let T = LookTree(); T.plates = pl
        let full = CGRect(origin: .zero, size: size)
        let t = T.root; t.frame = full; t.masksToBounds = true; t.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]
        t.addSublayer(image(pl.base, full))
        for l in [T.glow, T.lean, T.breath] { l.frame = full; l.autoresizingMask = [.layerWidthSizable, .layerHeightSizable] }
        T.glow.addSublayer(T.lean); T.lean.addSublayer(T.breath)
        let c = Ctx(T: T, pl: pl, P: P, size: size, o: o)

        switch look {
        case .dusk:  dusk(c)
        case .rain:  rain(c)
        case .neon:  neon(c)
        case .stars: stars(c)
        case .paper: paper(c)
        case .aurora: aurora(c)
        case .ember: ember(c)
        case .tide: tide(c)
        }
        return T
    }

    /// a one-shot sprite: added, animated, removed when done
    static func fire(_ parent: CALayer, _ l: CALayer, opacity: [Float], times: [NSNumber], dur: Double, _ fps: Double,
                     extra: [CABasicAnimation] = [], below: CALayer? = nil) {
        if let below { parent.insertSublayer(l, below: below) } else { parent.addSublayer(l) }
        let o = CAKeyframeAnimation(keyPath: "opacity"); o.values = opacity; o.keyTimes = times
        let grp = CAAnimationGroup(); grp.animations = [o] + extra; grp.duration = dur
        grp.preferredFrameRateRange = Builder.fps(fps)
        CATransaction.begin()
        CATransaction.setCompletionBlock { l.removeFromSuperlayer() }
        l.add(grp, forKey: "fire")
        CATransaction.commit()
    }
}

/// what one look's tree builder is handed: the tree's handles, the stills, the palette, the window size and the options
struct Ctx { let T: LookTree, pl: Plates, P: [SIMD3<Float>], size: CGSize, o: Builder.Opts }
