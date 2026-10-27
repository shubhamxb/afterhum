// the menu-bar player: the same player, cover and scene as the stage, in a small panel under the menu bar.
// the stage window can close and the app stays here, with no backdrop alive and nothing drawing.
import SwiftUI
import AppKit
import Combine

let pink = Color(red: 0.91, green: 0.56, blue: 0.71)

/// everything the app shares, made once: the stage window and the menu-bar panel are two faces of these
@MainActor final class Hub {
    static let shared = Hub()
    let player = Player(), cover = Cover(), ui = UIState(), settings = StageSettings(), surf = SurfState(), panel = PanelState(), prefs = Prefs()
    let search = SearchState(), panelSearch = SearchState()   // the stage's box and the panel's: one request in flight each
    let analyses = AnalysisCache()
    let auto = AutoPick()
    private var bag: AnyCancellable?, seen: AnyCancellable?, picks = Set<AnyCancellable>()
    private var wakeObs: NSObjectProtocol?

    private init() {
        // the cover follows the song even when no stage window is open to ask for it
        bag = player.$track.removeDuplicates().sink { [cover] t in cover.load(t) }
        // the auto look follows it too: words now, the cover and the analysis as they land (the backdrop hands over a fresh
        // analysis; one already on disk is read here). @Published sends before the value is set: the new track comes in t
        player.$track.removeDuplicates().sink { [auto, player, analyses] t in
            let fresh = t.vid != auto.vid
            auto.track(t, at: fresh ? 0 : player.position)
            if fresh, let a = analyses.peek(t.vid) { auto.audio(a, at: 0) }
        }.store(in: &picks)
        cover.$ready.sink { [auto, cover, player] _ in DispatchQueue.main.async { auto.cover(cover, at: player.position) } }.store(in: &picks)
        player.$duration.removeDuplicates().sink { [auto, player] d in auto.length(d, at: player.position) }.store(in: &picks)
        // nobody looking (stage and panel both off screen): the player stops knocking on a missing socket until someone is
        seen = ui.$visible.combineLatest(panel.$shown).map { $0 || $1 }.removeDuplicates().sink { [player] on in player.active = on }
        // the lid opened: check the stream didn't die under a "playing" mpv (Engine.swift didWake: two looks, at most one nudge)
        wakeObs = NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { _ in
            MainActor.assumeIsolated { Hub.shared.player.didWake() }
        }
    }
}

/// panel-only state (ui state lives in objects: no @State on this sdk)
final class PanelState: ObservableObject {
    @Published var shown = false   // the panel is on screen: only then does the progress line tick
    @Published var query = ""
}

/// the stage: an appkit window. the backdrop (core animation) is the bottom view, the swiftui glass a clear hosting view on top,
/// so the two never redraw each other. closing it frees both and drops the app to the menu bar (no dock icon, nothing drawing);
/// "open stage", `ytm stage` or a dock click (reopen) builds a fresh one at the remembered frame.
@MainActor final class StageWindowController: NSWindowController, NSWindowDelegate {
    static var current: StageWindowController?
    let backdrop: BackdropView
    private var idle: Timer?
    private var poke: Any?, moves: NSTrackingArea?, wasBusy = false

    static func show() {
        NSApp.setActivationPolicy(.regular)
        if current == nil { current = StageWindowController() }
        guard let w = current?.window else { return }
        if w.isMiniaturized { w.deminiaturize(nil) }
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        DispatchQueue.main.async { current?.visibility() }   // occlusion news can lag the first show: wake the backdrop now
    }

