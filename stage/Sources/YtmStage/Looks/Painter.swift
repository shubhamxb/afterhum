// the painter: one still-image set per (song, look, accent). each look lives in Looks/<Name>.swift; this file dispatches and
// holds the two sprite helpers more than one look uses.
import AppKit
import QuartzCore
import Accelerate

enum Painter {
    static let horizon: CGFloat = 0.40   // neon drive: the horizon, as a fraction of the height from the bottom

    static func render(_ look: Look, cover: CGImage?, pal P: [SIMD3<Float>], accent: String, seed: String) -> Plates {
        var rng = Rng(seed + look.id + accent)
        let out = Plates()
        let recolour = accent != "cover"
        switch look {
        case .dusk:  dusk(out, cover: cover, P: P, accent: accent, rng: &rng)
        case .rain:  rain(out, cover: cover, P: P, accent: accent, rng: &rng)
        case .neon:  neon(out, cover: cover, P: P, accent: accent, rng: &rng)
        case .stars: stars(out, cover: cover, P: P, accent: accent, rng: &rng)
        case .paper: paper(out, cover: cover, P: P, accent: accent, rng: &rng)
        case .aurora: aurora(out, cover: cover, P: P, accent: accent, rng: &rng)
        case .ember: ember(out, cover: cover, P: P, accent: accent, rng: &rng)
        case .tide: tide(out, cover: cover, P: P, accent: accent, rng: &rng)
        }
        return out
    }


    static func dust(_ n: Int, _ count: Int, _ r: ClosedRange<CGFloat>, _ a: ClosedRange<CGFloat>, _ col: SIMD3<Float>, _ rng: inout Rng, soft: Bool = false) -> CGImage? {
        let t = Canvas(n, n)
        for _ in 0..<count {
            let p = CGPoint(x: rng.f(0, CGFloat(n)), y: rng.f(0, CGFloat(n))), rr = rng.f(r.lowerBound, r.upperBound), aa = rng.f(a.lowerBound, a.upperBound)
            // soft: a lens-blurred disc with a faint rim, no pin-sharp core (a sharp core is a star)
            t.wrapped(p, rr * 3) { q in
                t.glow(q, rr * 3, col, aa * 0.25)
                if soft { t.glow(q, rr, col, aa * 0.8, hard: 0.75) } else { t.glow(q, rr, col, aa, hard: 0.4) }
            }
        }
        return t.image()
    }

    static func starTile(_ n: Int, _ count: Int, _ r: ClosedRange<CGFloat>, _ a: ClosedRange<CGFloat>, _ P: [SIMD3<Float>], _ rng: inout Rng, halo: Bool = false) -> CGImage? {
        let t = Canvas(n, n)
        for i in 0..<count {
            let p = CGPoint(x: rng.f(0, CGFloat(n)), y: rng.f(0, CGFloat(n))), rr = rng.f(r.lowerBound, r.upperBound), aa = rng.f(a.lowerBound, a.upperBound)
            let col = i % 5 == 0 ? mix(P[i % 3], white, 0.55) : SIMD3<Float>(0.92, 0.94, 1)
            t.wrapped(p, rr * 6) { q in
                if halo { t.glow(q, rr * 6, col, aa * 0.18) }
                t.glow(q, rr, col, aa, hard: 0.5)
            }
        }
        return t.image()
    }
}
