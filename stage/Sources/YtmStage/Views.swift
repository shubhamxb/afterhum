// the glass over the stage. stillness hides it: after a few seconds without the mouse, only the visuals and a quiet title stay.
// 0.8: the glass is its own layer. the backdrop (BackdropView, appkit + core animation) sits underneath in the same window, so
// nothing here redraws because the background moved, and nothing in the background notices the glass.
import SwiftUI

struct Glass: ViewModifier {
    var snapshot = false; var radius: CGFloat = 20
    func body(content: Content) -> some View {
        if snapshot { content.background(Color.black.opacity(0.32), in: RoundedRectangle(cornerRadius: radius, style: .continuous)) }
        // no live blur: the stage behind is already soft light, so a tint reads as glass, and the window server never
        // has to re-blur 8 frames a second (10-23: live materials over an animated layer were part of the heat)
        else { content.background(Color(red: 0.04, green: 0.05, blue: 0.10).opacity(0.42), in: RoundedRectangle(cornerRadius: radius, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: radius, style: .continuous).strokeBorder(.white.opacity(0.08), lineWidth: 1)) }
    }
}
extension View { func glass(_ snap: Bool = false, _ r: CGFloat = 20) -> some View { modifier(Glass(snapshot: snap, radius: r)) } }

/// ui state lives in an object: @State is a macro on this sdk, and its plugin only ships with full xcode
final class UIState: ObservableObject {
    @Published var query = ""
    @Published var showUI = true
    @Published var visible = true          // the stage window is on screen: the progress line only ticks then
    @Published var bpm: Int? = nil         // set by the backdrop when the song's analysis is in (no lookups inside body)
    var lastMove = Date()                  // plain: a mouse move must not re-evaluate the whole view
    var searching = false
}

let hhmm: DateFormatter = { let f = DateFormatter(); f.dateFormat = "HH:mm"; return f }()

func clock(_ s: Double) -> String { let s = max(0, Int(s)); return String(format: "%d:%02d", s / 60, s % 60) }

struct RootView: View {
    @ObservedObject var player: Player
    @ObservedObject var cover: Cover
    @ObservedObject var prefs: Prefs
    @ObservedObject var auto: AutoPick
    var snapshot = false

    @ObservedObject var ui: UIState
    @ObservedObject var settings: StageSettings
    @ObservedObject var surf: SurfState
    @ObservedObject var search: SearchState
    @FocusState private var searching: Bool

    // no background: the window's backdrop shows through. stillness (hiding the glass) is the window controller's: it sees
    // every move, click and key (MenuBar.swift wake) and checks once a second, only while the window is visible
    var body: some View {
        // the width decides the layout (never the settings or the glass): narrow windows drop the up-next list and take a
        // smaller cover, so nothing is ever wider than the window (980 is the stage's smallest)
        GeometryReader { g in ZStack {
            Color.clear
            overlay(compact: g.size.width < 1280)
            if settings.clock && !settings.open && !surf.open { ambient }
            if surf.open && !settings.open {
                HStack { Spacer(); SurfPanel(s: surf, player: player, live: !snapshot).padding(.trailing, 24).padding(.top, 64) }
                    .frame(maxHeight: .infinity, alignment: .top).transition(.move(edge: .trailing).combined(with: .opacity))
            }
            if settings.open {
                HStack { Spacer(); SettingsPanel(s: settings, prefs: prefs, auto: auto, cover: cover, player: snapshot ? nil : player, live: !snapshot).padding(.trailing, 24).padding(.top, 64) }
                    .frame(maxHeight: .infinity, alignment: .top).transition(.move(edge: .trailing).combined(with: .opacity))
            }
        }.frame(width: g.size.width, height: g.size.height) }
        .onChange(of: searching) { _, v in ui.searching = v }
        .onChange(of: search.hits) { _, h in if !h.isEmpty && !snapshot { MainActor.assumeIsolated { StageWindowController.current?.wake() } } }   // results are activity
        .onPreferenceChange(ArtFrame.self) { f in
            let moved = abs(f.midX - ArtSpot.frame.midX) > 12 || abs(f.midY - ArtSpot.frame.midY) > 12
            ArtSpot.frame = f
            if moved && !snapshot { MainActor.assumeIsolated { StageWindowController.current?.backdrop.artMoved() } }
        }
        .background(snapshot ? nil : KeyCatcher(player: player, focusSearch: { searching = true }, toggleSurf: { withAnimation(.easeOut(duration: 0.25)) { surf.open.toggle(); if surf.open { settings.open = false } } }))
        .background(snapshot ? nil : SearchKeys(search: search, play: pick, dismiss: letGo))
    }