    init() {
        let hub = Hub.shared
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1360, height: 860),
                         styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView], backing: .buffered, defer: true)
        w.title = "ytm"; w.titleVisibility = .hidden; w.titlebarAppearsTransparent = true
        w.isReleasedWhenClosed = false; w.tabbingMode = .disallowed
        w.collectionBehavior.insert(.fullScreenPrimary)
        w.minSize = NSSize(width: 980, height: 620)
        w.backgroundColor = .black; w.isOpaque = true      // opaque: the window server never blends what's behind it
        let bounds = NSRect(x: 0, y: 0, width: 1360, height: 860)
        let box = NSView(frame: bounds)
        backdrop = BackdropView(frame: bounds); backdrop.autoresizingMask = [.width, .height]
        let glass = NSHostingView(rootView: RootView(player: hub.player, cover: hub.cover, prefs: hub.prefs, auto: hub.auto, ui: hub.ui, settings: hub.settings, surf: hub.surf, search: hub.search)
            .environment(\.colorScheme, .dark))
        glass.sizingOptions = []                            // the window sizes the glass, not the other way round
        glass.frame = bounds; glass.autoresizingMask = [.width, .height]
        box.addSubview(backdrop); box.addSubview(glass)
        w.contentView = box
        if !w.setFrameUsingName("ytm.stage") { w.center() }
        w.setFrameAutosaveName("ytm.stage")
        super.init(window: w)
        w.delegate = self
        backdrop.attach(hub)
        // 0.8.1: stillness is the window's to see, not swiftui's. onContinuousHover only fired over hit-testable glass (the
        // cover, the words), so moving over the open stage never counted, and once the glass faded out it was nearly gone.
        // one tracking area over the whole window (active even when ytm isn't in front), clicks and scrolls by monitor
        let t = NSTrackingArea(rect: .zero, options: [.mouseMoved, .mouseEnteredAndExited, .activeAlways, .inVisibleRect], owner: self, userInfo: nil)
        box.addTrackingArea(t); moves = t
        poke = NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown, .otherMouseDown, .scrollWheel, .keyDown]) { [weak self] e in
            MainActor.assumeIsolated { if let self, e.window === self.window { self.wake() } }; return e
        }
    }
    required init?(coder: NSCoder) { fatalError() }

    func windowDidChangeOcclusionState(_ n: Notification) { visibility() }
    func windowDidMiniaturize(_ n: Notification) { visibility() }
    func windowDidDeminiaturize(_ n: Notification) { visibility() }

    /// on screen → the backdrop wakes and the glass ticks; covered, minimised or hidden → both rest
    func visibility() {
        guard let w = window else { return }
        let v = w.isVisible && !w.isMiniaturized && w.occlusionState.contains(.visible)
        backdrop.setAwake(v)
        if Hub.shared.ui.visible != v { Hub.shared.ui.visible = v }
        if v { startIdle() } else { idle?.invalidate(); idle = nil }
    }

    override func mouseMoved(with e: NSEvent) { wake() }
    override func mouseEntered(with e: NSEvent) { wake() }

    /// any sign of life (a move, a click, a scroll, a key): the countdown restarts and the glass comes straight back
    func wake() {
        let ui = Hub.shared.ui
        ui.lastMove = Date()
        if !ui.showUI { withAnimation(.easeOut(duration: 0.3)) { ui.showUI = true }; chrome(true) }
    }

    /// the traffic lights go with the glass (full screen has none to fade)
    private func chrome(_ on: Bool) {
        guard let w = window, !w.styleMask.contains(.fullScreen), let bar = w.standardWindowButton(.closeButton)?.superview else { return }
        NSAnimationContext.runAnimationGroup { c in c.duration = on ? 0.3 : 0.8; bar.animator().alphaValue = on ? 1 : 0 }
    }

    /// stillness hides the glass: checked once a second while the stage is visible, never by the view itself
    private func startIdle() {
        guard idle == nil else { return }
        Hub.shared.ui.lastMove = Date(); wasBusy = false     // back on screen: a fresh countdown, not one that ran out behind a window
        let t = Timer(timeInterval: 1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
        t.tolerance = 0.3; RunLoop.main.add(t, forMode: .common); idle = t
    }

    private func tick() {
        let h = Hub.shared, ui = h.ui, now = Date()
        // a search with words in it, a pause or the settings hold the glass. the countdown starts when the hold lifts, so
        // pressing play or closing settings never snaps the glass away the same second
        // 0.8.2: a search on its way, or results with the box still focused, hold it too
        let busy = (ui.searching && !ui.query.isEmpty) || h.player.paused || h.settings.open || h.search.loading || (h.search.open && ui.searching)
        if wasBusy && !busy { ui.lastMove = now }; wasBusy = busy
        let still = !busy && now.timeIntervalSince(ui.lastMove) > h.settings.hideAfter
        guard ui.showUI == still else { return }
        if still && h.search.open { h.search.close(); ui.query = "" }   // left alone, unfocused: the cards go with the glass
        // clicked into search, then away: appkit keeps the field focused (space would type into it). an empty one lets go
        if still && ui.searching { window?.makeFirstResponder(nil); ui.searching = false }
        withAnimation(.easeInOut(duration: 0.8)) { ui.showUI = !still }
        chrome(!still)
        // apple's way: the pointer leaves with the glass, only when it's over the stage and ytm is in front; a move brings both
        if still, NSApp.isActive, let w = window, w.isKeyWindow, w.frame.contains(NSEvent.mouseLocation) { NSCursor.setHiddenUntilMouseMoves(true) }
    }

    func windowWillClose(_ n: Notification) {
        idle?.invalidate(); idle = nil
        if let m = poke { NSEvent.removeMonitor(m) }; poke = nil
        if let t = moves { window?.contentView?.removeTrackingArea(t) }; moves = nil
        backdrop.sleep()
        Hub.shared.ui.visible = false; Hub.shared.ui.showUI = true; Hub.shared.settings.open = false
        window?.saveFrame(usingName: "ytm.stage"); window?.setFrameAutosaveName("")   // the next stage takes the name over
        DispatchQueue.main.async {
            if StageWindowController.current === self { StageWindowController.current = nil }
            NSApp.setActivationPolicy(.accessory)        // menu bar only: no dock icon, no app menu
        }
    }
}

