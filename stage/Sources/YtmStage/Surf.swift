// the surf drawer: moods, shuffle, surf and the sound, one tap from the stage.
// everything the cli can do for moods and discovery is one tap here, so the app alone is enough.
// it is the same drawer slot as settings (right side, where up next sits), opened from the top bar or with m. the stage does no
// thinking here: every tap is one `ytm <command>` (mode, rated, likes, pl, explore, radio, for-you, shuffle, surprise, out, cache),
// and the mood list comes from ytm itself, so a mood you make shows up here with no rebuild.
import SwiftUI

/// chips that wrap onto the next line instead of running off the edge
struct FlowLayout: Layout {
    var spacing: CGFloat = 6
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let w = proposal.width ?? 360; var x: CGFloat = 0, y: CGFloat = 0, row: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(.unspecified)
            if x + s.width > w, x > 0 { x = 0; y += row + spacing; row = 0 }
            x += s.width + spacing; row = max(row, s.height)
        }
        return CGSize(width: w, height: y + row)
    }
    func placeSubviews(in b: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = b.minX, y = b.minY, row: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(.unspecified)
            if x + s.width > b.maxX, x > b.minX { x = b.minX; y += row + spacing; row = 0 }
            v.place(at: CGPoint(x: x, y: y), proposal: .unspecified); x += s.width + spacing; row = max(row, s.height)
        }
    }
}

let MODE_ICONS: [String: String] = [
    "work": "moon.stars", "focus": "scope", "coding": "chevron.left.forwardslash.chevron.right", "design": "paintbrush.pointed",
    "writing": "pencil.line", "reading": "book", "study": "graduationcap", "cafe": "cup.and.saucer", "gym": "dumbbell", "run": "figure.run",
    "drive": "car", "cook": "fork.knife", "gaming": "gamecontroller", "party": "party.popper", "morning": "sunrise", "winddown": "moon.haze",
    "chill": "leaf", "energy": "bolt.fill", "sleep": "bed.double"]
let MODE_GROUPS = [("work", "working"), ("move", "doing"), ("wind", "winding down"), ("core", "mood"), ("mine", "yours")]

struct OutDev: Identifiable, Equatable { let id: Int; let name: String; let on: Bool }
struct PlaylistRef: Identifiable, Equatable { let id: String; let title: String; let count: Int }

/// what the drawer shows besides the moods (which live on the player): outputs, the cache, your playlists, the new-mood form.
/// view-local values live in an object: @State is a macro on this sdk and its plugin only ships with full xcode
final class SurfState: ObservableObject {
    @Published var open = false
    @Published var outputs: [OutDev] = []
    @Published var followsDefault = true
    @Published var cacheFiles = 0
    @Published var cacheMB = 0
    @Published var cacheCap = 1024
    @Published var playlists: [PlaylistRef] = []
    @Published var adding = false
    @Published var newName = ""
    @Published var newSeeds = ""
    @Published var newInstrumental = true
    @Published var newVol = 45.0
    @Published var confirmClear = false

