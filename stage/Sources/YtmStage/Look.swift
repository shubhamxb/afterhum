// the looks, the accent palettes and motion: ids match DATA/prefs.json (shared with the cli and the terminal ui: lib/looks.py).
import AppKit
import IOKit.ps

enum Look: Int, CaseIterable {
    case dusk, rain, neon, stars, paper, aurora, ember, tide
    static let ids = ["dusk", "rain", "neon", "stars", "paper", "aurora", "ember", "tide"]
    var id: String { Look.ids[rawValue] }
    var name: String { ["dusk", "rain window", "neon drive", "stargaze", "paper", "aurora", "embers", "tide"][rawValue] }
    var icon: String { ["sun.haze", "cloud.rain", "sun.horizon", "sparkles", "doc.plaintext", "wind", "flame", "water.waves"][rawValue] }
    /// the song-reading rules (AutoLook, and its python twin lib/autolook.py) choose among the first five; the rest are for choosing
    var autoPicks: Bool { rawValue < 5 }
    init(id: String) { self = Look(rawValue: Look.ids.firstIndex(of: id) ?? 0) ?? .dusk }
}

enum Accent {
    static let ids = ["cover", "night", "ember", "sea", "mono"]
    static func palette(_ id: String, cover: [SIMD3<Float>]) -> [SIMD3<Float>] {
        switch id {
        case "night": return Cover.night
        case "ember": return [SIMD3(1.0, 0.55, 0.26), SIMD3(0.93, 0.29, 0.36), SIMD3(1.0, 0.82, 0.48)]
        case "sea": return [SIMD3(0.22, 0.76, 0.82), SIMD3(0.22, 0.44, 0.95), SIMD3(0.62, 0.96, 0.86)]
        case "mono": return [SIMD3(0.74, 0.76, 0.82), SIMD3(0.50, 0.52, 0.58), SIMD3(0.95, 0.95, 0.97)]
        default: return cover.count >= 3 ? cover : Cover.night
        }
    }
}

enum Motion {
    /// still stops everything but the crossfade; lively runs ~1.7× the calm speed
    static func factor(_ id: String) -> Double { id == "still" ? 0 : id == "lively" ? 1.7 : 1 }
}

/// on battery? checked at most every 20 s, and again the moment the power source changes
enum Power {
    private static var last = Date.distantPast, cached = false
    private static var onFlip: (() -> Void)?, source: CFRunLoopSource?
    /// plugging in or out calls f (on main). the notification also fires on every percent tick: only a flip gets through
    static func watch(_ f: @escaping () -> Void) {
        onFlip = f
        guard source == nil, let s = IOPSNotificationCreateRunLoopSource({ _ in
            let was = Power.cached; Power.last = .distantPast
            if Power.onBattery != was { Power.onFlip?() }
        }, nil)?.takeRetainedValue() else { return }
        source = s; CFRunLoopAddSource(CFRunLoopGetMain(), s, .defaultMode)
    }
    static var onBattery: Bool {
        if Date().timeIntervalSince(last) > 20 {
            last = Date()
            let info = IOPSCopyPowerSourcesInfo().takeRetainedValue()
            cached = (IOPSGetProvidingPowerSourceType(info).takeUnretainedValue() as String) == kIOPSBatteryPowerValue
        }
        return cached
    }
}
