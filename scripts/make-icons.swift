// Render Fleet's icons: the macOS app icon (app/Fleet.icns) and the dashboard's home-screen
// icons (fleetd/web/icons). The mark is a stack of terminal windows, the front one showing a
// `>_` prompt, on Claude orange: many Claude Code sessions, seen from one place.
//   swift scripts/make-icons.swift          # from the repo root
// The favicon in fleetd/web/index.html is the same mark as a hand-written SVG (the small variant).
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

// MARK: - Geometry

/// Everything is drawn on Apple's macOS icon grid: a 1024 canvas, y pointing down, with the
/// icon body inset by 100 on every side. Other icons scale this grid so the body fills them.
let canvasSize: CGFloat = 1024
let body = CGRect(x: 100, y: 100, width: 824, height: 824)
let bodyCornerRadius: CGFloat = 185.4

/// One way of drawing the stack. Sizes up to `smallIconMaxPixels` get fewer, bolder shapes,
/// because three cards and a thin prompt turn to mush at 16 px.
struct StackStyle {
    let cardSize: CGSize
    let cardCornerRadius: CGFloat
    /// Offset of each card behind the front one: right and up.
    let cardStep: CGFloat
    /// Opacity of the white cards behind the front one, nearest first.
    let backCardOpacities: [CGFloat]
    let promptLineWidth: CGFloat
    /// From the front card's left edge to the chevron's centre.
    let promptInset: CGFloat
    let chevronHalfWidth: CGFloat
    let chevronHalfHeight: CGFloat
    /// From the chevron's centre to where the cursor starts, and the cursor's length.
    let cursorOffset: CGFloat
    let cursorLength: CGFloat
    let hasTitleBarDots: Bool

    static let regular = StackStyle(
        cardSize: CGSize(width: 520, height: 392), cardCornerRadius: 54, cardStep: 62,
        backCardOpacities: [0.58, 0.32], promptLineWidth: 46, promptInset: 150, chevronHalfWidth: 48, chevronHalfHeight: 74,
        cursorOffset: 100, cursorLength: 120, hasTitleBarDots: true)
    static let small = StackStyle(
        cardSize: CGSize(width: 600, height: 460), cardCornerRadius: 70, cardStep: 90,
        backCardOpacities: [0.5], promptLineWidth: 72, promptInset: 162, chevronHalfWidth: 60, chevronHalfHeight: 92,
        cursorOffset: 120, cursorLength: 140, hasTitleBarDots: false)
}

let smallIconMaxPixels = 32

/// Three dots in the front card's corner, like a window's title bar; the prompt moves down to make room.
let titleBarDotRadius: CGFloat = 14
let titleBarDotSpacing: CGFloat = 44
let titleBarFirstDot = CGPoint(x: 56, y: 52)
let titleBarPromptShift: CGFloat = 18

func style(forPixels pixels: Int) -> StackStyle {
    pixels <= smallIconMaxPixels ? .small : .regular
}

// MARK: - Colours

func color(_ hex: UInt32, alpha: CGFloat = 1) -> CGColor {
    CGColor(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: alpha)
}

/// Claude orange (#D97757, the dashboard's accent) sits in the middle of the background gradient.
let backgroundTop = color(0xE8906D)
let backgroundBottom = color(0xC65E3D)
let frontCardTop = color(0x272C34)
let frontCardBottom = color(0x1A1D23)
let promptColor = color(0xE8906D)
let cardShadowColor = color(0x5A2414, alpha: 0.45)
let bodyShadowColor = color(0x000000, alpha: 0.32)
let bodyHighlightColor = color(0xFFFFFF, alpha: 0.10)
let titleBarDotColor = color(0xFFFFFF, alpha: 0.22)

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

func fillVerticalGradient(_ ctx: CGContext, top: CGColor, bottom: CGColor, over rect: CGRect) {
    let gradient = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB),
                              colors: [top, bottom] as CFArray, locations: nil)!
    ctx.drawLinearGradient(gradient, start: CGPoint(x: rect.midX, y: rect.minY),
                           end: CGPoint(x: rect.midX, y: rect.maxY),
                           options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
}

func strokeRoundLine(_ ctx: CGContext, through points: [CGPoint], width: CGFloat, color: CGColor) {
    ctx.saveGState()
    ctx.setLineCap(.round)
    ctx.setLineJoin(.round)
    ctx.setLineWidth(width)
    ctx.setStrokeColor(color)
    ctx.addLines(between: points)
    ctx.strokePath()
    ctx.restoreGState()
}

/// Shadows are measured in device pixels, not in the scaled 1024 grid, so scale them by hand;
/// otherwise a 16 px icon gets the 10 px drop of the 1024 px one.
func setShadow(_ ctx: CGContext, drop: CGFloat, blur: CGFloat, color: CGColor) {
    let pixelsPerUnit = ctx.ctm.a
    ctx.setShadow(offset: CGSize(width: 0, height: -drop * pixelsPerUnit), blur: blur * pixelsPerUnit, color: color)
}

// MARK: - The mark

func drawBackground(_ ctx: CGContext) {
    fillVerticalGradient(ctx, top: backgroundTop, bottom: backgroundBottom, over: body)
}