    /// ask the cli for the outputs and the cache (a few ms each), read the playlists from the last sync. never in a snapshot.
    func refresh(live: Bool) {
        guard live else { return }
        Accounts.run(["out", "--json"]) { out in
            guard let j = Accounts.json(out) else { return }
            self.followsDefault = (j["current"] as? String ?? "auto") == "auto"
            self.outputs = ((j["devices"] as? [[String: Any]]) ?? []).compactMap { d in
                guard let n = d["n"] as? Int, let name = d["name"] as? String else { return nil }
                return OutDev(id: n, name: name, on: d["on"] as? Bool ?? false)
            }
        }
        Accounts.run(["cache", "--json"]) { out in
            guard let j = Accounts.json(out) else { return }
            self.cacheFiles = j["files"] as? Int ?? 0; self.cacheMB = j["mb"] as? Int ?? 0; self.cacheCap = max(1, j["cap_mb"] as? Int ?? 1024)
        }
        DispatchQueue.global(qos: .utility).async {
            var out: [PlaylistRef] = []
            if let d = try? Data(contentsOf: DATA.appendingPathComponent("account/snapshot.json")),
               let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any], let pls = j["playlists"] as? [[String: Any]] {
                for p in pls {
                    guard let id = p["id"] as? String, id != "LM", !id.isEmpty else { continue }
                    let digits = String(describing: p["count"] ?? "").filter(\.isNumber)
                    out.append(PlaylistRef(id: id, title: p["title"] as? String ?? "", count: Int(digits) ?? 0))
                }
            }
            let top = Array(out.sorted { $0.count > $1.count }.prefix(12))
            DispatchQueue.main.async { self.playlists = top }
        }
    }

    /// seed words for a new mood: letters, digits and a few marks only (the cli would refuse the rest anyway)
    var seedWords: [String] {
        newSeeds.split(whereSeparator: { $0 == " " || $0 == "\n" }).map { w in
            var t = String(w).replacingOccurrences(of: #"[^\p{L}\p{N}_'.&+,-]"#, with: "", options: .regularExpression)
            while t.hasPrefix("-") { t.removeFirst() }
            return String(t.prefix(24))
        }.filter { !$0.isEmpty }
    }
    var nameOK: Bool { newName.range(of: #"^[a-z][a-z0-9-]{1,15}$"#, options: .regularExpression) != nil }
}

struct SurfPanel: View {
    @ObservedObject var s: SurfState
    @ObservedObject var player: Player
    var live = true
    let pink = Color(red: 0.91, green: 0.56, blue: 0.71)

    var body: some View {
        ViewThatFits(in: .vertical) {
            content
            ScrollView(.vertical, showsIndicators: true) { content }.scrollIndicators(.visible)
        }
        .toggleStyle(.switch).font(.system(size: 13, design: .rounded)).foregroundStyle(.white)
        .frame(width: 400)
        .background(Color(red: 0.035, green: 0.04, blue: 0.075), in: RoundedRectangle(cornerRadius: 26, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 26, style: .continuous).strokeBorder(.white.opacity(0.09), lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 26, style: .continuous))
        .shadow(color: .black.opacity(0.45), radius: 30)
        .padding(.bottom, 24)
        .onAppear { s.refresh(live: live) }
        .confirmationDialog("Clear the offline cache?", isPresented: $s.confirmClear) {
            Button("clear \(s.cacheMB) MB", role: .destructive) { go(["cache", "clear"]) }
        } message: { Text("The songs download again as you listen.") }
    }

    /// one ytm command, then the drawer looks again (the output or the cache may have changed)
    func go(_ args: [String]) { guard live else { return }; player.ytm(args) { _ in s.refresh(live: true) } }

    var content: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack {
                Text("moods & surf").font(.system(size: 17, weight: .bold, design: .rounded))
                Spacer()
                Button { withAnimation(.easeOut(duration: 0.25)) { s.open = false } } label: {
                    Image(systemName: "xmark.circle.fill").font(.system(size: 17)).foregroundStyle(.white.opacity(0.45))
                }.buttonStyle(.plain).help("close (m)")
            }
            section("moods", "moon.stars") { moods }
            section("shuffle", "shuffle") { shuffles }
            section("surf", "safari") { surfs }
            section("sound", "hifispeaker") { sound }
        }
        .padding(22)
    }

    // ---- moods: all of them, grouped; the one on is lit, tap it again to turn it off ----
    var moods: some View {
        VStack(alignment: .leading, spacing: 12) {
            FlowLayout { chip("surprise me", "dice") { go(["surprise"]) } }
            ForEach(MODE_GROUPS, id: \.0) { g, title in
                let ms = player.modes.filter { $0.group == g }
                if !ms.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(title).font(.system(size: 11, weight: .semibold, design: .rounded)).opacity(0.4)
                        FlowLayout {
                            ForEach(ms) { m in
                                chip(m.id, MODE_ICONS[m.id] ?? "sparkles", on: player.mode == m.id, help: m.what) { go(["mode", m.id]) }
                                    .contextMenu { if m.group == "mine" { Button("forget \(m.id)") { go(["mode", "forget", m.id]); player.loadModes() } } }
                            }
                        }
                    }
                }
            }
            FlowLayout { chip(s.adding ? "never mind" : "new mood", s.adding ? "xmark" : "plus") { s.adding.toggle() } }
            if s.adding { newMood }
        }
    }

    var newMood: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField("name (a–z, 0–9, dash)", text: $s.newName).textFieldStyle(.plain).padding(9)
                .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            TextField("what to play: ambient, slow jazz, rain piano", text: $s.newSeeds, axis: .vertical).lineLimit(1...3).textFieldStyle(.plain).padding(9)
                .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            HStack(spacing: 10) {
                Toggle("no lyrics", isOn: $s.newInstrumental).controlSize(.small)
                Spacer(minLength: 4)
                Image(systemName: "speaker.wave.2.fill").opacity(0.5)
                Slider(value: $s.newVol, in: 10...100).tint(pink).controlSize(.small).frame(width: 90)
            }
            chip("save this mood", "checkmark", on: s.nameOK && !s.seedWords.isEmpty) {
                guard s.nameOK, !s.seedWords.isEmpty else { return }
                var args: [String] = ["mode", "new", s.newName]
                args.append(contentsOf: s.seedWords.prefix(30))
                if s.newInstrumental { args.append("--instrumental") }
                args.append(contentsOf: ["--vol", String(Int(s.newVol))])
                go(args)
                s.adding = false; s.newName = ""; s.newSeeds = ""
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { player.loadModes() }
            }
        }
    }

    // ---- shuffle: reorder what's queued, or deal a new hand from what you love ----
    var shuffles: some View {
        VStack(alignment: .leading, spacing: 10) {
            FlowLayout {
                chip("shuffle the queue", "shuffle") { go(["shuffle"]) }
                chip("my liked", "heart.fill") { go(["rated", "liked", "--play"]) }
                chip("youtube likes", "music.note.list") { go(["likes"]) }
                chip("offline cache", "arrow.down.circle") { go(["cache", "play"]) }
            }
            if !s.playlists.isEmpty {
                Text("your playlists, shuffled").font(.system(size: 11, weight: .semibold, design: .rounded)).opacity(0.4)
                FlowLayout {
                    ForEach(s.playlists) { p in
                        chip(short(p.title, 22) + (p.count > 0 ? "  \(p.count)" : ""), "play.fill") { go(["pl", "--id", p.id]) }
                    }
                }
            }
        }
    }

    // ---- surf: the map around what you know, not the feed's loop ----
    var surfs: some View {
        VStack(alignment: .leading, spacing: 8) {
            FlowLayout {
                chip("explore", "safari") { go(["explore"]) }
                chip("more like this", "dot.radiowaves.left.and.right") { go(["radio"]) }
                chip("picked for you", "sparkles") { go(["for-you"]) }
            }
            Text("explore: songs you saved and forgot, neighbours of your favourites you've never played, and a wild card.")
                .font(.system(size: 11, design: .rounded)).opacity(0.4).fixedSize(horizontal: false, vertical: true)
        }
    }

    // ---- sound: where it goes, and the offline cache ----
    var sound: some View {
        VStack(alignment: .leading, spacing: 10) {
            FlowLayout {
                chip("the mac's default", "macbook", on: s.followsDefault) { go(["out", "auto"]) }
                ForEach(s.outputs) { o in chip(short(o.name, 22), "speaker.wave.2", on: o.on) { go(["out", "\(o.id)"]) } }
                chip("silent? re-open", "arrow.clockwise") { go(["out", "reload"]) }
            }
            Text("headphones that drop mid-song pause the music instead of switching to the speakers.")
                .font(.system(size: 11, design: .rounded)).opacity(0.4).fixedSize(horizontal: false, vertical: true)
            GeometryReader { g in
                ZStack(alignment: .leading) {
                    Capsule().fill(.white.opacity(0.12))
                    Capsule().fill(pink.opacity(0.8)).frame(width: max(4, g.size.width * min(1, CGFloat(s.cacheMB) / CGFloat(s.cacheCap))))
                }
            }.frame(height: 5)
            HStack {
                Text("offline · \(s.cacheFiles) songs · \(s.cacheMB) of \(s.cacheCap) MB").font(.system(size: 12, design: .rounded)).opacity(0.6)
                Spacer(minLength: 6)
                chip("clear", "trash") { s.confirmClear = true }
            }
        }
    }

    @ViewBuilder func section<C: View>(_ title: String, _ icon: String, @ViewBuilder _ c: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            Label(title, systemImage: icon).font(.system(size: 12, weight: .semibold, design: .rounded)).opacity(0.6)
            c()
        }
    }

    func chip(_ text: String, _ icon: String, on: Bool = false, help: String = "", _ act: @escaping () -> Void) -> some View {
        Button(action: act) {
            HStack(spacing: 5) {
                Image(systemName: icon).font(.system(size: 11))
                Text(text).lineLimit(1).fixedSize()
            }
            .font(.system(size: 12.5, weight: on ? .semibold : .regular, design: .rounded))
            .padding(.horizontal, 11).padding(.vertical, 6)
            .foregroundStyle(on ? pink : .white.opacity(0.88))
            .background(on ? pink.opacity(0.18) : .white.opacity(0.08), in: Capsule())
            .overlay(Capsule().strokeBorder(on ? pink.opacity(0.6) : .clear))
            .contentShape(Capsule())
        }.buttonStyle(.plain).help(help)
    }

    func short(_ t: String, _ n: Int) -> String { t.count <= n ? t : String(t.prefix(n - 1)) + "…" }
}
