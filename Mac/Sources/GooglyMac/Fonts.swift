import AppKit
import CoreText

/// Fredoka and IBM Plex ship inside the app bundle; this makes them usable.
enum Fonts {
    static func registerBundled() {
        let urls = Bundle.main.urls(forResourcesWithExtension: "ttf", subdirectory: nil) ?? []
        for url in urls { CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil) }
    }

    /// Rounded display face for captions, falling back to SF Rounded.
    static func display(_ size: CGFloat) -> NSFont {
        if let fredoka = NSFontManager.shared.font(withFamily: "Fredoka", traits: [], weight: 8, size: size) { return fredoka }
        let base = NSFont.systemFont(ofSize: size, weight: .semibold)
        return base.fontDescriptor.withDesign(.rounded).flatMap { NSFont(descriptor: $0, size: size) } ?? base
    }
}
