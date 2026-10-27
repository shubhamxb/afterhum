// the backdrop view: the stage's background, as core animation. each look is drawn once per (song, look, accent) into a few small
// still images on a background queue (Looks/*), and the window server moves them (Layers.swift). the app does no work per
// frame: a 2 s timer nudges the glow to the song's intensity, a one-bar animation breathes on the beat, and the song's real
// moments are scheduled ahead of time.
import AppKit
import QuartzCore
import Accelerate
import Combine
import IOKit.ps

// ---- the view: owns the tree, follows the player, the prefs and the settings ----

final class BackdropView: NSView {
    let root = CALayer()
    private var tree: LookTree?
    private var cache: [PlateKey: Plates] = [:], order: [PlateKey] = []
    private let queue = DispatchQueue(label: "ytm.backdrop", qos: .utility)
    private var gen = 0                       // newest render request; older results are dropped
    private var shownKey: PlateKey?, builtSize = CGSize.zero
    private(set) var awake = false
    private var frozen = false
    private var bag = Set<AnyCancellable>()
    private weak var hub: Hub?
    private var analysis: SongAnalysis?, analysisVid = ""
    private var tick: Timer?
    private var pending: [DispatchWorkItem] = []
    private var lastPos = 0.0, lastAt = Date()
    private var resizeItem: DispatchWorkItem?

    override init(frame: NSRect) {
        super.init(frame: frame)
        root.backgroundColor = CGColor(gray: 0.02, alpha: 1); root.isOpaque = true
        layer = root; wantsLayer = true       // layer-hosting: the tree is ours, appkit never redraws it
        layerContentsRedrawPolicy = .never
    }
    required init?(coder: NSCoder) { fatalError() }

    // ---- inputs ----

    func attach(_ h: Hub) {
        hub = h
        let p = h.player
        p.$track.removeDuplicates().sink { [weak self] _ in DispatchQueue.main.async { self?.trackChanged() } }.store(in: &bag)
        p.$paused.removeDuplicates().sink { [weak self] _ in DispatchQueue.main.async { self?.playChanged() } }.store(in: &bag)
        p.$mode.removeDuplicates().sink { [weak self] _ in DispatchQueue.main.async { self?.refresh() } }.store(in: &bag)
        h.cover.$ready.sink { [weak self] _ in DispatchQueue.main.async { self?.refresh() } }.store(in: &bag)
        h.prefs.objectWillChange.sink { [weak self] _ in DispatchQueue.main.async { self?.refresh(rebuild: true); self?.align(); self?.resync() } }.store(in: &bag)
        h.auto.$pick.removeDuplicates().sink { [weak self] _ in DispatchQueue.main.async { self?.refresh(); self?.resync() } }.store(in: &bag)
        // only the settings that change the picture (opening the panel must not rebuild anything), settled for 0.3 s
        let s = h.settings
        Publishers.Merge4(s.$fps.map { _ in () }, s.$texture.map { _ in () }, s.$glow.map { _ in () }, s.$autoBattery.map { _ in () })
            .dropFirst(4).debounce(for: .seconds(0.3), scheduler: RunLoop.main)
            .sink { [weak self] in self?.refresh(rebuild: true) }.store(in: &bag)
        // unplugging caps the frame rate now, not at the next song (the cap is read only when the tree is built)
        Power.watch { [weak self] in if self?.hub?.settings.autoBattery == true { self?.refresh(rebuild: true) } }
    }

    /// the window's on screen (or not): asleep means no tree, no stills, no timers
    func setAwake(_ on: Bool) {
        guard on != awake else { return }
        awake = on
        if on { refresh(); startTick(); resync() }
        else { sleep() }
    }

    func sleep() {
        awake = false; gen += 1
        tick?.invalidate(); tick = nil; cancelMoments()
        tree?.root.removeAllAnimations(); tree?.root.removeFromSuperlayer(); tree = nil
        cache = [:]; order = []; shownKey = nil
    }

    override func setFrameSize(_ s: NSSize) {
        super.setFrameSize(s)
        // a live resize stretches the old tree (autoresizing masks); the paths and tile counts are redone once it settles
        resizeItem?.cancel()
        let it = DispatchWorkItem { [weak self] in self?.refresh(rebuild: true) }
        resizeItem = it; DispatchQueue.main.asyncAfter(deadline: .now() + 0.25, execute: it)
    }

