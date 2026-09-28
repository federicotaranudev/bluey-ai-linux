import AVFoundation
import Speech

/// Push-to-talk speech recognition with Apple's recognizer (on-device when available).
final class Listener {
    private let engine = AVAudioEngine()
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var transcript = ""
    private var finish: ((String) -> Void)?
    private(set) var isListening = false

    /// Asks for microphone and speech permission. Calls back on the main queue.
    static func requestPermissions(_ done: @escaping (String?) -> Void) {
        SFSpeechRecognizer.requestAuthorization { status in
            guard status == .authorized else {
                DispatchQueue.main.async { done("Speech recognition is off. Turn it on for Googly Eyes in System Settings, Privacy & Security, Speech Recognition.") }
                return
            }
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                DispatchQueue.main.async { done(granted ? nil : "The microphone is off. Turn it on for Googly Eyes in System Settings, Privacy & Security, Microphone.") }
            }
        }
    }

    func start() throws {
        guard !isListening, let recognizer, recognizer.isAvailable else { return }
        transcript = ""
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.addsPunctuation = true
        if recognizer.supportsOnDeviceRecognition { request.requiresOnDeviceRecognition = true }
        self.request = request

        let input = engine.inputNode
        input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { buffer, _ in
            request.append(buffer)
        }
        engine.prepare()
        try engine.start()
        isListening = true

        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            DispatchQueue.main.async {
                guard let self else { return }
                if let result { self.transcript = result.bestTranscription.formattedString }
                if result?.isFinal == true || error != nil { self.complete() }
            }
        }
    }

    /// Stops listening and hands back what was heard (waits briefly for the final result).
    func stop(_ done: @escaping (String) -> Void) {
        guard isListening else { done(""); return }
        isListening = false
        finish = done
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        request?.endAudio()
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in self?.complete() }
    }

    private func complete() {
        guard let finish, !isListening else { return }
        self.finish = nil
        task?.cancel()
        task = nil
        request = nil
        finish(transcript.trimmingCharacters(in: .whitespacesAndNewlines))
    }
}