/// tells a view which window it lives in, whether that window is on screen, and when it closes
struct WindowWatch: NSViewRepresentable {
    var onWindow: (NSWindow) -> Void = { _ in }
    var onVisible: (NSWindow, Bool) -> Void = { _, _ in }
    var onClose: (NSWindow) -> Void = { _ in }
    func makeNSView(context: Context) -> WatchView { let v = WatchView(); v.watch = self; return v }
    func updateNSView(_ v: WatchView, context: Context) { v.watch = self }

    final class WatchView: NSView {
        var watch = WindowWatch()
        override func viewWillMove(toWindow new: NSWindow?) {
            if let w = window { NotificationCenter.default.removeObserver(self, name: nil, object: w) }
            super.viewWillMove(toWindow: new)
        }
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            guard let w = window else { return }
            watch.onWindow(w)
            for n in [NSWindow.didChangeOcclusionStateNotification, NSWindow.didBecomeKeyNotification, NSWindow.didResignKeyNotification] {
                NotificationCenter.default.addObserver(self, selector: #selector(changed), name: n, object: w)
            }
            NotificationCenter.default.addObserver(self, selector: #selector(closing), name: NSWindow.willCloseNotification, object: w)
            changed()
        }
        // reported on the next turn of the loop, never in the middle of a swiftui update
        @objc func changed() {
            DispatchQueue.main.async { [weak self] in
                guard let self, let w = self.window else { return }
                self.watch.onVisible(w, w.isVisible && w.occlusionState.contains(.visible))
            }
        }
        @objc func closing() { if let w = window { watch.onVisible(w, false); watch.onClose(w) } }
    }
}

struct MenuBarPanel: View {
    @ObservedObject var player: Player
    @ObservedObject var cover: Cover
    @ObservedObject var prefs: Prefs
    @ObservedObject var auto: AutoPick
    @ObservedObject var panel: PanelState
    @ObservedObject var search: SearchState
    var snapshot = false
    var fixedPos: Double? = nil            // snapshot: the moment to draw, instead of the live position
    @FocusState private var searching: Bool

    var tint: Color { let p = cover.palette[0]; return Color(red: Double(p.x), green: Double(p.y), blue: Double(p.z)) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            nowPlaying
            progressLine
            transport
            volume
            scenes
            actions
            searchBox
            footer
        }
        .padding(14).frame(width: 300)
        .font(.system(size: 12, design: .rounded))
        .background(Group { if !snapshot { WindowWatch(onVisible: { _, v in if panel.shown != v { panel.shown = v }; if !v { letGo() } }) } })
        .background(Group { if !snapshot { SearchKeys(search: search, play: pick, dismiss: letGo) } })
        .onAppear { if !snapshot { cover.load(player.track) } }
    }

    var nowPlaying: some View {
        HStack(spacing: 12) {
            Group {
                if let img = cover.image { Image(nsImage: img).resizable().interpolation(.medium).scaledToFill() }
                else { ZStack { Color.primary.opacity(0.08); Image(systemName: "music.note").font(.system(size: 20)).foregroundStyle(.secondary) } }
            }
            .frame(width: 56, height: 56).clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            VStack(alignment: .leading, spacing: 3) {
                Text(player.track.title.isEmpty ? "nothing playing" : player.track.title)
                    .font(.system(size: 14, weight: .semibold, design: .rounded)).lineLimit(2)
                Text(player.track.artist.isEmpty ? (player.connected ? "◐ ytm" : "the player is resting") : player.track.artist)
                    .font(.system(size: 12, design: .rounded)).foregroundStyle(.secondary).lineLimit(1)
                if player.mode == "work" { Text("work mode").font(.system(size: 11, weight: .medium, design: .rounded)).foregroundStyle(pink) }
            }
            Spacer(minLength: 0)
        }
    }

