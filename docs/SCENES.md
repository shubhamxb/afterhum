# adding a scene

a scene (code calls it a *look*) is two things: the **stills** it paints once per song, and the **layer tree** core animation moves.
the app does no per-frame work, so a scene costs nothing while it plays. every scene lives in one file.

## 1. write `stage/Sources/YtmStage/Looks/<Name>.swift`

```swift
extension Painter {
    // once per (song, scene, accent), on a background queue. P = the palette (main, second, spark), 0...1 rgb
    static func mist(_ out: Plates, cover: CGImage?, P: [SIMD3<Float>], accent: String, rng: inout Rng) {
        let c = Canvas(192, 120)         // small: core animation scales it, and the scaling is the last blur
        c.fill(cgc(SIMD3(0.02, 0.03, 0.05)))
        c.glow(CGPoint(x: 96, y: 40), 90, P[0], 0.4)
        out.base = c.image()             // base fills the window; a, b, c are tiles or sprites, yours to use
        out.a = dust(512, 20, 1.5...3.0, 0.2...0.5, P[2], &rng)
    }
}
extension Builder {
    // the layers. Ctx has T (the tree), pl (the stills), P, size, o (motion, fps, texture, glow options)
    static func mist(_ c: Ctx) {
        let T = c.T, t = T.root, pl = c.pl, size = c.size, o = c.o
        t.addSublayer(T.glow)            // T.breath holds what pulses on the beat; T.glow follows the song's intensity
        if o.texture { let motes = tiles(pl.a, 512, cover: size); t.addSublayer(motes); scroll(motes, dx: 0, dy: 512, o.motion > 0 ? 60 / o.motion : 0, o.fps) }
        t.addSublayer(vignette(size, 0.5))
        T.gesture = { drop, s, at in /* a drop or lift in the song: add a one-shot with fire(...) */ }
    }
}
```

helpers you can use: `Canvas` (glow, blur, cover, map, wrapped), `Rng` (deterministic: the same song draws the same dust),
`Noise.fbm`, `Painter.dust`, `Painter.starTile`, and on the layer side `Builder.glow`, `tiles`, `scroll`, `drift`, `vignette`, `fire`.
rules that keep it cheap: no per-frame drawing; animate `position`, `opacity` or `transform` only; keep stills small; use
`Builder.fps(...)` on every animation.

## 2. register it

1. `stage/Sources/YtmStage/Look.swift`: add the case and its entry in `ids`, `name` and `icon` (an SF Symbol).
2. `Looks/Painter.swift` and `Layers.swift`: one `case` line each in the dispatch `switch`.
3. `lib/looks.py`: append the id to `LOOKS` and its label to `LOOK_NAMES`. a test fails if the two lists disagree.
4. `live/live.py`: a number in `LOOK_ENERGY` (0 = calm … 1 = lively) for the terminal shader.
5. `auto` choosing it from a song is separate and optional: `AutoLook.swift` and `lib/autolook.py` are twins checked against
   `tests/autolook_cases.json`. the first five scenes take part; `Look.autoPicks` says so.

## 3. look at it

```sh
cd stage && swift build -c release
.build/release/YtmStage --snapshot /tmp/mist.png --vid <a cover id from ~/.local/share/ytm/art> --look mist
.build/release/YtmStage --bench          # paint and build time, memory, per scene
.build/release/YtmStage --checks         # then: tests/run.sh
```

check it at the window's minimum size too: `--size 980x620`.
