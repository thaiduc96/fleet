// Render Fleet's icons: the macOS app icon (app/Fleet.icns) and the dashboard's home-screen
// icons (fleetd/web/icons). The mark is a fleet of sailboats on a Claude-orange sea at dusk,
// the nearest one white and the others fading into the distance.
//   swift scripts/make-icons.swift          # from the repo root
// The favicon in fleetd/web/index.html is the same mark as a hand-written SVG (the small variant).
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

// MARK: - Grid

/// Everything is drawn on Apple's macOS icon grid: a 1024 canvas, y pointing down, with the
/// icon body inset by 100 on every side. Other icons scale this grid so the body fills them.
let canvasSize: CGFloat = 1024
let body = CGRect(x: 100, y: 100, width: 824, height: 824)
let bodyCornerRadius: CGFloat = 185.4

/// Where the sea meets the sky, and how far the swell rises and dips around it.
let horizonY: CGFloat = 640
let swellHeight: CGFloat = 26

// MARK: - Colours

func color(_ hex: UInt32, alpha: CGFloat = 1) -> CGColor {
    CGColor(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: alpha)
}

let skyTop = color(0x1A2B4C)
let skyAtHorizon = color(0x2E4374)
/// Claude orange (#D97757, the dashboard's accent) sits in the middle of the sea's gradient.
let seaTop = color(0xE8906D)
let seaBottom = color(0xC4613F)
let bodyShadowColor = color(0x000000, alpha: 0.32)
let bodyHighlightColor = color(0xFFFFFF, alpha: 0.10)

/// A boat's flat colours. Distant boats take colours closer to the sky's, which is what makes
/// them read as further away without any transparency (overlapping translucent sails smear).
struct BoatColors {
    /// The mainsail shades from its outer edge to the mast.
    let sailOuter: CGColor
    let sailAtMast: CGColor
    let jib: CGColor
    let hull: CGColor

    static let near = BoatColors(sailOuter: color(0xFFFFFF), sailAtMast: color(0xD9E1EE),
                                 jib: color(0xEEF2F8), hull: color(0x121D33))
    static let middle = BoatColors(sailOuter: color(0xC9D2E2), sailAtMast: color(0xAEBACF),
                                   jib: color(0xBFC9DB), hull: color(0x1E2C48))
    static let far = BoatColors(sailOuter: color(0x9AA8C2), sailAtMast: color(0x8696B4),
                                jib: color(0x93A2BE), hull: color(0x2B3B5C))
    static let behind = BoatColors(sailOuter: color(0xB4C0D6), sailAtMast: color(0x9AA8C2),
                                   jib: color(0xA9B6CD), hull: color(0x24345A))
}

// MARK: - Boats

/// A sloop heading right: a mainsail behind the mast, a jib in front of it, a hull below.
/// Every proportion follows from `height`, the mast's height above the sails' foot.
struct Boat {
    let mastX: CGFloat
    /// The y of the sails' foot; nearer boats sit lower, further from the horizon.
    let footY: CGFloat
    let height: CGFloat
    let colors: BoatColors
}

/// Proportions of a boat, as fractions of its height (or of the mainsail's width where noted).
enum BoatShape {
    static let mainsailWidth: CGFloat = 0.58
    /// The mainsail's outer edge bellies out through this point (fractions of width, height).
    static let mainsailBelly = CGPoint(x: 0.72, y: 0.42)
    /// The gap between the sails and the mast, and between the sails and the hull.
    static let gap: CGFloat = 0.035
    /// The jib starts this far down the mast and is this wide at its foot (of mainsail width).
    static let jibTop: CGFloat = 0.16
    static let jibWidth: CGFloat = 0.5
    static let jibBelly = CGPoint(x: 0.42, y: 0.5)
    static let hullDepth: CGFloat = 0.11
    /// The hull's deck and keel run between these points (of mainsail width, from the mast).
    static let deckStern: CGFloat = -1.02
    static let deckBow: CGFloat = 0.62
    static let keelStern: CGFloat = -0.86
    static let keelBow: CGFloat = 0.28
    static let bowCurve: CGFloat = 0.5
}

/// Which boats to draw, furthest first. Sizes up to `smallIconMaxPixels` get two bigger boats,
/// because three boats with two sails each turn to mush at 16 px.
struct FleetStyle {
    let boats: [Boat]