    // nothing here runs on a clock but the progress line (2 a second, only while visible and playing)
    func overlay(compact: Bool) -> some View {
        VStack(spacing: 0) {
            topBar.opacity(ui.showUI ? 1 : 0).zIndex(1)   // the results hang below the bar: drawn over the cover and the title
            Spacer(minLength: 20)
            HStack(alignment: .center, spacing: compact ? 32 : 44) {
                art(compact ? 260 : 340)   // no per-beat pulse: the backdrop owns the motion, the cover stays still (10-23: "sonic boom no matter what")
                info
                if !compact {
                    // always laid out, only faded: opening settings or hiding the glass must not slide the cover and title over
                    upNext.opacity(ui.showUI && !settings.open && !surf.open ? 1 : 0).allowsHitTesting(ui.showUI && !settings.open && !surf.open)
                        .animation(.easeInOut(duration: 0.3), value: settings.open || surf.open)
                }
            }
            .padding(.horizontal, compact ? 40 : 56)
            Spacer(minLength: 20)
            Text(player.status).font(.system(size: 12, design: .rounded)).foregroundStyle(.white.opacity(0.45))
                .lineLimit(1).padding(.bottom, 18).opacity(ui.showUI ? 1 : 0)
        }
    }

    /// what stays when the glass hides: a big quiet clock, the date, and one line of what's playing
    var ambient: some View {
        VStack {
            HStack {
                Spacer()
                TimelineView(.periodic(from: .now, by: 15)) { tl in
                    VStack(alignment: .trailing, spacing: 2) {
                        Text(hhmm.string(from: tl.date))   // 24-hour, always (.dateTime with amPM omitted still counts in 12)
                            .font(.system(size: ui.showUI ? 28 : 64, weight: .thin, design: .rounded))
                        Text(tl.date, format: .dateTime.weekday(.wide).day().month(.wide))
                            .font(.system(size: ui.showUI ? 11 : 15, weight: .medium, design: .rounded)).opacity(0.6)
                    }.foregroundStyle(.white.opacity(0.85)).shadow(color: .black.opacity(0.35), radius: 12)
                }
            }
            .padding(.top, ui.showUI ? 58 : 40).padding(.trailing, 36)
            Spacer()
            HStack(spacing: 10) {
                if !player.track.title.isEmpty && !ui.showUI {
                    Image(systemName: player.paused ? "pause.fill" : "waveform").font(.system(size: 12))
                    Text("\(player.track.title) · \(player.track.artist)").font(.system(size: 14, weight: .medium, design: .rounded)).lineLimit(1)
                }
                Spacer()
            }
            .foregroundStyle(.white.opacity(0.7)).padding(.leading, 36).padding(.bottom, 30)
        }
        .allowsHitTesting(false)
        .animation(ui.showUI ? .easeOut(duration: 0.3) : .easeInOut(duration: 0.8), value: ui.showUI)   // in step with the glass: back fast, away slow
    }