    /// the cover moved (a wider window re-centres the glass): neon drive's sun sits behind it, so that look is rebuilt
    func artMoved() { if awake && look == .neon { refresh(rebuild: true) } }

    // ---- what to show ----

    /// a chosen look always wins; on auto, work mode is paper
    /// and otherwise it's the song's pick. the pick for the cover on screen, not the song
    /// that just started: a new song changes cover and look in one crossfade, not two
    var look: Look {
        guard let h = hub else { return .dusk }
        if h.prefs.look != "auto" { return Look(id: h.prefs.look) }
        // on auto, a mode sets the scene (10-26: modes beyond work): work + focus calm paper, chill rain, energy neon, sleep stars
        if let m = ["work": Look.paper, "focus": .paper, "chill": .rain, "energy": .neon, "sleep": .stars][h.player.mode] { return m }
        return h.auto.pick(for: h.cover.loadedVid).look
    }

    /// a chosen accent always wins; auto only swaps "cover" for the look's own set when the cover is grey
    var accent: String {
        guard let h = hub, h.prefs.look == "auto", h.prefs.accent == "cover" else { return hub?.prefs.accent ?? "cover" }
        return h.player.mode == "work" ? AutoLook.accent(.paper, h.auto.signals) : h.auto.pick(for: h.cover.loadedVid).accent
    }

    var opts: Builder.Opts {
        guard let h = hub else { return Builder.Opts() }
        var f = h.settings.fps
        if h.settings.autoBattery && Power.onBattery { f = min(f, 8) }
        return Builder.Opts(motion: Motion.factor(h.prefs.motion), fps: f, texture: h.settings.texture, glow: h.settings.glow,
                            gestures: h.prefs.moments && h.prefs.motion != "still", art: ArtSpot.frame.width > 0 ? coverSpot : .zero,
                            artW: ArtSpot.frame.width)
    }

