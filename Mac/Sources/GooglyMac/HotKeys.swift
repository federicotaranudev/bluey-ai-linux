import AppKit
import Carbon.HIToolbox

/// System-wide shortcuts through Carbon, which needs no Accessibility permission.
final class HotKeys {
    static let shared = HotKeys()

    private var pressActions: [UInt32: () -> Void] = [:]
    private var releaseActions: [UInt32: () -> Void] = [:]
    private var refs: [EventHotKeyRef] = []
    private var nextID: UInt32 = 1
    private var installed = false

    /// Registers `modifiers` + `key` (a kVK_ constant). Defaults to ⌃⌥.
    func register(keyCode: Int, modifiers: Int = controlKey | optionKey,
                  onRelease: (() -> Void)? = nil, onPress: @escaping () -> Void) {
        installHandlerIfNeeded()
        let id = nextID
        nextID += 1
        pressActions[id] = onPress
        releaseActions[id] = onRelease
        var ref: EventHotKeyRef?
        let hotKeyID = EventHotKeyID(signature: OSType(0x474F4F47), id: id)  // 'GOOG'
        let status = RegisterEventHotKey(UInt32(keyCode), UInt32(modifiers), hotKeyID, GetApplicationEventTarget(), 0, &ref)
        if status == noErr, let ref { refs.append(ref) } else { NSLog("Googly: hotkey \(keyCode) not registered (\(status))") }
    }

    fileprivate func fire(_ id: UInt32, pressed: Bool) {
        if pressed { pressActions[id]?() } else { releaseActions[id]?() }
    }

    private func installHandlerIfNeeded() {
        guard !installed else { return }
        installed = true
        var specs = [
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed)),
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyReleased)),
        ]
        InstallEventHandler(GetApplicationEventTarget(), { _, event, _ in
            var hotKeyID = EventHotKeyID()
            GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                              nil, MemoryLayout<EventHotKeyID>.size, nil, &hotKeyID)
            let pressed = GetEventKind(event) == UInt32(kEventHotKeyPressed)
            DispatchQueue.main.async { HotKeys.shared.fire(hotKeyID.id, pressed: pressed) }
            return noErr
        }, 2, &specs, nil, nil)
    }
}