    static let regular = FleetStyle(boats: [
        Boat(mastX: 820, footY: 640, height: 175, colors: .far),
        Boat(mastX: 650, footY: 676, height: 270, colors: .middle),
        Boat(mastX: 400, footY: 744, height: 420, colors: .near),
    ])
    static let small = FleetStyle(boats: [
        Boat(mastX: 790, footY: 660, height: 250, colors: .behind),
        Boat(mastX: 470, footY: 744, height: 480, colors: .near),
    ])
}

let smallIconMaxPixels = 32

func style(forPixels pixels: Int) -> FleetStyle {
    pixels <= smallIconMaxPixels ? .small : .regular
}

// MARK: - Shapes

/// A rounded rectangle with Apple's continuous ("squircle") corners, which plain circular arcs
/// do not match: the curvature eases in over 1.53 × radius along each edge.
func continuousRoundedRect(_ rect: CGRect, radius: CGFloat) -> CGPath {
    let path = CGMutablePath()
    typealias Direction = (dx: CGFloat, dy: CGFloat)
    /// Points are given as multiples of `radius` along the incoming edge (back from the corner)
    /// and along the outgoing edge.
    func addCorner(at corner: CGPoint, incoming: Direction, outgoing: Direction, isFirst: Bool) {
        func point(_ along: CGFloat, _ out: CGFloat) -> CGPoint {
            CGPoint(x: corner.x + (along * incoming.dx + out * outgoing.dx) * radius,
                    y: corner.y + (along * incoming.dy + out * outgoing.dy) * radius)
        }
        if isFirst { path.move(to: point(1.52866483, 0)) } else { path.addLine(to: point(1.52866483, 0)) }
        path.addCurve(to: point(0.66993427, 0.06549600),
                      control1: point(1.08849323, 0), control2: point(0.86840689, 0))
        path.addLine(to: point(0.63149399, 0.07491100))
        path.addCurve(to: point(0.07491100, 0.63149399),
                      control1: point(0.37282392, 0.16905899), control2: point(0.16905899, 0.37282392))
        path.addLine(to: point(0.06549600, 0.66993427))
        path.addCurve(to: point(0, 1.52866483), control1: point(0, 0.86840689), control2: point(0, 1.08849323))
    }
    addCorner(at: CGPoint(x: rect.maxX, y: rect.minY), incoming: (-1, 0), outgoing: (0, 1), isFirst: true)
    addCorner(at: CGPoint(x: rect.maxX, y: rect.maxY), incoming: (0, -1), outgoing: (-1, 0), isFirst: false)
    addCorner(at: CGPoint(x: rect.minX, y: rect.maxY), incoming: (1, 0), outgoing: (0, -1), isFirst: false)
    addCorner(at: CGPoint(x: rect.minX, y: rect.minY), incoming: (0, 1), outgoing: (1, 0), isFirst: false)
    path.closeSubpath()
    return path
}

func fillGradient(_ ctx: CGContext, _ colors: [CGColor], from start: CGPoint, to end: CGPoint) {
    let gradient = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB),
                              colors: colors as CFArray, locations: nil)!
    ctx.drawLinearGradient(gradient, start: start, end: end,
                           options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
}

func fill(_ ctx: CGContext, _ path: CGPath, with fillColor: CGColor) {
    ctx.addPath(path)
    ctx.setFillColor(fillColor)
    ctx.fillPath()
}

/// Shadows are measured in device pixels, not in the scaled 1024 grid, so scale them by hand;
/// otherwise a 16 px icon gets the 10 px drop of the 1024 px one.
func setShadow(_ ctx: CGContext, drop: CGFloat, blur: CGFloat, color: CGColor) {
    let pixelsPerUnit = ctx.ctm.a
    ctx.setShadow(offset: CGSize(width: 0, height: -drop * pixelsPerUnit), blur: blur * pixelsPerUnit, color: color)
}

// MARK: - The scene

func drawSky(_ ctx: CGContext) {
    fillGradient(ctx, [skyTop, skyAtHorizon], from: CGPoint(x: body.midX, y: body.minY),
                 to: CGPoint(x: body.midX, y: horizonY))
}

