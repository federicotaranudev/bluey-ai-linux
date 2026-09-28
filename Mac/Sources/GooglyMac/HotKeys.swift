import AppKit
import Carbon.HIToolbox

/// System-wide shortcuts through Carbon, which needs no Accessibility permission.
final class HotKeys {
    static let shared = HotKeys()

    private var actions: [UInt32: () -> Void] = [:]
    private var refs: [EventHotKeyRef] = []
    private var nextID: UInt32 = 1
    private var installed = false

    /// Registers ⌃⌥ + `key`. `keyCode` is a kVK_ constant.
    func register(keyCode: Int, modifiers: Int = controlKey | optionKey, action: @escaping () -> Void) {
        installHandlerIfNeeded()
        let id = nextID
        nextID += 1
        actions[id] = action
        var ref: EventHotKeyRef?
        let hotKeyID = EventHotKeyID(signature: OSType(0x474F4F47), id: id)  // 'GOOG'
        let status = RegisterEventHotKey(UInt32(keyCode), UInt32(modifiers), hotKeyID, GetApplicationEventTarget(), 0, &ref)
        if status == noErr, let ref { refs.append(ref) } else { NSLog("Googly: hotkey \(keyCode) not registered (\(status))") }
    }

    fileprivate func fire(_ id: UInt32) {
        actions[id]?()
    }

    private func installHandlerIfNeeded() {
        guard !installed else { return }
        installed = true
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, event, _ in
            var hotKeyID = EventHotKeyID()
            GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                              nil, MemoryLayout<EventHotKeyID>.size, nil, &hotKeyID)
            DispatchQueue.main.async { HotKeys.shared.fire(hotKeyID.id) }
            return noErr
        }, 1, &spec, nil, nil)
    }
}
