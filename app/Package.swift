// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "Fleet",
    platforms: [.macOS(.v14)],
    targets: [
        // Pure logic, unit-tested: invite codes, versions, release parsing, fleet summaries.
        .target(name: "FleetCore"),
        // The menu bar app: supervises fleetd, shows the dashboard, pairs machines, updates itself.
        .executableTarget(name: "Fleet", dependencies: ["FleetCore"]),
        .testTarget(name: "FleetCoreTests", dependencies: ["FleetCore"]),
    ]
)