    var sceneChip: some View {
        HStack(spacing: 2) {
            // auto first: the song picks, and says which look and why (the tooltip). a chosen look always wins over it
            let on = prefs.look == "auto"
            Button { prefs.set(look: "auto") } label: {
                HStack(spacing: 6) { Image(systemName: "wand.and.stars"); if on { Text("auto · \(auto.pick.look.name)").lineLimit(1).fixedSize() } }
                    .font(.system(size: 12, weight: .medium, design: .rounded))
                    .padding(.horizontal, 11).padding(.vertical, 7).help(on ? auto.pick.reason : "auto · the song picks")
                    .background(on ? Color.white.opacity(0.16) : .clear, in: Capsule())
                    .foregroundStyle(.white.opacity(on ? 0.95 : 0.6))
            }.buttonStyle(.plain)
            ForEach(Look.allCases, id: \.self) { lk in
                let on = prefs.look == lk.id
                Button { prefs.set(look: lk.id) } label: {
                    // icons for all, the name only on the chosen one, on one line: nothing wraps
                    HStack(spacing: 6) { Image(systemName: lk.icon); if on { Text(lk.name).lineLimit(1).fixedSize() } }
                        .font(.system(size: 12, weight: .medium, design: .rounded))
                        .padding(.horizontal, 11).padding(.vertical, 7).help(lk.name)
                        .background(on ? Color.white.opacity(0.16) : .clear, in: Capsule())
                        .foregroundStyle(.white.opacity(on ? 0.95 : 0.6))
                }.buttonStyle(.plain)
            }
        }.padding(3).glass(snapshot, 18)
    }

    var topBar: some View {
        HStack(spacing: 12) {
            Text("◐ ytm").font(.system(size: 15, weight: .semibold, design: .rounded)).foregroundStyle(Color(red: 0.91, green: 0.56, blue: 0.71))
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundStyle(.white.opacity(0.6))
                TextField("search youtube music", text: $ui.query)
                    .textFieldStyle(.plain).focused($searching).foregroundStyle(.white)
                    .onSubmit(submit)
                if search.open && !snapshot {
                    Button(action: letGo) { Image(systemName: "xmark.circle.fill").foregroundStyle(.white.opacity(0.45)) }.buttonStyle(.plain).help("close (esc)")
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 9).glass(snapshot, 18).frame(maxWidth: 420)
            // an overlay, not a row: three cards drop over the stage and nothing under them moves
            .overlay(alignment: .topLeading) {
                if search.open && ui.showUI { SearchResults(search: search, play: pick).offset(y: 46).transition(.opacity) }
            }
            .animation(.easeOut(duration: 0.15), value: search.open)
            Button { withAnimation(.easeOut(duration: 0.25)) { surf.open.toggle(); if surf.open { settings.open = false } } } label: {
                Label(player.mode == "normal" ? "moods" : player.mode, systemImage: MODE_ICONS[player.mode] ?? "circle.grid.2x2")
                    .font(.system(size: 13, weight: .medium, design: .rounded))
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .foregroundStyle(player.mode != "normal" ? Color(red: 0.91, green: 0.56, blue: 0.71) : .white.opacity(0.8))
            }.buttonStyle(.plain).glass(snapshot, 18).help("moods, shuffle and surf (m)")
            sceneChip
            Spacer()
            Button { withAnimation(.easeOut(duration: 0.25)) { settings.open.toggle(); if settings.open { surf.open = false } } } label: {
                Image(systemName: "gearshape.fill").font(.system(size: 14)).padding(9).foregroundStyle(.white.opacity(0.8))
            }.buttonStyle(.plain).glass(snapshot, 18).help("settings")
            if !player.connected { Text("player is resting · search to start it").font(.system(size: 12, design: .rounded)).foregroundStyle(.white.opacity(0.5)) }
        }
        .padding(.leading, 86).padding(.trailing, 24).padding(.top, 14)
    }

    /// enter: new words search (three cards, nothing plays yet); enter again on the same words plays the one picked
    func submit() {
        let q = ui.query.trimmingCharacters(in: .whitespacesAndNewlines)
        if !q.isEmpty, q == search.asked, let h = search.chosen { pick(h) } else { search.run(q) }
    }

    /// a card played: the box empties and lets go of the keyboard, so space is play/pause again and the glass can rest
    func pick(_ h: SearchHit) { player.ytm(SearchState.playArgs(h)); letGo() }

    func letGo() { search.close(); ui.query = ""; searching = false }