    private var refreshQueued = false, rebuildQueued = false
    /// coalesced: a burst of changes (a track change brings the track, then the cover) becomes one render
    func refresh(rebuild: Bool = false) {
        rebuildQueued = rebuildQueued || rebuild
        guard !refreshQueued else { return }
        refreshQueued = true
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.refreshQueued = false; let rb = self.rebuildQueued; self.rebuildQueued = false
            self.apply(rebuild: rb)
        }
    }

    private func apply(rebuild: Bool) {
        guard awake, let h = hub, bounds.width > 10 else { return }
        let cover = h.cover, lk = look, acc = accent
        let vid = cover.loadedVid
        let key = PlateKey(vid: vid, look: lk, accent: acc)
        if key == shownKey && !rebuild && bounds.size == builtSize { return }
        let pal = Accent.palette(acc, cover: cover.palette)
        let o = opts, size = bounds.size
        let fade = key != shownKey
        if let pl = cache[key] { show(Builder.build(lk, pl, pal, size, o), key, fade: fade); return }
        gen += 1; let g = gen
        let img = cover.cgImage
        queue.async { [weak self] in
            let pl = Painter.render(lk, cover: img, pal: pal, accent: acc, seed: vid)
            DispatchQueue.main.async {
                guard let self, self.gen == g, self.awake else { return }
                self.remember(key, pl)
                self.show(Builder.build(lk, pl, pal, self.bounds.size, self.opts), key, fade: fade)
            }
        }
    }

    private func remember(_ k: PlateKey, _ p: Plates) {
        cache[k] = p; order.removeAll { $0 == k }; order.append(k)
        while order.count > 3 { cache[order.removeFirst()] = nil }   // three stills sets at most (~2 MB each)
    }

    private func show(_ t: LookTree, _ key: PlateKey, fade: Bool) {
        CATransaction.begin()
        if fade && tree != nil {
            let tr = CATransition(); tr.type = .fade; tr.duration = 1.6
            tr.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
            root.add(tr, forKey: kCATransition)
        } else { CATransaction.setDisableActions(true) }
        if let old = tree?.root { root.replaceSublayer(old, with: t.root) } else { root.addSublayer(t.root) }
        CATransaction.commit()
        tree = t; shownKey = key; builtSize = bounds.size
        frozen = false
        if hub?.player.paused ?? true { freeze() }
        intensityNow(animated: false); align()
    }

    // ---- pause = freeze the tree where it is (apple qa1673), resume picks up from there ----

    private func freeze() {
        guard let l = tree?.root, !frozen else { return }
        let t = l.convertTime(CACurrentMediaTime(), from: nil)
        l.speed = 0; l.timeOffset = t; frozen = true
    }
    private func unfreeze() {
        guard let l = tree?.root, frozen else { return }
        let p = l.timeOffset
        l.speed = 1; l.timeOffset = 0; l.beginTime = 0
        l.beginTime = l.convertTime(CACurrentMediaTime(), from: nil) - p; frozen = false
    }

    private func playChanged() {
        guard let p = hub?.player else { return }
        if p.paused { freeze(); cancelMoments() } else { unfreeze(); align(); resync() }
        if awake { startTick() }
    }

    private func trackChanged() {
        analysis = nil; analysisVid = ""
        cancelMoments(); loadAnalysis(); refresh()
    }

    // ---- the music, without a frame loop ----

    private func loadAnalysis() {
        guard let h = hub else { return }
        let vid = h.player.track.vid
        if analysisVid == vid, analysis != nil { return }
        if let a = h.analyses.get(vid) {
            analysis = a; analysisVid = vid
            h.ui.bpm = Int(a.bpm.rounded())
            h.auto.audio(a, at: h.player.position)     // a song analysed just now: the auto look hears it too
            align(); resync()
        } else if h.ui.bpm != nil { h.ui.bpm = nil }
    }

    /// every 2 s while visible + playing: the glow follows the song's intensity, and a jump in position means a seek
    private func startTick() {
        let want = awake && !(hub?.player.paused ?? true)
        if !want { tick?.invalidate(); tick = nil; return }
        guard tick == nil else { return }
        lastPos = hub?.player.position ?? 0; lastAt = Date()
        let t = Timer(timeInterval: 2, repeats: true) { [weak self] _ in self?.onTick() }
        t.tolerance = 0.4; RunLoop.main.add(t, forMode: .common); tick = t
    }

    private func onTick() {
        guard let p = hub?.player else { return }
        if analysis == nil { loadAnalysis() }
        let pos = p.position, expect = lastPos + Date().timeIntervalSince(lastAt)
        if abs(pos - expect) > 1.5 { align(); resync() }            // someone seeked (here, the cli or the terminal ui)
        lastPos = pos; lastAt = Date()
        intensityNow(animated: true)
    }

    private func intensityNow(animated: Bool) {
        guard let t = tree, let p = hub?.player else { return }
        let i = analysis?.intensity(at: p.position + 1) ?? 0.5
        let v = Float(0.55 + 0.45 * Double(i))
        CATransaction.begin(); CATransaction.setAnimationDuration(animated ? 2 : 0); CATransaction.setDisableActions(!animated)
        t.glow.opacity = v
        CATransaction.commit()
    }

    /// the breath: one bar, a soft peak on every beat (the downbeat a little more), phase-locked to the next beat
    private func align() {
        guard let t = tree, let p = hub?.player else { return }
        t.breath.removeAnimation(forKey: "breath")
        guard let a = analysis, !p.paused, opts.motion > 0, look != .paper, a.bpm > 30 else { return }
        guard let off = Schedule.breath(a, pos: p.position) else { return }
        let beat = 60 / a.bpm
        let br = CAKeyframeAnimation(keyPath: "opacity")
        br.values = [1, 0.8, 0.93, 0.8, 0.93, 0.8, 0.93, 0.8, 1]
        br.keyTimes = [0, 0.16, 0.25, 0.41, 0.5, 0.66, 0.75, 0.91, 1]
        br.calculationMode = .cubic; br.duration = beat * 4; br.repeatCount = .infinity
        br.preferredFrameRateRange = Builder.fps(min(opts.fps, 30))
        br.beginTime = t.breath.convertTime(CACurrentMediaTime(), from: nil) + off
        t.breath.add(br, forKey: "breath")
    }

    // ---- moments: each upcoming drop / lift scheduled from the playback position, never polled ----

    /// a lean in flight lets go too (it's fill-forwards): a pause or a seek past the drop must not leave the light drawn in
    private func cancelMoments() { pending.forEach { $0.cancel() }; pending = []; release() }

    func resync() {
        cancelMoments()
        guard awake, let p = hub?.player, !p.paused, let a = analysis, opts.gestures, look != .paper else { return }
        guard let (at, drop, s, leanAt) = Schedule.moment(a, pos: p.position) else { return }
        // no gen check: a render bumps gen after this is scheduled (a wake, a new cover, a look change) and the chain would
        // end there. cancelMoments() is the only token moments need; they act on whatever tree is up when they land
        let lean = DispatchWorkItem { [weak self] in self?.leanIn() }
        let land = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.release(); self.gesture(drop: drop, s: s); self.resync()
        }
        if let la = leanAt { DispatchQueue.main.asyncAfter(deadline: .now() + la, execute: lean); pending.append(lean) }
        DispatchQueue.main.asyncAfter(deadline: .now() + at, execute: land); pending.append(land)
    }

    /// 1.5 s before a drop the light draws in
    private func leanIn() {
        guard let l = tree?.lean else { return }
        let sc = CABasicAnimation(keyPath: "transform.scale"); sc.toValue = 0.93
        let op = CABasicAnimation(keyPath: "opacity"); op.toValue = 0.62
        let g = CAAnimationGroup(); g.animations = [sc, op]; g.duration = 1.5; g.fillMode = .forwards; g.isRemovedOnCompletion = false
        g.timingFunction = CAMediaTimingFunction(name: .easeIn); g.preferredFrameRateRange = Builder.fps(opts.fps)
        l.add(g, forKey: "lean")
    }

    private func release() {
        guard let l = tree?.lean, l.animation(forKey: "lean") != nil else { return }
        let from = l.presentation()
        l.removeAnimation(forKey: "lean")
        let sc = CASpringAnimation(keyPath: "transform.scale"); sc.fromValue = from?.value(forKeyPath: "transform.scale") ?? 0.93; sc.toValue = 1
        sc.damping = 9; sc.duration = sc.settlingDuration
        let op = CABasicAnimation(keyPath: "opacity"); op.fromValue = from?.opacity ?? 0.62; op.toValue = 1; op.duration = 0.6
        sc.preferredFrameRateRange = Builder.fps(opts.fps); op.preferredFrameRateRange = Builder.fps(opts.fps)
        l.add(sc, forKey: "release"); l.add(op, forKey: "release.o")
    }

    func gesture(drop: Bool, s: CGFloat) {
        guard let t = tree else { return }
        t.gesture?(drop, s, coverSpot)
    }

    /// where the cover sits, in the backdrop's coordinates (swiftui reports it top-down; layers count from the bottom)
    var coverSpot: CGPoint {
        let f = ArtSpot.frame
        if f.width > 0 { return CGPoint(x: f.midX, y: bounds.height - f.midY) }
        return CGPoint(x: bounds.width * 0.2, y: bounds.height * 0.5)
    }

    // ---- headless (--snapshot / --bench): the same tree, built synchronously, with no hub ----

    static func headless(_ look: Look, cover: Cover, accent: String, size: CGSize, opts: Builder.Opts) -> LookTree {
        let pal = Accent.palette(accent, cover: cover.palette)
        let pl = Painter.render(look, cover: cover.cgImage, pal: pal, accent: accent, seed: cover.loadedVid)
        return Builder.build(look, pl, pal, size, opts)
    }
}