/// The sea runs far past the body on three sides, so it still reaches the edges when a
/// maskable icon shrinks the scene into its safe zone.
func drawSea(_ ctx: CGContext) {
    let farOutside: CGFloat = 1000
    let sea = CGMutablePath()
    sea.move(to: CGPoint(x: -farOutside, y: horizonY))
    sea.addLine(to: CGPoint(x: body.minX, y: horizonY))
    // Two swells across the body: up, down, up, down.
    func alongBody(_ fraction: CGFloat) -> CGFloat { body.minX + body.width * fraction }
    sea.addCurve(to: CGPoint(x: body.midX, y: horizonY),
                 control1: CGPoint(x: alongBody(0.17), y: horizonY - swellHeight),
                 control2: CGPoint(x: alongBody(0.33), y: horizonY + swellHeight))
    sea.addCurve(to: CGPoint(x: body.maxX, y: horizonY),
                 control1: CGPoint(x: alongBody(0.67), y: horizonY - swellHeight),
                 control2: CGPoint(x: alongBody(0.83), y: horizonY + swellHeight))
    sea.addLine(to: CGPoint(x: canvasSize + farOutside, y: horizonY))
    sea.addLine(to: CGPoint(x: canvasSize + farOutside, y: canvasSize + farOutside))
    sea.addLine(to: CGPoint(x: -farOutside, y: canvasSize + farOutside))
    sea.closeSubpath()
    ctx.saveGState()
    ctx.addPath(sea)
    ctx.clip()
    fillGradient(ctx, [seaTop, seaBottom], from: CGPoint(x: body.midX, y: horizonY),
                 to: CGPoint(x: body.midX, y: body.maxY))
    ctx.restoreGState()
}

func drawBoat(_ ctx: CGContext, _ boat: Boat) {
    let x = boat.mastX, foot = boat.footY, height = boat.height
    let top = foot - height
    let width = height * BoatShape.mainsailWidth
    let gap = height * BoatShape.gap

    let mainsail = CGMutablePath()
    mainsail.move(to: CGPoint(x: x, y: top))
    mainsail.addQuadCurve(to: CGPoint(x: x - width, y: foot),
                          control: CGPoint(x: x - width * BoatShape.mainsailBelly.x,
                                           y: top + height * BoatShape.mainsailBelly.y))
    mainsail.addLine(to: CGPoint(x: x, y: foot))
    mainsail.closeSubpath()
    ctx.saveGState()
    ctx.addPath(mainsail)
    ctx.clip()
    fillGradient(ctx, [boat.colors.sailOuter, boat.colors.sailAtMast],
                 from: CGPoint(x: x - width, y: foot), to: CGPoint(x: x, y: foot))
    ctx.restoreGState()

    let jibLuff = x + gap
    let jib = CGMutablePath()
    jib.move(to: CGPoint(x: jibLuff, y: top + height * BoatShape.jibTop))
    jib.addQuadCurve(to: CGPoint(x: jibLuff + width * BoatShape.jibWidth, y: foot),
                     control: CGPoint(x: jibLuff + width * BoatShape.jibBelly.x, y: top + height * BoatShape.jibBelly.y))
    jib.addLine(to: CGPoint(x: jibLuff, y: foot))
    jib.closeSubpath()
    fill(ctx, jib, with: boat.colors.jib)

    let deck = foot + gap, keel = deck + height * BoatShape.hullDepth
    let hull = CGMutablePath()
    hull.move(to: CGPoint(x: x + width * BoatShape.deckStern, y: deck))
    hull.addLine(to: CGPoint(x: jibLuff + width * BoatShape.deckBow, y: deck))
    hull.addQuadCurve(to: CGPoint(x: x + width * BoatShape.keelBow, y: keel),
                      control: CGPoint(x: x + width * BoatShape.bowCurve, y: keel))
    hull.addLine(to: CGPoint(x: x + width * BoatShape.keelStern, y: keel))
    hull.closeSubpath()
    fill(ctx, hull, with: boat.colors.hull)
}

/// The sea and the boats; the sky is drawn separately because it never scales.
func drawScene(_ ctx: CGContext, style: FleetStyle) {
    drawSea(ctx)
    for boat in style.boats {
        drawBoat(ctx, boat)
    }
}

// MARK: - Icon variants

/// The macOS app icon: a squircle body with a drop shadow, on a transparent canvas.
func drawMacIcon(_ ctx: CGContext, style: FleetStyle) {
    let bodyPath = continuousRoundedRect(body, radius: bodyCornerRadius)
    ctx.saveGState()
    setShadow(ctx, drop: 10, blur: 22, color: bodyShadowColor)
    fill(ctx, bodyPath, with: skyTop)
    ctx.restoreGState()

    ctx.saveGState()
    ctx.addPath(bodyPath)
    ctx.clip()
    drawSky(ctx)
    drawScene(ctx, style: style)
    ctx.restoreGState()

    // A hairline of light along the edge, as on Apple's own icons.
    ctx.addPath(continuousRoundedRect(body.insetBy(dx: 2, dy: 2), radius: bodyCornerRadius - 2))
    ctx.setLineWidth(3)
    ctx.setStrokeColor(bodyHighlightColor)
    ctx.strokePath()
}

