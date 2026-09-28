import AVFoundation

enum VoiceError: LocalizedError {
    case badKey
    case failed(String)

    var errorDescription: String? {
        switch self {
        case .badKey: return "ElevenLabs didn't accept the API key. It should start with sk_."
        case .failed(let why): return "ElevenLabs couldn't make the voice: \(why.prefix(160))"
        }
    }
}

/// Speaks a reply with ElevenLabs (Eleven v4 Turbo when available) or the Mac's own voice,
/// and reports each word as it starts so the cursor can land on it.
final class Voice: NSObject, AVSpeechSynthesizerDelegate, AVAudioPlayerDelegate {
    /// Shown in the menu: which voice and model actually worked last.
    private(set) var status = "Not used yet"
    /// Loudness right now, 0…1, for the talking bounce.
    private(set) var level: Double = 0

    private var player: AVAudioPlayer?
    private let synth = AVSpeechSynthesizer()
    private var timers: [DispatchWorkItem] = []
    private var meter: Timer?
    private var onWord: ((Int) -> Void)?
    private var onDone: (() -> Void)?
    private var wordOffsetsUTF16: [Int] = []
    private var lastWord = -1

    static let defaultVoiceID = "JBFqnCBsd6RMkjVDRZzb"
    var voiceID: String { UserDefaults.standard.string(forKey: "voiceID") ?? Self.defaultVoiceID }

    override init() {
        super.init()
        synth.delegate = self
    }

    /// Word start positions in `text`, splitting on whitespace like the prompt tells Claude to count.
    private static func wordStarts(_ text: String) -> (chars: [Int], utf16: [Int]) {
        var chars: [Int] = [], utf16: [Int] = []
        var inWord = false
        var utf16Offset = 0
        for (i, ch) in text.enumerated() {
            if ch.isWhitespace { inWord = false } else if !inWord {
                inWord = true
                chars.append(i)
                utf16.append(utf16Offset)
            }
            utf16Offset += ch.utf16.count
        }
        return (chars, utf16)
    }

    /// Fetches audio first so the caller can start pointing and talking at the same moment.
    func prepare(_ text: String) async throws -> PreparedSpeech {
        guard let key = Keychain.get(.elevenlabs) else {
            status = "Mac voice (no ElevenLabs key)"
            return PreparedSpeech(text: text, audio: nil, charStarts: nil)
        }
        let attempts: [(label: String, path: String, body: [String: Any])] = [
            ("Eleven v4 Turbo", "/v1/text-to-dialogue/with-timestamps",
             ["inputs": [["text": text, "voice_id": voiceID]], "model_id": "eleven_v4_turbo"]),
            ("Eleven v4 Turbo", "/v1/text-to-speech/\(voiceID)/with-timestamps",
             ["text": text, "model_id": "eleven_v4_turbo"]),
            ("Eleven Flash v2.5", "/v1/text-to-speech/\(voiceID)/with-timestamps",
             ["text": text, "model_id": "eleven_flash_v2_5"]),
        ]
        var lastError = ""
        for attempt in attempts {
            var request = URLRequest(url: URL(string: "https://api.elevenlabs.io\(attempt.path)?output_format=mp3_44100_128")!)
            request.httpMethod = "POST"
            request.timeoutInterval = 30
            request.setValue("application/json", forHTTPHeaderField: "content-type")
            request.setValue(key, forHTTPHeaderField: "xi-api-key")
            request.httpBody = try JSONSerialization.data(withJSONObject: attempt.body)
            let (data, response) = try await URLSession.shared.data(for: request)
            let code = (response as? HTTPURLResponse)?.statusCode ?? 0
            if code == 401 || String(data: data, encoding: .utf8)?.contains("invalid_api_key") == true { throw VoiceError.badKey }
            guard code == 200,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let b64 = json["audio_base64"] as? String, let audio = Data(base64Encoded: b64) else {
                lastError = "\(code) " + (String(data: data, encoding: .utf8) ?? "")
                continue
            }
            let alignment = json["alignment"] as? [String: Any]
            let starts = alignment?["character_start_times_seconds"] as? [Double]
            status = "\(attempt.label)" + (starts == nil ? ", no word timing" : ", word timing on")
            return PreparedSpeech(text: text, audio: audio, charStarts: starts)
        }
        throw VoiceError.failed(lastError)
    }