    func art(_ side: CGFloat) -> some View {
        Group {
            if let img = cover.image { Image(nsImage: img).resizable().scaledToFill() }
            else { ZStack { Color.white.opacity(0.06); Image(systemName: "music.note").font(.system(size: 60)).foregroundStyle(.white.opacity(0.25)) } }
        }
        .frame(width: side, height: side)
        .clipShape(RoundedRectangle(cornerRadius: 26, style: .continuous))
        .shadow(color: .black.opacity(0.55), radius: 40, y: 18)
        .background(GeometryReader { g in Color.clear.preference(key: ArtFrame.self, value: g.frame(in: .global)) })
    }

    var info: some View {
        VStack(alignment: .leading, spacing: 10) {
            // a soft shadow under the words: every look stays readable, even neon drive's sun behind them
            Group {
                Text(player.track.title.isEmpty ? "nothing playing" : player.track.title)
                    .font(.system(size: 42, weight: .bold, design: .rounded)).foregroundStyle(.white).lineLimit(2)
                Text(player.track.artist).font(.system(size: 22, weight: .medium, design: .rounded)).foregroundStyle(.white.opacity(0.82)).lineLimit(1)
            }.shadow(color: .black.opacity(0.45), radius: 10, y: 2)
            HStack(spacing: 10) {
                if !player.track.album.isEmpty { Text(player.track.album).lineLimit(1) }
                if let b = ui.bpm { Text("· \(b) bpm") }
                if player.mode != "normal" { Text("· \(player.mode) mode") }
            }.font(.system(size: 14, design: .rounded)).foregroundStyle(.white.opacity(0.5))
            progressLine.padding(.top, 14).opacity(ui.showUI ? 1 : 0.35)
            controls.padding(.top, 6).opacity(ui.showUI ? 1 : 0)
        }
        .frame(maxWidth: 560, alignment: .leading)
    }

    // the only clock in the glass: twice a second, and only while the window is on screen and the song is moving
    @ViewBuilder var progressLine: some View {
        if ui.visible && !player.paused && !snapshot { TimelineView(.periodic(from: .now, by: 0.5)) { _ in progress(pos: player.position) } }
        else { progress(pos: player.position) }
    }

    func progress(pos: Double) -> some View {
        VStack(spacing: 6) {
            GeometryReader { g in
                let f = player.duration > 0 ? CGFloat(pos / player.duration) : 0
                ZStack(alignment: .leading) {
                    Capsule().fill(.white.opacity(0.16)).frame(height: 4)
                    Capsule().fill(Color(red: Double(cover.palette[0].x), green: Double(cover.palette[0].y), blue: Double(cover.palette[0].z)))
                        .frame(width: max(4, g.size.width * min(1, f)), height: 4)
                }
                .frame(height: 14).contentShape(Rectangle())
                .gesture(DragGesture(minimumDistance: 0).onEnded { v in
                    if player.duration > 0 { player.seek(Double(max(0, min(1, v.location.x / g.size.width))) * player.duration) }
                })
            }.frame(height: 14)
            HStack { Text(clock(pos)); Spacer(); Text(clock(player.duration)) }
                .font(.system(size: 12, design: .monospaced)).foregroundStyle(.white.opacity(0.5))
        }
    }

