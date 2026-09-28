import AppKit
import Carbon.HIToolbox
import GooglyShared

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let settings = Settings.shared
    private let overlay = CursorOverlay()
    private let server = PhoneServer()
    private var statusItem: NSStatusItem!
    private lazy var conductor = Conductor(overlay: overlay)
    private var voices: [(id: String, name: String)] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        Fonts.registerBundled()
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.image = MenuIcon.make()
        statusItem.button?.toolTip = "Googly Eyes"
        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu

        overlay.onFace = { [weak self] face in self?.server.send(face) }
        overlay.start()
        server.onPhonesChanged = { [weak self] _ in self?.refreshIcon() }
        server.start()

        // ⌃⌥P point here, ⌃⌥F follow mouse, ⌃⌥D dock, ⌃⌥H hide, ⌃⌥T talk test.
        HotKeys.shared.register(keyCode: kVK_ANSI_P) { [weak self] in self?.pointHere() }
        HotKeys.shared.register(keyCode: kVK_ANSI_F) { [weak self] in self?.toggleFollow() }
        HotKeys.shared.register(keyCode: kVK_ANSI_D) { [weak self] in self?.overlay.goHome() }
        HotKeys.shared.register(keyCode: kVK_ANSI_H) { [weak self] in self?.toggleShow() }
        HotKeys.shared.register(keyCode: kVK_ANSI_T) { [weak self] in self?.overlay.talkTest() }

        // Hold ⌥Space to ask, let go and he answers. ⌃⌥A asks by typing.
        HotKeys.shared.register(keyCode: kVK_Space, modifiers: optionKey,
                                onRelease: { [weak self] in self?.conductor.releaseTalk() },
                                onPress: { [weak self] in self?.conductor.pressTalk() })
        HotKeys.shared.register(keyCode: kVK_ANSI_A) { [weak self] in self?.askByTyping() }
        HotKeys.shared.register(keyCode: kVK_ANSI_V) { [weak self] in self?.conductor.testVoice() }

        conductor.onChange = { [weak self] in self?.refreshIcon() }
        Listener.requestPermissions { problem in if let problem { NSLog("Googly: \(problem)") } }
        loadVoices()
        if Keychain.get(.anthropic) == nil {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in self?.editKeys() }
        }
    }

    private func loadVoices() {
        Task { @MainActor in self.voices = await Voice.fetchVoices() }
    }

    private func refreshIcon() {
        statusItem.button?.appearsDisabled = server.phoneNames.isEmpty
    }

    // MARK: Actions

    @objc private func pointHere() {
        let m = NSEvent.mouseLocation
        guard let screen = NSScreen.screens.first else { return }
        overlay.mode = .pinned(CGPoint(x: m.x - screen.frame.minX, y: screen.frame.maxY - m.y))
    }

    @objc private func toggleFollow() {
        settings.followMouse.toggle()
        overlay.goHome()
    }

    @objc private func dock() { overlay.goHome() }
    @objc private func toggleShow() { settings.showCursor.toggle(); overlay.view.needsDisplay = true }
    @objc private func toggleGlow() { settings.glow.toggle() }
    @objc private func talkTest() { overlay.talkTest() }

    @objc private func setSize(_ item: NSMenuItem) { settings.cursorSize = Double(item.tag) }
    @objc private func setPhonePosition(_ item: NSMenuItem) { settings.phonePosition = Double(item.tag) / 100 }
    @objc private func volumeChanged(_ slider: NSSlider) {
        settings.volume = slider.doubleValue
        conductor.voice.applyVolume()
    }

    @objc private func testVoice() { conductor.testVoice() }

    @objc private func toggleCaptions() { settings.captions.toggle() }
    @objc private func setVoice(_ item: NSMenuItem) {
        if let id = item.representedObject as? String { UserDefaults.standard.set(id, forKey: "voiceID") }
    }

    @objc private func askByTyping() {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Ask Googly"
        alert.informativeText = "Type what you'd say out loud. He'll look at your screen and answer."
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 24))
        field.placeholderString = "What's the biggest number in this table?"
        alert.accessoryView = field
        alert.addButton(withTitle: "Ask")
        alert.addButton(withTitle: "Cancel")
        alert.window.initialFirstResponder = field
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let question = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty else { return }
        // Let the dialog disappear before he looks at the screen.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { self.conductor.ask(typed: question) }
    }

    @objc private func editKeys() {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "API Keys"
        alert.informativeText = "Saved in your Mac's Keychain, never in the project files. Leave a box empty to keep the saved key."
        let claude = NSSecureTextField(frame: NSRect(x: 0, y: 30, width: 360, height: 24))
        claude.placeholderString = Keychain.get(.anthropic) == nil ? "Claude API key (sk-ant-…)" : "Claude key saved"
        let eleven = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 24))
        eleven.placeholderString = Keychain.get(.elevenlabs) == nil ? "ElevenLabs API key (optional)" : "ElevenLabs key saved"
        let workspace = NSTextField(frame: NSRect(x: 0, y: 60, width: 360, height: 24))
        workspace.placeholderString = "Claude workspace ID (only if your key needs one)"
        workspace.stringValue = UserDefaults.standard.string(forKey: "anthropicWorkspace") ?? ""
        claude.frame.origin.y = 30
        let stack = NSView(frame: NSRect(x: 0, y: 0, width: 360, height: 84))
        stack.addSubview(workspace)
        stack.addSubview(claude)
        stack.addSubview(eleven)
        alert.accessoryView = stack
        alert.addButton(withTitle: "Save")
        alert.addButton(withTitle: "Cancel")
        alert.window.initialFirstResponder = claude
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let c = claude.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        let e = eleven.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if !c.isEmpty { Keychain.set(.anthropic, c) }
        UserDefaults.standard.set(workspace.stringValue.trimmingCharacters(in: .whitespacesAndNewlines), forKey: "anthropicWorkspace")
        if !e.isEmpty { Keychain.set(.elevenlabs, e); loadVoices() }
    }

    @objc private func setMood(_ item: NSMenuItem) {
        if let mood = item.representedObject as? String, let m = Mood(rawValue: mood) { settings.mood = m }
    }

    // MARK: Menu

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()

        let phones = server.phoneNames
        let status = NSMenuItem(title: phones.isEmpty ? "Waiting for the phone app…" : "Phone connected: \(phones.joined(separator: ", "))",
                                action: nil, keyEquivalent: "")
        status.isEnabled = false
        menu.addItem(status)
        let brain = NSMenuItem(title: conductor.statusText, action: nil, keyEquivalent: "")
        brain.isEnabled = false
        menu.addItem(brain)
        if let problem = conductor.screenProblem {
            let p = NSMenuItem(title: problem, action: nil, keyEquivalent: "")
            p.isEnabled = false
            menu.addItem(p)
        }
        menu.addItem(item("Ask by Typing…", #selector(askByTyping), key: "a"))
        menu.addItem(.separator())

        let point = item("Point Here", #selector(pointHere), key: "p")
        menu.addItem(point)
        let follow = item("Eyes Follow My Mouse", #selector(toggleFollow), key: "f")
        follow.state = settings.followMouse ? .on : .off
        menu.addItem(follow)
        menu.addItem(item("Stop Pointing", #selector(dock), key: "d"))
        menu.addItem(item("Talk Test", #selector(talkTest), key: "t"))
        menu.addItem(.separator())

        let moodMenu = NSMenu()
        for mood in Mood.allCases where mood != .talking && mood != .pointing {
            let m = item(mood.title, #selector(setMood(_:)))
            m.representedObject = mood.rawValue
            m.state = settings.mood == mood ? .on : .off
            moodMenu.addItem(m)
        }
        menu.addItem(submenu("Mood", moodMenu))

        let sizeMenu = NSMenu()
        for (name, size) in [("Small", 48), ("Medium", 72), ("Large", 96), ("Huge", 120)] {
            let s = item("\(name) (\(size) pt)", #selector(setSize(_:)))
            s.tag = size
            s.state = Int(settings.cursorSize) == size ? .on : .off
            sizeMenu.addItem(s)
        }
        menu.addItem(submenu("Cursor Size", sizeMenu))

        let phoneMenu = NSMenu()
        for (name, pos) in [("Left", 20), ("Center", 50), ("Right", 80)] {
            let p = item(name, #selector(setPhonePosition(_:)))
            p.tag = pos
            p.state = Int((settings.phonePosition * 100).rounded()) == pos ? .on : .off
            phoneMenu.addItem(p)
        }
        menu.addItem(submenu("Phone Sits Under", phoneMenu))

        let voiceMenu = NSMenu()
        let voiceStatus = NSMenuItem(title: "Last reply: \(conductor.voice.status)", action: nil, keyEquivalent: "")
        voiceStatus.isEnabled = false
        voiceMenu.addItem(voiceStatus)
        voiceMenu.addItem(.separator())
        if voices.isEmpty {
            let none = NSMenuItem(title: Keychain.get(.elevenlabs) == nil ? "Add an ElevenLabs key to pick a voice" : "Loading voices…",
                                  action: nil, keyEquivalent: "")
            none.isEnabled = false
            voiceMenu.addItem(none)
        }
        for voice in voices.prefix(40) {
            let v = item(voice.name, #selector(setVoice(_:)))
            v.representedObject = voice.id
            v.state = conductor.voice.voiceID == voice.id ? .on : .off
            voiceMenu.addItem(v)
        }
        menu.addItem(submenu("Voice", voiceMenu))

        let volumeLabel = NSMenuItem(title: "Volume", action: nil, keyEquivalent: "")
        volumeLabel.isEnabled = false
        menu.addItem(volumeLabel)
        let volumeItem = NSMenuItem()
        let holder = NSView(frame: NSRect(x: 0, y: 0, width: 240, height: 28))
        let slider = NSSlider(value: settings.volume, minValue: 0, maxValue: 1, target: self, action: #selector(volumeChanged(_:)))
        slider.frame = NSRect(x: 20, y: 4, width: 200, height: 20)
        slider.isContinuous = true
        holder.addSubview(slider)
        volumeItem.view = holder
        menu.addItem(volumeItem)
        menu.addItem(item("Test Voice", #selector(testVoice), key: "v"))
        menu.addItem(item("API Keys…", #selector(editKeys)))
        let captions = item("Live Captions", #selector(toggleCaptions))
        captions.state = settings.captions ? .on : .off
        menu.addItem(captions)
        menu.addItem(.separator())

        let glow = item("Cursor Glow", #selector(toggleGlow))
        glow.state = settings.glow ? .on : .off
        menu.addItem(glow)
        let show = item("Show Cursor", #selector(toggleShow), key: "h")
        show.state = settings.showCursor ? .on : .off
        menu.addItem(show)

        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit Googly Eyes", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
    }

    /// Menu item that shows its global shortcut (⌃⌥ + key) when given one.
    private func item(_ title: String, _ action: Selector, key: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.keyEquivalentModifierMask = [.control, .option]
        item.target = self
        return item
    }

    private func submenu(_ title: String, _ menu: NSMenu) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.submenu = menu
        return item
    }
}

/// The little blueberry blob with eyes for the menu bar.
enum MenuIcon {
    static func make() -> NSImage {
        let image = NSImage(size: NSSize(width: 22, height: 18), flipped: true) { rect in
            let body = NSBezierPath(ovalIn: NSRect(x: 1, y: 2, width: 20, height: 16))
            NSGradient(colors: Palette.gradient.map { NSColor(hex: $0) })?.draw(in: body, angle: -60)
            NSColor.white.setFill()
            NSBezierPath(ovalIn: NSRect(x: 5, y: 6, width: 5.5, height: 5.5)).fill()
            NSBezierPath(ovalIn: NSRect(x: 11.5, y: 6, width: 5.5, height: 5.5)).fill()
            NSColor(hex: Palette.ink).setFill()
            NSBezierPath(ovalIn: NSRect(x: 6.3, y: 6.8, width: 3, height: 3)).fill()
            NSBezierPath(ovalIn: NSRect(x: 12.8, y: 6.8, width: 3, height: 3)).fill()
            return true
        }
        image.isTemplate = false
        return image
    }
}
