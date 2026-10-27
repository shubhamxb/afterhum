// swift-tools-version:5.9
// ytm stage: the native face of ytm (SwiftUI glass over a Core Animation backdrop; 0.8 dropped Metal). the engine stays mpv + bin/ytm; this is the screen.
import PackageDescription
let package = Package(
    name: "YtmStage",
    platforms: [.macOS(.v14)],
    targets: [.executableTarget(name: "YtmStage", path: "Sources/YtmStage",
                                swiftSettings: [.unsafeFlags(["-Ounchecked"])])]
)
