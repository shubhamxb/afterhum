// the song, read ahead of time (bin/ytm analyze). 0.8: the backdrop has no frame loop to feed, so this keeps only what it
// schedules from: the tempo, the beat times, the moments (drops / lifts), and the intensity at 2 Hz. the 60 fps bands are
// read once and dropped (an hour mix's ~6 MB of frames becomes ~60 KB).
import Foundation

final class SongAnalysis {
    let vid: String, bpm: Double
    let beats: [Double]                                   // seconds, sorted
    let moments: [(t: Double, s: Float, kind: Float)]   // drops (1) and lifts (0.5): the only big gestures
    private let level: [Float]                            // intensity, one value per 0.5 s
    // the song's shape for the auto look, summed in the same pass (bands are normalised per song: shapes, not loudness)
    let tilt: Double, density: Double, onset: Double, quiet: Double   // bass − treble, mean energy, note starts, share quiet
    var drops: Int { moments.filter { $0.kind > 0.75 }.count }

    init?(vid: String) {
        let dir = DATA.appendingPathComponent("analysis")
        guard let jd = try? Data(contentsOf: dir.appendingPathComponent("\(vid).json")),
              let j = try? JSONSerialization.jsonObject(with: jd) as? [String: Any],
              let d = try? Data(contentsOf: dir.appendingPathComponent("\(vid).u8")) else { return nil }
        let fps = j["fps"] as? Double ?? 60, frames = j["frames"] as? Int ?? 0, width = j["width"] as? Int ?? 29
        guard frames > 0, width >= 29, d.count >= frames * width, fps > 0 else { return nil }
        self.vid = vid; bpm = j["bpm"] as? Double ?? 120
        moments = ((j["moments"] as? [[String: Any]]) ?? []).compactMap { m in
            guard let t = m["t"] as? Double else { return nil }
            return (t, Float(m["s"] as? Double ?? 0.6), (m["kind"] as? String) == "drop" ? 1 : 0.5)
        }.sorted { $0.t < $1.t }
        var b: [Double] = [], lv: [Float] = []
        var low = 0, high = 0, en = 0, on = 0, hush = 0
        let per = max(1, Int(fps / 2))
        d.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            let u = raw.bindMemory(to: UInt8.self)
            var sum = 0, n = 0
            for f in 0..<frames {
                let row = f * width
                if u[row + 28] > 127 { b.append(Double(f) / fps) }
                sum += Int(u[row + 26]); n += 1
                if n == per { lv.append(Float(sum) / Float(n * 255)); sum = 0; n = 0 }
                for k in 0..<8 { low += Int(u[row + k]); high += Int(u[row + 16 + k]) }
                en += Int(u[row + 24]); on += Int(u[row + 25]); if u[row + 26] < 89 { hush += 1 }
            }
            if n > 0 { lv.append(Float(sum) / Float(n * 255)) }
        }
        beats = b; level = lv
        let F = Double(frames)
        tilt = Double(low - high) / (F * 8 * 255); density = Double(en) / (F * 255); onset = Double(on) / (F * 255); quiet = Double(hush) / F
    }

    var bytes: Int { beats.count * 8 + level.count * 4 + moments.count * 16 }

    /// 0…1, how much is going on around t
    func intensity(at t: Double) -> Float { level.isEmpty ? 0.5 : level[max(0, min(level.count - 1, Int(t * 2)))] }

    /// the first beat after t and its number (binary search: an hour mix has ~7000 beats)
    func nextBeat(after t: Double) -> (Double, Int)? {
        var lo = 0, hi = beats.count
        while lo < hi { let mid = (lo + hi) / 2; if beats[mid] <= t { lo = mid + 1 } else { hi = mid } }
        return lo < beats.count ? (beats[lo], lo) : nil
    }
}

/// asks the cli to analyse a song the first time it plays, then hands it to the stage.
/// 10-23 17:49: the first version re-spawned a failed analysis every second and ran one per song in parallel; with hour-long
/// mixes that was ~50 GB and a kernel panic. now: at most ONE analysis process at all, a 3-minute limit, niced, and a song that
/// failed is never retried by this app (bin/ytm parks it for an hour on its side too).
final class AnalysisCache {
    private var cache: [String: SongAnalysis] = [:]
    private var running: String? = nil            // the one analysis in flight, if any
    private var gaveUp = Set<String>()             // tried once this run and got nothing: leave it alone
    private var lastTry: [String: Date] = [:]      // disk checks at most once a second per song

    /// what's already on disk, never a new analysis: the auto look asks at every track change, stage open or not
    func peek(_ vid: String) -> SongAnalysis? {
        if vid.isEmpty { return nil }
        if let a = cache[vid] { return a }
        guard let a = SongAnalysis(vid: vid) else { return nil }
        keep(a); return a
    }

    private func keep(_ a: SongAnalysis) {
        // capped by bytes too (0.6 held an hour mix's ~6 MB of frames each; 0.8 keeps ~60 KB, the cap stays as a guard)
        cache[a.vid] = a
        if cache.count > 8 || cache.values.reduce(0, { $0 + $1.bytes }) > 32 << 20 { cache = [a.vid: a] }
    }

    func get(_ vid: String) -> SongAnalysis? {
        if vid.isEmpty { return nil }
        if let a = cache[vid] { return a }
        if let t = lastTry[vid], Date().timeIntervalSince(t) < 1 { return nil }
        lastTry[vid] = Date()
        if lastTry.count > 64 { lastTry = lastTry.filter { Date().timeIntervalSince($0.value) < 5 } }
        if let a = SongAnalysis(vid: vid) { keep(a); return a }
        guard running == nil, !gaveUp.contains(vid) else { return nil }
        running = vid
        DispatchQueue.global(qos: .background).async {
            let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/nice")
            p.arguments = ["-n", "15", "/usr/bin/env", "python3", YTM, "analyze", vid]
            var env = ProcessInfo.processInfo.environment
            env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (env["PATH"] ?? "")
            p.environment = env; p.standardOutput = FileHandle.nullDevice; p.standardError = FileHandle.nullDevice
            p.qualityOfService = .background
            // no thread sits polling isRunning for 3 minutes: the process says when it's done, a timer stops it at the limit
            p.terminationHandler = { _ in DispatchQueue.main.async { self.finished(vid) } }
            do { try p.run() } catch { DispatchQueue.main.async { self.finished(vid) }; return }
            DispatchQueue.global(qos: .background).asyncAfter(deadline: .now() + 180) { if p.isRunning { p.terminate() } }
        }
        return nil
    }

    private func finished(_ vid: String) {
        if SongAnalysis(vid: vid) == nil { gaveUp.insert(vid) }
        running = nil
    }
}