    func play(_ speech: PreparedSpeech, onWord: @escaping (Int) -> Void, onDone: @escaping () -> Void) {
        stop()
        self.onWord = onWord
        self.onDone = onDone
        let words = Self.wordStarts(speech.text)
        wordOffsetsUTF16 = words.utf16
        lastWord = -1

        if let audio = speech.audio, let player = try? AVAudioPlayer(data: audio) {
            self.player = player
            player.delegate = self
            player.isMeteringEnabled = true
            player.volume = Float(Settings.shared.volume)
            player.prepareToPlay()
            let count = speech.text.count
            for (index, charOffset) in words.chars.enumerated() {
                let time: Double
                if let starts = speech.charStarts, starts.count == count, charOffset < starts.count {
                    time = starts[charOffset]
                } else {
                    time = player.duration * Double(charOffset) / Double(max(count, 1))  // estimate
                }
                let work = DispatchWorkItem { [weak self] in self?.onWord?(index) }
                timers.append(work)
                DispatchQueue.main.asyncAfter(deadline: .now() + time, execute: work)
            }
            player.play()
            meter = Timer.scheduledTimer(withTimeInterval: 1.0 / 30, repeats: true) { [weak self] _ in
                guard let self, let player = self.player else { return }
                player.updateMeters()
                let db = Double(player.averagePower(forChannel: 0))
                self.level = min(1, max(0, pow(10, db / 20) * 3.2))
            }
        } else {
            let utterance = AVSpeechUtterance(string: speech.text)
            utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
            utterance.pitchMultiplier = 1.25
            utterance.volume = Float(Settings.shared.volume)
            synth.speak(utterance)
            meter = Timer.scheduledTimer(withTimeInterval: 1.0 / 30, repeats: true) { [weak self] _ in
                guard let self else { return }
                let t = CACurrentMediaTime()
                self.level = self.synth.isSpeaking ? 0.45 + 0.4 * sin(t * 17) * sin(t * 5.3) : 0
            }
        }
    }

    /// Changes the volume of whatever is playing right now.
    func applyVolume() {
        player?.volume = Float(Settings.shared.volume)
    }

    func stop() {
        timers.forEach { $0.cancel() }
        timers = []
        meter?.invalidate()
        meter = nil
        player?.stop()
        player = nil
        if synth.isSpeaking { synth.stopSpeaking(at: .immediate) }
        level = 0
        onWord = nil
        onDone = nil
    }

    private func finished() {
        let done = onDone
        stop()
        done?()
    }

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        DispatchQueue.main.async { self.finished() }
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, willSpeakRangeOfSpeechString characterRange: NSRange, utterance: AVSpeechUtterance) {
        DispatchQueue.main.async {
            guard let index = self.wordOffsetsUTF16.lastIndex(where: { $0 <= characterRange.location }), index > self.lastWord else { return }
            for i in (self.lastWord + 1)...index { self.onWord?(i) }
            self.lastWord = index
        }
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        DispatchQueue.main.async { self.finished() }
    }

    /// Voices on the ElevenLabs account, for the menu.
    static func fetchVoices() async -> [(id: String, name: String)] {
        guard let key = Keychain.get(.elevenlabs) else { return [] }
        var request = URLRequest(url: URL(string: "https://api.elevenlabs.io/v1/voices")!)
        request.setValue(key, forHTTPHeaderField: "xi-api-key")
        guard let (data, _) = try? await URLSession.shared.data(for: request),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let voices = json["voices"] as? [[String: Any]] else { return [] }
        return voices.compactMap { v in
            guard let id = v["voice_id"] as? String, let name = v["name"] as? String else { return nil }
            return (id, name)
        }
    }
}

struct PreparedSpeech {
    let text: String
    /// nil means use the Mac's own voice.
    let audio: Data?
    let charStarts: [Double]?
}
