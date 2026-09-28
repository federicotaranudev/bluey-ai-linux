import SwiftUI

extension Color {
    init(hex: UInt32, opacity: Double = 1) {
        let c = Palette.rgb(hex)
        self.init(.sRGB, red: c.r, green: c.g, blue: c.b, opacity: opacity)
    }
}

extension Font {
    /// Rounded display face from the design. Falls back to SF Rounded until Fredoka is bundled.
    static func fredoka(_ size: CGFloat) -> Font {
        UIFontCheck.has("Fredoka") ? .custom("Fredoka", size: size).weight(.bold) : .system(size: size, weight: .bold, design: .rounded)
    }

    static func plexSans(_ size: CGFloat) -> Font {
        UIFontCheck.has("IBM Plex Sans") ? .custom("IBM Plex Sans", size: size) : .system(size: size)
    }

    static func plexMono(_ size: CGFloat) -> Font {
        UIFontCheck.has("IBM Plex Mono") ? .custom("IBM Plex Mono", size: size) : .system(size: size, design: .monospaced)
    }
}

enum UIFontCheck {
    private static let families: Set<String> = {
        #if canImport(UIKit)
        return Set(UIFont.familyNames)
        #else
        return []
        #endif
    }()

    static func has(_ family: String) -> Bool { families.contains(family) }
}

/// The blob outline: CSS `border-radius: 52% 48% 46% 54% / 58% 56% 44% 42%`.
struct BlobShape: Shape {
    func path(in rect: CGRect) -> Path {
        BlobShape.path(in: rect)
    }

    static func path(in r: CGRect) -> Path {
        let w = r.width, h = r.height
        // Horizontal and vertical radii for top-left, top-right, bottom-right, bottom-left.
        let tl = CGSize(width: 0.52 * w, height: 0.58 * h)
        let tr = CGSize(width: 0.48 * w, height: 0.56 * h)
        let br = CGSize(width: 0.46 * w, height: 0.44 * h)
        let bl = CGSize(width: 0.54 * w, height: 0.42 * h)
        let k: CGFloat = 0.5523  // cubic approximation of a quarter ellipse
        var p = Path()
        p.move(to: CGPoint(x: r.minX + tl.width, y: r.minY))
        p.addLine(to: CGPoint(x: r.maxX - tr.width, y: r.minY))
        p.addCurve(to: CGPoint(x: r.maxX, y: r.minY + tr.height),
                   control1: CGPoint(x: r.maxX - tr.width * (1 - k), y: r.minY),
                   control2: CGPoint(x: r.maxX, y: r.minY + tr.height * (1 - k)))
        p.addLine(to: CGPoint(x: r.maxX, y: r.maxY - br.height))
        p.addCurve(to: CGPoint(x: r.maxX - br.width, y: r.maxY),
                   control1: CGPoint(x: r.maxX, y: r.maxY - br.height * (1 - k)),
                   control2: CGPoint(x: r.maxX - br.width * (1 - k), y: r.maxY))
        p.addLine(to: CGPoint(x: r.minX + bl.width, y: r.maxY))
        p.addCurve(to: CGPoint(x: r.minX, y: r.maxY - bl.height),
                   control1: CGPoint(x: r.minX + bl.width * (1 - k), y: r.maxY),
                   control2: CGPoint(x: r.minX, y: r.maxY - bl.height * (1 - k)))
        p.addLine(to: CGPoint(x: r.minX, y: r.minY + tl.height))
        p.addCurve(to: CGPoint(x: r.minX + tl.width, y: r.minY),
                   control1: CGPoint(x: r.minX, y: r.minY + tl.height * (1 - k)),
                   control2: CGPoint(x: r.minX + tl.width * (1 - k), y: r.minY))
        p.closeSubpath()
        return p
    }

    /// The blueberry gradient at 150°, like the design.
    static func fill(in r: CGRect) -> GraphicsContext.Shading {
        .linearGradient(gradient,
                        startPoint: CGPoint(x: r.minX + 0.25 * r.width, y: r.minY + 0.067 * r.height),
                        endPoint: CGPoint(x: r.minX + 0.75 * r.width, y: r.minY + 0.933 * r.height))
    }

    static var gradient: Gradient {
        Gradient(stops: zip(Palette.gradient, Palette.gradientStops).map {
            Gradient.Stop(color: Color(hex: $0.0), location: $0.1)
        })
    }

    static var linear: LinearGradient {
        LinearGradient(gradient: gradient, startPoint: UnitPoint(x: 0.25, y: 0.067), endPoint: UnitPoint(x: 0.75, y: 0.933))
    }
}

/// The little blueberry crown on top of his head (SVG path from the design, 60×40 box).
enum Crown {
    static func path(in r: CGRect) -> Path {
        let pts: [(CGFloat, CGFloat)] = [(30, 4), (36, 16), (52, 12), (40, 24), (46, 36), (30, 28), (14, 36), (20, 24), (8, 12), (24, 16)]
        var p = Path()
        for (i, pt) in pts.enumerated() {
            let point = CGPoint(x: r.minX + pt.0 / 60 * r.width, y: r.minY + pt.1 / 40 * r.height)
            if i == 0 { p.move(to: point) } else { p.addLine(to: point) }
        }
        p.closeSubpath()
        return p
    }
}