enum WebIconShape {
    /// For iOS, which rounds icons itself, and Android's maskable icons.
    case square
    /// For browsers that show icons as they are.
    case rounded
}

/// The body filling the whole image. `sceneScale` < 1 keeps the boats inside a maskable
/// icon's safe zone.
func drawWebIcon(_ ctx: CGContext, shape: WebIconShape, sceneScale: CGFloat) {
    let bleed = canvasSize / body.width
    ctx.translateBy(x: canvasSize / 2, y: canvasSize / 2)
    ctx.scaleBy(x: bleed, y: bleed)
    ctx.translateBy(x: -canvasSize / 2, y: -canvasSize / 2)
    if shape == .rounded {
        ctx.addPath(continuousRoundedRect(body, radius: bodyCornerRadius))
        ctx.clip()
    }
    drawSky(ctx)
    ctx.translateBy(x: canvasSize / 2, y: canvasSize / 2)
    ctx.scaleBy(x: sceneScale, y: sceneScale)
    ctx.translateBy(x: -canvasSize / 2, y: -canvasSize / 2)
    drawScene(ctx, style: .regular)
}

// MARK: - Output

func render(pixels: Int, _ draw: (CGContext) -> Void) -> CGImage {
    let ctx = CGContext(data: nil, width: pixels, height: pixels, bitsPerComponent: 8, bytesPerRow: 0,
                        space: CGColorSpace(name: CGColorSpace.sRGB)!,
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    let scale = CGFloat(pixels) / canvasSize
    // Flip to y-down so the geometry above reads like the SVG favicon.
    ctx.translateBy(x: 0, y: CGFloat(pixels))
    ctx.scaleBy(x: scale, y: -scale)
    draw(ctx)
    return ctx.makeImage()!
}

func writePNG(_ image: CGImage, to url: URL) throws {
    guard let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)
    else { throw IconError.cannotWrite(url.path) }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else { throw IconError.cannotWrite(url.path) }
}

enum IconError: Error {
    case cannotWrite(String)
    case iconutilFailed(Int32)
}

/// Every size macOS looks up in an .icns, by point size; each also comes at 2×.
let macIconPointSizes = [16, 32, 128, 256, 512]

func writeMacIcon(to icns: URL) throws {
    let iconset = FileManager.default.temporaryDirectory
        .appendingPathComponent("Fleet-\(UUID().uuidString).iconset")
    try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: iconset) }
    for points in macIconPointSizes {
        for (pixels, suffix) in [(points, ""), (points * 2, "@2x")] {
            let image = render(pixels: pixels) { drawMacIcon($0, style: style(forPixels: pixels)) }
            try writePNG(image, to: iconset.appendingPathComponent("icon_\(points)x\(points)\(suffix).png"))
        }
    }
    let iconutil = Process()
    iconutil.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
    iconutil.arguments = ["--convert", "icns", "--output", icns.path, iconset.path]
    try iconutil.run()
    iconutil.waitUntilExit()
    guard iconutil.terminationStatus == 0 else { throw IconError.iconutilFailed(iconutil.terminationStatus) }
    print("wrote \(icns.lastPathComponent)")
}

/// Android crops maskable icons to as little as a circle 80 % of the icon's width.
let maskableSafeZone: CGFloat = 0.8

func writeWebIcons(to directory: URL) throws {
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    let icons: [(name: String, pixels: Int, shape: WebIconShape, sceneScale: CGFloat)] = [
        ("apple-touch-icon.png", 180, .square, 1),
        ("icon-192.png", 192, .rounded, 1),
        ("icon-512.png", 512, .rounded, 1),
        ("icon-maskable-512.png", 512, .square, maskableSafeZone),
    ]
    for icon in icons {
        let image = render(pixels: icon.pixels) {
            drawWebIcon($0, shape: icon.shape, sceneScale: icon.sceneScale)
        }
        try writePNG(image, to: directory.appendingPathComponent(icon.name))
        print("wrote \(directory.lastPathComponent)/\(icon.name)")
    }
}

let repo = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
try writeMacIcon(to: repo.appendingPathComponent("app/Fleet.icns"))
try writeWebIcons(to: repo.appendingPathComponent("fleetd/web/icons"))