/// the timing maths, apart from the layers so `--moments` can check it headless
enum Schedule {
    /// the breath's beginTime offset from now: its keyframe 0 lands on a downbeat (a bar is 4 beats; beats count from the
    /// song's first detected beat), so the soft peaks fall on the beats from the very first one
    static func breath(_ a: SongAnalysis, pos: Double) -> Double? {
        guard a.bpm > 30, let (next, idx) = a.nextBeat(after: pos) else { return nil }
        let beat = 60 / a.bpm, toDown = Double((4 - idx % 4) % 4)
        return (next - pos) + toDown * beat - beat * 4
    }

    /// the next moment after pos: seconds until it lands, drop or lift, its strength, and (drops only, if there's time)
    /// when the 1.5 s lean-in starts
    static func moment(_ a: SongAnalysis, pos: Double) -> (at: Double, drop: Bool, s: CGFloat, leanAt: Double?)? {
        guard let m = a.moments.first(where: { $0.t > pos + 0.05 }) else { return nil }
        let at = m.t - pos, drop = m.kind > 0.75
        return (at, drop, CGFloat(min(1, 0.45 + Double(m.s))), drop && at > 1.6 ? at - 1.5 : nil)
    }
}

/// the cover's frame on screen, reported by RootView (plain storage: nothing redraws when it changes)
enum ArtSpot { static var frame = CGRect.zero }
