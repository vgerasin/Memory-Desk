// swift-tools-version:5.9
// ЧЕРНОВИК: написан без Swift и Xcode; на Mac собирается и открывается, подробная проверка не пройдена. Что проверить — в README.md рядом.
import PackageDescription

let package = Package(
    name: "MemoryDesk",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(name: "MemoryDesk", path: "Sources/MemoryDesk")
    ]
)
