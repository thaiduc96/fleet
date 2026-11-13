// Render the dashboard's home-screen icons (the favicon design: three lines on Claude orange).
//   swift scripts/make-icons.swift fleetd/web/icons
// "rounded" icons have their own corners; "full" ones fill the square, for iOS (which
// rounds them itself) and Android's maskable icons (content kept inside the safe zone).
import AppKit

let out = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "fleetd/web/icons")
let orange = NSColor(srgbRed: 0xD9 / 255.0, green: 0x77 / 255.0, blue: 0x57 / 255.0, alpha: 1)

/// The 32×32 favicon geometry, scaled: lines at y 11/16/21 from x 8 to 24/19/15, 3 wide.
func draw(size: CGFloat, rounded: Bool, safeZone: CGFloat) {
    let rect = NSRect(x: 0, y: 0, width: size, height: size)
    orange.setFill()
    if rounded {
        NSBezierPath(roundedRect: rect, xRadius: size * 7 / 32, yRadius: size * 7 / 32).fill()
    } else {
        rect.fill()
    }
    let unit = size * safeZone / 32
    let inset = (size - 32 * unit) / 2
    NSColor.white.setStroke()
    for (y, end) in [(11.0, 24.0), (16.0, 19.0), (21.0, 15.0)] {
        let line = NSBezierPath()
        line.lineWidth = 3 * unit
        line.lineCapStyle = .round
        // AppKit's origin is bottom-left; the SVG's is top-left.
        line.move(to: NSPoint(x: inset + 8 * unit, y: size - (inset + y * unit)))
        line.line(to: NSPoint(x: inset + end * unit, y: size - (inset + y * unit)))
        line.stroke()
    }
}

func write(_ name: String, size: Int, rounded: Bool, safeZone: CGFloat = 1) throws {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    draw(size: CGFloat(size), rounded: rounded, safeZone: safeZone)
    NSGraphicsContext.restoreGraphicsState()
    try rep.representation(using: .png, properties: [:])!.write(to: out.appendingPathComponent(name))
    print("wrote \(name)")
}

try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
try write("apple-touch-icon.png", size: 180, rounded: false)
try write("icon-192.png", size: 192, rounded: true)
try write("icon-512.png", size: 512, rounded: true)
try write("icon-maskable-512.png", size: 512, rounded: false, safeZone: 0.8)  // Android crops to a circle
