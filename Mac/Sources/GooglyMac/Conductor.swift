import AppKit
import GooglyShared

/// Runs one question end to end: listen, look at the screen, ask Claude, then speak while pointing on cue.
final class Conductor {
    enum State: Equatable {
        case idle, listening, thinking, speaking
    }

    private(set) var state = State.idle { didSet { onChange?() } }
    /// Set when screen reading failed, shown in the menu.
    private(set) var screenProblem: String?
    var onChange: (() -> Void)?

    private let overlay: CursorOverlay
    private let listener = Listener()
    private let brain = Brain()
    let voice = Voice()
    private var job: Task<Void, Never>?
    private var screenTask: Task<ScreenSnapshot?, Never>?
    private var resetWork: DispatchWorkItem?

    init(overlay: CursorOverlay) {
        self.overlay = overlay
        overlay.view.engine.talkLevel = { [weak self] in self?.voice.level ?? 0 }
    }

    var statusText: String {
        switch state {
        case .idle: return "Hold ⌥Space to ask"
        case .listening: return "Listening…"
        case .thinking: return "Thinking…"
        case .speaking: return "Talking"
        }
    }

    // MARK: Push-to-talk

    func pressTalk() {
        cancel()
        do {
            try listener.start()
        } catch {
            showProblem("I couldn't start the microphone: \(error.localizedDescription)")
            return
        }
        guard listener.isListening else {
            showProblem("Speech recognition isn't ready. Check Microphone and Speech Recognition in System Settings.")
            return
        }
        state = .listening
        engine.brainMood = .listening
        engine.gazeOverride = CGPoint(x: 0, y: 0.15)  // looks at you
        overlay.view.caption = nil
        lookAtScreen()
    }

    func releaseTalk() {
        guard state == .listening else { return }
        listener.stop { [weak self] heard in
            guard let self else { return }
            if heard.isEmpty { self.finish(after: 0) } else { self.answer(heard) }
        }
    }

    /// For testing without a microphone.
    func ask(typed question: String) {
        cancel()
        lookAtScreen()
        answer(question)
    }

    // MARK: Steps

    private var engine: CursorEngine { overlay.view.engine }

    private func lookAtScreen() {
        screenTask = Task { @MainActor [weak self] in
            do {
                let shot = try await ScreenReader.snapshot()
                self?.screenProblem = nil
                return shot
            } catch {
                NSLog("Googly: screen capture failed: \(error)")
                self?.screenProblem = "Can't see the screen. Allow Googly Eyes in System Settings, Privacy & Security, Screen & System Audio Recording, then reopen it."
                return nil
            }
        }
    }

    private func answer(_ question: String) {
        state = .thinking
        engine.brainMood = .thinking
        engine.gazeOverride = nil
        let screenTask = self.screenTask
        job = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                let screen = await screenTask?.value
                let reply = try await self.brain.ask(question, screen: screen)
                try Task.checkCancellation()
                let speech: PreparedSpeech
                do {
                    speech = try await self.voice.prepare(reply.say)
                } catch {
                    NSLog("Googly: voice failed, using the Mac voice: \(error)")
                    speech = PreparedSpeech(text: reply.say, audio: nil, charStarts: nil)
                }
                try Task.checkCancellation()
                self.perform(reply, speech: speech, screen: screen)
            } catch is CancellationError {
            } catch {
                self.showProblem(error.localizedDescription)
            }
        }
    }

    private func perform(_ reply: Reply, speech: PreparedSpeech, screen: ScreenSnapshot?) {
        state = .speaking
        engine.brainMood = reply.mood == "happy" ? .happy : .talking
        if Settings.shared.captions { overlay.view.caption = reply.say }

        let wordCount = reply.say.split(whereSeparator: \.isWhitespace).count
        var cues: [Int: [CGPoint]] = [:]
        for point in reply.points {
            guard let target = screen?.target(point.target) else { continue }
            // Land the tip just under the target so the body doesn't cover it.
            let spot = CGPoint(x: target.rect.midX, y: target.rect.maxY + 3)
            cues[min(max(point.atWord, 0), max(wordCount - 1, 0)), default: []].append(spot)
        }

        voice.play(speech, onWord: { [weak self] index in
            guard let self, let spot = cues[index]?.last else { return }
            self.overlay.mode = .pinned(spot)
        }, onDone: { [weak self] in
            self?.finish(after: 1.4)
        })
    }

    private func showProblem(_ message: String) {
        NSLog("Googly: \(message)")
        state = .speaking
        engine.brainMood = .thinking
        overlay.view.caption = message
        finish(after: 5)
    }

    /// Goes back to resting above the phone after a short pause.
    private func finish(after delay: Double) {
        resetWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.state = .idle
            self.engine.brainMood = nil
            self.engine.gazeOverride = nil
            self.overlay.view.caption = nil
            if case .pinned = self.overlay.mode { self.overlay.goHome() }
        }
        resetWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }

    private func cancel() {
        job?.cancel()
        job = nil
        resetWork?.cancel()
        voice.stop()
        if listener.isListening { listener.stop { _ in } }
        engine.brainMood = nil
        engine.gazeOverride = nil
        overlay.view.caption = nil
        state = .idle
    }
}