    var controls: some View {
        HStack(spacing: 26) {
            Button(action: player.back) { Image(systemName: "backward.fill").font(.system(size: 22)) }
            Button(action: player.togglePause) {
                Image(systemName: player.paused ? "play.fill" : "pause.fill").font(.system(size: 26))
                    .frame(width: 62, height: 62).background(.white.opacity(0.14), in: Circle())
            }
            Button(action: player.next) { Image(systemName: "forward.fill").font(.system(size: 22)) }
            Button(action: player.like) {
                Image(systemName: player.rating == "LIKE" ? "heart.fill" : "heart").font(.system(size: 20))
                    .foregroundStyle(player.rating == "LIKE" ? Color(red: 0.91, green: 0.56, blue: 0.71) : .white.opacity(0.85))
            }.help("like this song: it lifts the artist (l)")
            Button(action: player.dislike) {
                Image(systemName: player.rating == "DISLIKE" ? "hand.thumbsdown.fill" : "hand.thumbsdown").font(.system(size: 18))
                    .foregroundStyle(player.rating == "DISLIKE" ? Color.orange : .white.opacity(0.85))
            }.help("not for me: skip it and never queue it again (x)")
            Button(action: player.shuffleQueue) { Image(systemName: "shuffle").font(.system(size: 17)) }.help("shuffle the queue (s)")
            Spacer().frame(width: 10)
            Image(systemName: "speaker.wave.2.fill").foregroundStyle(.white.opacity(0.6))
            Slider(value: Binding(get: { player.volume }, set: { player.setVolume($0) }), in: 0...100).frame(width: 130).tint(.white.opacity(0.7))
            Button { player.ytm(["radio"]) } label: { Image(systemName: "dot.radiowaves.left.and.right").font(.system(size: 16)) }.help("fresh radio from this song")
        }
        .buttonStyle(.plain).foregroundStyle(.white)
    }

    var upNext: some View {
        let cur = player.queue.firstIndex { $0.current } ?? -1
        let items = Array(player.queue.dropFirst(cur + 1).prefix(9))
        return VStack(alignment: .leading, spacing: 6) {
            Text("up next").font(.system(size: 12, weight: .semibold, design: .rounded)).foregroundStyle(.white.opacity(0.5)).padding(.bottom, 4)
            if items.isEmpty { Text("search, or press m for moods").font(.system(size: 13, design: .rounded)).foregroundStyle(.white.opacity(0.4)) }
            ForEach(items) { it in
                Button { player.jump(it.id) } label: {
                    Text(it.title).font(.system(size: 13, design: .rounded)).foregroundStyle(.white.opacity(0.85)).lineLimit(1)
                        .frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 3)
                }.buttonStyle(.plain)
            }
        }
        .padding(18).frame(width: 280, alignment: .leading).glass(snapshot, 22)
    }
}

/// space / ←→ / f / "/" without needing a focused control
struct KeyCatcher: NSViewRepresentable {
    let player: Player; let focusSearch: () -> Void; var toggleSurf: () -> Void = {}
    func makeNSView(context: Context) -> NSView {
        let v = NSView()
        context.coordinator.monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak v] e in
            guard let v, e.window === v.window else { return e }                // the stage's keys, not the menu-bar panel's
            MainActor.assumeIsolated { StageWindowController.current?.wake() }   // any key brings the glass back, typing too
            if e.modifierFlags.contains(.command) {                               // no file menu: ⌘w closes the stage here
                if e.charactersIgnoringModifiers == "w" { e.window?.performClose(nil); return nil }
                return e
            }
            if let r = e.window?.firstResponder, r is NSTextView { return e }   // typing in search
            switch e.keyCode {
            case 49: player.togglePause(); return nil                 // space
            case 124: player.next(); return nil                       // →
            case 123: player.back(); return nil                       // ←
            case 3: e.window?.toggleFullScreen(nil); return nil       // f
            case 44: focusSearch(); return nil                        // /
            case 46: toggleSurf(); return nil                         // m: moods, shuffle and surf
            case 37: player.like(); return nil                        // l: like (again to undo)
            case 7: player.dislike(); return nil                      // x: not for me (skips, never queued again)
            case 1: player.shuffleQueue(); return nil                 // s: shuffle the queue
            default: return e
            }
        }
        return v
    }
    func updateNSView(_ v: NSView, context: Context) {}
    // a reopened stage makes a new catcher: the old monitor goes with its view, or every key would land twice
    static func dismantleNSView(_ v: NSView, coordinator: C) { if let m = coordinator.monitor { NSEvent.removeMonitor(m) }; coordinator.monitor = nil }
    func makeCoordinator() -> C { C() }
    final class C { var monitor: Any? }
}

struct ArtFrame: PreferenceKey {
    static var defaultValue = CGRect.zero
    static func reduce(value: inout CGRect, nextValue: () -> CGRect) { let n = nextValue(); if n != .zero { value = n } }
}