/// The cards and the prompt, centred on the canvas.
func drawStack(_ ctx: CGContext, style: StackStyle) {
    let size = style.cardSize, step = style.cardStep
    let depth = CGFloat(style.backCardOpacities.count) * step
    let front = CGRect(x: canvasSize / 2 - (size.width + depth) / 2,
                       y: canvasSize / 2 - (size.height + depth) / 2 + depth,
                       width: size.width, height: size.height)
    func card(_ rect: CGRect) -> CGPath {
        CGPath(roundedRect: rect, cornerWidth: style.cardCornerRadius, cornerHeight: style.cardCornerRadius,
               transform: nil)
    }

    // Front card first, then each card behind it.
    let cards = (0...style.backCardOpacities.count).map {
        front.offsetBy(dx: CGFloat($0) * step, dy: -CGFloat($0) * step)
    }
    for (index, opacity) in style.backCardOpacities.enumerated().reversed() {
        let back = cards[index + 1]
        ctx.saveGState()
        // Paint only the part that shows: translucent cards would brighten where they overlap.
        for nearer in cards[...index] {
            ctx.addRect(back)
            ctx.addPath(card(nearer))
            ctx.clip(using: .evenOdd)
        }
        ctx.addPath(card(back))
        ctx.setFillColor(color(0xFFFFFF, alpha: opacity))
        ctx.fillPath()
        ctx.restoreGState()
    }

    ctx.saveGState()
    setShadow(ctx, drop: 14, blur: 30, color: cardShadowColor)
    ctx.addPath(card(front))
    ctx.setFillColor(frontCardBottom)
    ctx.fillPath()
    ctx.restoreGState()
    ctx.saveGState()
    ctx.addPath(card(front))
    ctx.clip()
    fillVerticalGradient(ctx, top: frontCardTop, bottom: frontCardBottom, over: front)
    ctx.restoreGState()

    var promptCenterY = front.midY
    if style.hasTitleBarDots {
        ctx.setFillColor(titleBarDotColor)
        for index in 0..<3 {
            let center = CGPoint(x: front.minX + titleBarFirstDot.x + CGFloat(index) * titleBarDotSpacing,
                                 y: front.minY + titleBarFirstDot.y)
            ctx.fillEllipse(in: CGRect(x: center.x - titleBarDotRadius, y: center.y - titleBarDotRadius,
                                       width: 2 * titleBarDotRadius, height: 2 * titleBarDotRadius))
        }
        promptCenterY += titleBarPromptShift
    }

    let chevron = CGPoint(x: front.minX + style.promptInset, y: promptCenterY)
    strokeRoundLine(ctx, through: [
        CGPoint(x: chevron.x - style.chevronHalfWidth, y: chevron.y - style.chevronHalfHeight),
        CGPoint(x: chevron.x + style.chevronHalfWidth, y: chevron.y),
        CGPoint(x: chevron.x - style.chevronHalfWidth, y: chevron.y + style.chevronHalfHeight),
    ], width: style.promptLineWidth, color: promptColor)
    let cursorY = chevron.y + style.chevronHalfHeight
    strokeRoundLine(ctx, through: [
        CGPoint(x: chevron.x + style.cursorOffset, y: cursorY),
        CGPoint(x: chevron.x + style.cursorOffset + style.cursorLength, y: cursorY),
    ], width: style.promptLineWidth, color: promptColor)
}

// MARK: - Icon variants

/// The macOS app icon: a squircle body with a drop shadow, on a transparent canvas.
func drawMacIcon(_ ctx: CGContext, style: StackStyle) {
    let bodyPath = continuousRoundedRect(body, radius: bodyCornerRadius)
    ctx.saveGState()
    setShadow(ctx, drop: 10, blur: 22, color: bodyShadowColor)
    ctx.addPath(bodyPath)
    ctx.setFillColor(backgroundBottom)
    ctx.fillPath()
    ctx.restoreGState()

    ctx.saveGState()
    ctx.addPath(bodyPath)
    ctx.clip()
    drawBackground(ctx)
    drawStack(ctx, style: style)
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

/// The body filling the whole image. `artworkScale` < 1 keeps the stack inside a maskable
/// icon's safe zone.
func drawWebIcon(_ ctx: CGContext, shape: WebIconShape, artworkScale: CGFloat) {
    let bleed = canvasSize / body.width
    ctx.translateBy(x: canvasSize / 2, y: canvasSize / 2)
    ctx.scaleBy(x: bleed, y: bleed)
    ctx.translateBy(x: -canvasSize / 2, y: -canvasSize / 2)
    if shape == .rounded {
        ctx.addPath(continuousRoundedRect(body, radius: bodyCornerRadius))
        ctx.clip()
    }
    drawBackground(ctx)
    ctx.translateBy(x: canvasSize / 2, y: canvasSize / 2)
    ctx.scaleBy(x: artworkScale, y: artworkScale)
    ctx.translateBy(x: -canvasSize / 2, y: -canvasSize / 2)
    drawStack(ctx, style: .regular)
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
    let icons: [(name: String, pixels: Int, shape: WebIconShape, artworkScale: CGFloat)] = [
        ("apple-touch-icon.png", 180, .square, 1),
        ("icon-192.png", 192, .rounded, 1),
        ("icon-512.png", 512, .rounded, 1),
        ("icon-maskable-512.png", 512, .square, maskableSafeZone),
    ]
    for icon in icons {
        let image = render(pixels: icon.pixels) {
            drawWebIcon($0, shape: icon.shape, artworkScale: icon.artworkScale)
        }
        try writePNG(image, to: directory.appendingPathComponent(icon.name))
        print("wrote \(directory.lastPathComponent)/\(icon.name)")
    }
}

let repo = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
try writeMacIcon(to: repo.appendingPathComponent("app/Fleet.icns"))
try writeWebIcons(to: repo.appendingPathComponent("fleetd/web/icons"))
