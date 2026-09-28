import Foundation

/// Blueberry colors from the design canvas, as 0xRRGGBB.
public enum Palette {
    public static let berry1: UInt32 = 0xA9BCFF  // light periwinkle
    public static let berry2: UInt32 = 0x6C86F5
    public static let berry3: UInt32 = 0x4254D6
    public static let berry4: UInt32 = 0x2B2F8F  // deep indigo
    public static let nose: UInt32 = 0x1C1F66
    public static let ink: UInt32 = 0x17151F
    public static let inkSoft: UInt32 = 0xB9B2CC
    public static let panel: UInt32 = 0x1E1B29

    public static let gradient: [UInt32] = [berry1, berry2, berry3, berry4]
    public static let gradientStops: [Double] = [0, 0.34, 0.68, 1]

    public static func rgb(_ hex: UInt32) -> (r: Double, g: Double, b: Double) {
        (Double((hex >> 16) & 0xFF) / 255, Double((hex >> 8) & 0xFF) / 255, Double(hex & 0xFF) / 255)
    }
}
