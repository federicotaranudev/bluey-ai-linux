import AppKit
import ApplicationServices

/// Reads the clickable controls (buttons, fields, links, checkboxes…) of the frontmost app through
/// Accessibility, so he can click things that have no visible text, like icon buttons.
enum ControlsReader {
    struct Control {
        let id: String
        let kind: String
        let label: String
        /// In overlay coordinates: points, top-left origin, y down.
        let rect: CGRect
    }

    private static let kinds: [String: String] = [
        "AXButton": "button", "AXMenuButton": "menu button", "AXPopUpButton": "pop-up", "AXCheckBox": "checkbox",
        "AXRadioButton": "option", "AXTextField": "text field", "AXTextArea": "text area", "AXComboBox": "combo box",
        "AXSearchField": "search field", "AXLink": "link", "AXSlider": "slider", "AXDisclosureTriangle": "disclosure",
        "AXIncrementor": "stepper", "AXTab": "tab", "AXMenuBarItem": "menu",
    ]
    private static let fieldKinds: Set<String> = ["text field", "text area", "combo box", "search field"]

    /// The frontmost app's name and its visible controls, in reading order. Stays within a small time budget.
    static func read(screen: CGSize, limit: Int = 140, budget: TimeInterval = 0.45) -> (app: String?, controls: [Control]) {
        guard let app = NSWorkspace.shared.frontmostApplication else { return (nil, []) }
        let name = app.localizedName
        guard ComputerControl.isTrusted else { return (name, []) }

        let appElement = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(appElement, 0.25)
        // Electron apps (Slack, VS Code, Discord…) only expose their controls when asked.
        AXUIElementSetAttributeValue(appElement, "AXManualAccessibility" as CFString, kCFBooleanTrue)

        var roots: [AXUIElement] = []
        if let window: AXUIElement = attribute(appElement, kAXFocusedWindowAttribute) { roots.append(window) }
        if roots.isEmpty, let windows: [AXUIElement] = attribute(appElement, kAXWindowsAttribute) { roots += windows.prefix(2) }
        if let menuBar: AXUIElement = attribute(appElement, kAXMenuBarAttribute) { roots.append(menuBar) }

        let deadline = Date().addingTimeInterval(budget)
        let visible = CGRect(origin: .zero, size: screen)
        var queue = roots
        var visited = 0
        var found: [(kind: String, label: String, rect: CGRect)] = []
        var seen = Set<String>()

        while !queue.isEmpty, visited < 5000, Date() < deadline {
            let element = queue.removeFirst()
            visited += 1
            let role: String = attribute(element, kAXRoleAttribute) ?? ""
            let subrole: String = attribute(element, kAXSubroleAttribute) ?? ""
            let kind = kinds[subrole == "AXSearchField" ? "AXSearchField" : role]
            if let kind, let rect = frame(of: element), rect.width > 3, rect.height > 3, visible.intersects(rect) {
                let label = describe(element, kind: kind)
                if !label.isEmpty || fieldKinds.contains(kind) {
                    let key = "\(Int(rect.minX)),\(Int(rect.minY)),\(Int(rect.width)),\(Int(rect.height))"
                    if !seen.contains(key) {
                        seen.insert(key)
                        found.append((kind, label, rect))
                    }
                }
            }
            // Menu bar items hold whole menus; don't walk into closed menus.
            if role == "AXMenuBarItem" { continue }
            if let children: [AXUIElement] = attribute(element, kAXChildrenAttribute) {
                queue.append(contentsOf: children.prefix(400))
            }
        }

        found.sort { abs($0.rect.minY - $1.rect.minY) > 6 ? $0.rect.minY < $1.rect.minY : $0.rect.minX < $1.rect.minX }
        let controls = found.prefix(limit).enumerated().map { i, item in
            Control(id: "C\(i + 1)", kind: item.kind, label: item.label, rect: item.rect)
        }
        return (name, controls)
    }

    /// The focused element of the front app: where typing will go, and whether it's a password field.
    static func focusedField() -> (frame: CGRect?, secure: Bool) {
        guard ComputerControl.isTrusted, let app = NSWorkspace.shared.frontmostApplication else { return (nil, false) }
        let appElement = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(appElement, 0.2)
        guard let focused: AXUIElement = attribute(appElement, kAXFocusedUIElementAttribute) else { return (nil, false) }
        let subrole: String = attribute(focused, kAXSubroleAttribute) ?? ""
        return (frame(of: focused), subrole == "AXSecureTextField")
    }

    private static func describe(_ element: AXUIElement, kind: String) -> String {
        var parts: [String] = []
        for key in [kAXTitleAttribute, kAXDescriptionAttribute, "AXPlaceholderValue", kAXHelpAttribute] {
            if let text: String = attribute(element, key) {
                let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
                if !clean.isEmpty, !parts.contains(clean) { parts.append(clean) }
            }
            if !parts.isEmpty { break }
        }
        if fieldKinds.contains(kind), let value: String = attribute(element, kAXValueAttribute) {
            let clean = value.trimmingCharacters(in: .whitespacesAndNewlines)
            if !clean.isEmpty { parts.append("contains: \(clean.prefix(40))") }
        } else if parts.isEmpty, let value: String = attribute(element, kAXValueAttribute) {
            let clean = value.trimmingCharacters(in: .whitespacesAndNewlines)
            if !clean.isEmpty { parts.append(String(clean.prefix(40))) }
        }
        return parts.joined(separator: ", ").replacingOccurrences(of: "\n", with: " ")
    }

    private static func frame(of element: AXUIElement) -> CGRect? {
        guard let positionValue: AXValue = attribute(element, kAXPositionAttribute),
              let sizeValue: AXValue = attribute(element, kAXSizeAttribute) else { return nil }
        var position = CGPoint.zero
        var size = CGSize.zero
        guard AXValueGetValue(positionValue, .cgPoint, &position), AXValueGetValue(sizeValue, .cgSize, &size) else { return nil }
        return CGRect(origin: position, size: size)
    }

    private static func attribute<T>(_ element: AXUIElement, _ name: String) -> T? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success, let value else { return nil }
        if T.self == AXUIElement.self {
            guard CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
            return (value as! AXUIElement) as? T
        }
        if T.self == AXValue.self {
            guard CFGetTypeID(value) == AXValueGetTypeID() else { return nil }
            return (value as! AXValue) as? T
        }
        if T.self == [AXUIElement].self {
            guard let array = value as? [AnyObject] else { return nil }
            return array.compactMap { item -> AXUIElement? in
                CFGetTypeID(item) == AXUIElementGetTypeID() ? (item as! AXUIElement) : nil
            } as? T
        }
        return value as? T
    }
}