    // the progress line redraws twice a second, and only while the panel is open and the song is moving
    @ViewBuilder var progressLine: some View {
        if let t = fixedPos { bar(t) }
        else if panel.shown && !player.paused { TimelineView(.periodic(from: .now, by: 0.5)) { _ in bar(player.position) } }
        else { bar(player.position) }
    }

    func bar(_ pos: Double) -> some View {
        VStack(spacing: 4) {
            GeometryReader { g in
                let f = player.duration > 0 ? CGFloat(min(1, max(0, pos / player.duration))) : 0
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.primary.opacity(0.15)).frame(height: 3)
                    Capsule().fill(tint).frame(width: max(3, g.size.width * f), height: 3)
                }
                .frame(height: 10).contentShape(Rectangle())
                .gesture(DragGesture(minimumDistance: 0).onEnded { v in
                    if player.duration > 0 { player.seek(Double(max(0, min(1, v.location.x / g.size.width))) * player.duration) }
                })
            }.frame(height: 10)
            HStack { Text(clock(pos)); Spacer(); Text(clock(player.duration)) }
                .font(.system(size: 10, design: .monospaced)).foregroundStyle(.secondary)
        }
    }

    var transport: some View {
        HStack(spacing: 24) {
            Spacer()
            Button(action: player.back) { Image(systemName: "backward.fill").font(.system(size: 15)) }.help("back")
            Button(action: player.togglePause) {
                Image(systemName: player.paused ? "play.fill" : "pause.fill").font(.system(size: 18))
                    .frame(width: 40, height: 40).background(Color.primary.opacity(0.1), in: Circle())
            }.help(player.paused ? "play" : "pause")
            Button(action: player.next) { Image(systemName: "forward.fill").font(.system(size: 15)) }.help("next")
            Button(action: player.like) {
                Image(systemName: player.rating == "LIKE" ? "heart.fill" : "heart").font(.system(size: 14))
                    .foregroundStyle(player.rating == "LIKE" ? pink : Color.secondary)
            }.help("like this song (again to undo)")
            Button(action: player.dislike) {
                Image(systemName: player.rating == "DISLIKE" ? "hand.thumbsdown.fill" : "hand.thumbsdown").font(.system(size: 13))
                    .foregroundStyle(player.rating == "DISLIKE" ? Color.orange : Color.secondary)
            }.help("not for me: skip it, never queue it again")
            Spacer()
        }
        .buttonStyle(.plain).foregroundStyle(.primary)
    }

    // drawn by hand (no nsslider): lighter, matches the progress line, and it renders in the headless snapshot
    var volume: some View {
        HStack(spacing: 8) {
            Image(systemName: player.volume < 1 ? "speaker.slash.fill" : "speaker.wave.2.fill")
                .font(.system(size: 11)).foregroundStyle(.secondary).frame(width: 16)
            GeometryReader { g in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.primary.opacity(0.15)).frame(height: 4)
                    Capsule().fill(Color.primary.opacity(0.55)).frame(width: max(4, g.size.width * CGFloat(min(100, player.volume) / 100)), height: 4)
                }
                .frame(height: 14).contentShape(Rectangle())
                .gesture(DragGesture(minimumDistance: 0).onChanged { v in
                    let want = (Double(max(0, min(1, v.location.x / g.size.width))) * 100).rounded()
                    if want != player.volume { player.setVolume(want) }
                })
            }.frame(height: 14)
            Text("\(Int(player.volume))").font(.system(size: 10, design: .monospaced)).foregroundStyle(.secondary).frame(width: 24, alignment: .trailing)
        }
    }

    var scenes: some View {
        HStack(spacing: 2) {
            let on = prefs.look == "auto"
            Button { prefs.set(look: "auto") } label: {
                HStack(spacing: 5) { Image(systemName: "wand.and.stars"); if on { Text("auto · \(auto.pick.look.name)").lineLimit(1).fixedSize() } }
                    .font(.system(size: 11, weight: .medium, design: .rounded))
                    .frame(maxWidth: on ? nil : .infinity).padding(.horizontal, on ? 10 : 0).padding(.vertical, 6)
                    .help(on ? auto.pick.reason : "auto · the song picks")
                    .background(on ? Color.primary.opacity(0.14) : .clear, in: Capsule())
                    .foregroundStyle(on ? Color.primary : Color.secondary)
                    .contentShape(Capsule())
            }.buttonStyle(.plain)
            ForEach(Look.allCases, id: \.self) { lk in
                let on = prefs.look == lk.id
                Button { prefs.set(look: lk.id) } label: {
                    HStack(spacing: 5) { Image(systemName: lk.icon); if on { Text(lk.name).lineLimit(1).fixedSize() } }
                        .font(.system(size: 11, weight: .medium, design: .rounded))
                        .frame(maxWidth: on ? nil : .infinity).padding(.horizontal, on ? 10 : 0).padding(.vertical, 6).help(lk.name)
                        .background(on ? Color.primary.opacity(0.14) : .clear, in: Capsule())
                        .foregroundStyle(on ? Color.primary : Color.secondary)
                        .contentShape(Capsule())
                }.buttonStyle(.plain)
            }
        }
        .padding(3).background(Color.primary.opacity(0.06), in: Capsule())
    }

    var actions: some View {
        HStack(spacing: 6) {
            chip(player.mode == "normal" ? "moods" : player.mode, MODE_ICONS[player.mode] ?? "circle.grid.2x2", on: player.mode != "normal") {
                StageWindowController.show(); Hub.shared.surf.open = true; Hub.shared.settings.open = false   // the drawer lives on the stage
            }
            chip("shuffle", "shuffle") { player.shuffleQueue() }
            chip("fresh radio", "dot.radiowaves.left.and.right") { player.ytm(["radio"]) }
            chip("open stage", "macwindow") { StageWindowController.show() }
        }
    }

    func chip(_ name: String, _ icon: String, on: Bool = false, _ act: @escaping () -> Void) -> some View {
        Button(action: act) {
            VStack(spacing: 4) {
                Image(systemName: icon).font(.system(size: 14))
                Text(name).font(.system(size: 11, weight: .medium, design: .rounded)).lineLimit(1)
            }
            .frame(maxWidth: .infinity).padding(.vertical, 8)
            .foregroundStyle(on ? pink : Color.primary)
            .background(Color.primary.opacity(on ? 0.12 : 0.06), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        }.buttonStyle(.plain)
    }

    var searchBox: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass").font(.system(size: 11)).foregroundStyle(.secondary)
            if snapshot {
                if panel.query.isEmpty { Text("search youtube music").foregroundStyle(.tertiary) } else { Text(panel.query) }
                Spacer(minLength: 0)
            } else {
                TextField("search youtube music", text: $panel.query).textFieldStyle(.plain).focused($searching).onSubmit(submit)
            }
            if search.open {
                Button(action: letGo) { Image(systemName: "xmark.circle.fill").font(.system(size: 11)).foregroundStyle(.secondary) }.buttonStyle(.plain).help("close (esc)")
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        // three compact rows, laid over the scenes and the chips above the box: the panel never grows or jumps
        .overlay(alignment: .bottom) { if search.open { SearchResults(search: search, stage: false, play: pick).offset(y: -36) } }
    }

    /// the same rules as the stage: new words search, the same words again play the picked row
    func submit() {
        let q = panel.query.trimmingCharacters(in: .whitespacesAndNewlines)
        if !q.isEmpty, q == search.asked, let h = search.chosen { pick(h) } else { search.run(q) }
    }
    func pick(_ h: SearchHit) { player.ytm(SearchState.playArgs(h)); letGo() }
    func letGo() { search.close(); panel.query = ""; searching = false }

    var footer: some View {
        HStack(spacing: 8) {
            Text(player.status.isEmpty ? (player.connected ? "◐ ytm" : "resting · search to start it") : player.status)
                .font(.system(size: 11, design: .rounded)).foregroundStyle(.secondary).lineLimit(1)
            Spacer(minLength: 4)
            // quitting the app leaves the music playing: mpv is the cli's, not the app's
            Button("quit") { NSApp.terminate(nil) }.buttonStyle(.plain).font(.system(size: 11, design: .rounded))
                .foregroundStyle(.secondary).keyboardShortcut("q").help("quit ytm.app (the music keeps playing)")
        }
    }
}
