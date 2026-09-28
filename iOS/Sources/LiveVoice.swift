import AVFoundation
import Foundation

/// A live, two-way voice conversation with OpenAI's Realtime API, running on the phone:
/// the phone's mic listens (with echo cancellation) and his voice comes out of the phone's speaker.
/// Tools (look at the screen, point) run on the Mac.
final class LiveVoice: NSObject, ObservableObject {
    enum State: Equatable { case asleep, waking, listening, speaking }

    @Published private(set) var state: State = .asleep

    static let model = "gpt-realtime-2.1"

    /// Asks the Mac for a short-lived key. Calls back with nil on failure.
    var requestToken: ((@escaping (String?) -> Void) -> Void)?
    /// Runs a tool on the Mac: (name, JSON arguments) → (output text, optional JPEG base64).
    var runTool: ((String, String, @escaping (String, String?) -> Void) -> Void)?
    /// What he's saying, as it streams in. `done` is true when the reply finished.
    var onCaption: ((String, _ done: Bool) -> Void)?
    var onStateChange: ((State) -> Void)?

    /// Loudness of his voice right now, 0…1.
    private(set) var level: Double = 0

    var volume: Double {
        get { UserDefaults.standard.object(forKey: "volume") as? Double ?? 1 }
        set {
            UserDefaults.standard.set(newValue, forKey: "volume")
            player.volume = Float(newValue)
        }
    }

    private var socket: URLSessionWebSocketTask?
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private var converter: AVAudioConverter?
    private let wireFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 24000, channels: 1, interleaved: true)!
    private let playFormat = AVAudioFormat(standardFormatWithSampleRate: 24000, channels: 1)!
    private var audioReady = false

    private var transcript = ""
    private var pendingBuffers = 0
    private var responseActive = false
    private var sleepAfterReply = false
    // For cutting him off cleanly when you start talking over him.
    private var currentItem: String?
    private var itemSamples = 0
    private var itemStarted: Date?

    private func setState(_ new: State) {
        guard new != state else { return }
        state = new
        onStateChange?(new)
    }

    // MARK: Wake and sleep

    func toggle() {
        state == .asleep ? wake() : sleep()
    }

    func wake() {
        guard state == .asleep else { return }
        setState(.waking)
        AVAudioApplication.requestRecordPermission { granted in
            DispatchQueue.main.async {
                guard granted else {
                    self.onCaption?("I need the microphone. Turn it on for Googly Eyes in the iPhone's Settings.", true)
                    self.setState(.asleep)
                    return
                }
                guard let requestToken = self.requestToken else { self.setState(.asleep); return }
                requestToken { token in
                    DispatchQueue.main.async {
                        guard self.state == .waking else { return }
                        guard let token else { self.setState(.asleep); return }
                        self.connect(token)
                    }
                }
            }
        }
    }

    func sleep() {
        socket?.cancel(with: .normalClosure, reason: nil)
        socket = nil
        stopAudio()
        transcript = ""
        pendingBuffers = 0
        responseActive = false
        sleepAfterReply = false
        level = 0
        setState(.asleep)
    }

    /// Asks him to say something short out loud (for checking the volume).
    func sayHi() {
        guard socket != nil else { wake(); return }
        send(["type": "response.create", "response": ["instructions": "Say a quick, cheerful hi in under ten words so the user can check your volume."]])
    }

    // MARK: Connection

    private func connect(_ token: String) {
        var request = URLRequest(url: URL(string: "wss://api.openai.com/v1/realtime?model=\(Self.model)")!)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let socket = URLSession.shared.webSocketTask(with: request)
        self.socket = socket
        socket.resume()
        receive(on: socket)
        do {
            try startAudio()
        } catch {
            onCaption?("I couldn't start the microphone: \(error.localizedDescription)", true)
            sleep()
            return
        }
        setState(.listening)
        send(["type": "response.create", "response": ["instructions": "You just woke up. Say a tiny, cheerful hello (a few words)."]])
    }

    private func send(_ event: [String: Any]) {
        guard let socket, let data = try? JSONSerialization.data(withJSONObject: event),
              let text = String(data: data, encoding: .utf8) else { return }
        socket.send(.string(text)) { error in
            if let error { NSLog("Googly realtime send error: \(error)") }
        }
    }

    private func receive(on socket: URLSessionWebSocketTask) {
        socket.receive { [weak self] result in
            guard let self, socket === self.socket else { return }
            switch result {
            case .success(.string(let text)):
                if let data = text.data(using: .utf8),
                   let event = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    DispatchQueue.main.async { self.handle(event) }
                }
                self.receive(on: socket)
            case .success:
                self.receive(on: socket)
            case .failure(let error):
                NSLog("Googly realtime closed: \(error)")
                DispatchQueue.main.async { if socket === self.socket { self.sleep() } }
            }
        }
    }

    // MARK: Events

    private func handle(_ event: [String: Any]) {
        guard let type = event["type"] as? String else { return }
        switch type {
        case "response.created":
            responseActive = true
            transcript = ""

        case "response.output_audio.delta":
            if let delta = event["delta"] as? String, let data = Data(base64Encoded: delta) {
                play(data, item: event["item_id"] as? String)
            }

        case "response.output_audio_transcript.delta":
            if let delta = event["delta"] as? String {
                transcript += delta
                onCaption?(transcript, false)
            }

        case "input_audio_buffer.speech_started":
            interrupt()

        case "response.done":
            responseActive = false
            if !transcript.isEmpty { onCaption?(transcript, true) }
            let output = (event["response"] as? [String: Any])?["output"] as? [[String: Any]] ?? []
            let calls = output.filter { $0["type"] as? String == "function_call" }
            if !calls.isEmpty { runTools(calls) }
            finishIfQuiet()

        case "error":
            let message = ((event["error"] as? [String: Any])?["message"] as? String) ?? "Something went wrong."
            NSLog("Googly realtime error: \(message)")

        default:
            break
        }
    }

    private func runTools(_ calls: [[String: Any]]) {
        var remaining = calls.count
        var images: [String] = []
        for call in calls {
            let name = call["name"] as? String ?? ""
            let callID = call["call_id"] as? String ?? ""
            let arguments = call["arguments"] as? String ?? "{}"
            if name == "go_to_sleep" { sleepAfterReply = true }
            let finish: (String, String?) -> Void = { [weak self] output, image in
                DispatchQueue.main.async {
                    guard let self, self.socket != nil else { return }
                    self.send(["type": "conversation.item.create",
                               "item": ["type": "function_call_output", "call_id": callID, "output": output]])
                    if let image { images.append(image) }
                    remaining -= 1
                    if remaining == 0 {
                        // Screenshots go in as a user image so he can actually see the screen.
                        for image in images {
                            self.send(["type": "conversation.item.create",
                                       "item": ["type": "message", "role": "user",
                                                "content": [["type": "input_image", "image_url": "data:image/jpeg;base64,\(image)"]]]])
                        }
                        self.send(["type": "response.create"])
                    }
                }
            }
            if let runTool { runTool(name, arguments, finish) } else { finish("The Mac isn't connected.", nil) }
        }
    }

    /// Back to listening once he's finished talking (or asleep, if he said goodbye).
    private func finishIfQuiet() {
        guard pendingBuffers == 0, !responseActive else { return }
        if sleepAfterReply { sleep(); return }
        if state == .speaking { setState(.listening) }
    }

    // MARK: Audio

    private func startAudio() throws {
        #if os(iOS)
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetoothHFP])
        try session.setActive(true)
        #endif

        if !audioReady {
            let input = engine.inputNode
            try input.setVoiceProcessingEnabled(true)  // echo cancellation, so he doesn't hear himself
            engine.attach(player)
            engine.connect(player, to: engine.mainMixerNode, format: playFormat)
            player.installTap(onBus: 0, bufferSize: 1024, format: playFormat) { [weak self] buffer, _ in
                guard let self, let samples = buffer.floatChannelData?[0] else { return }
                var sum: Float = 0
                for i in 0..<Int(buffer.frameLength) { sum += samples[i] * samples[i] }
                let rms = sqrt(sum / Float(max(buffer.frameLength, 1)))
                self.level = min(1, Double(rms) * 5)
            }
            audioReady = true
        }
        let input = engine.inputNode
        let inputFormat = input.outputFormat(forBus: 0)
        converter = AVAudioConverter(from: inputFormat, to: wireFormat)
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 2400, format: inputFormat) { [weak self] buffer, _ in
            self?.sendMic(buffer)
        }
        player.volume = Float(volume)
        engine.prepare()
        try engine.start()
        player.play()
    }

    private func stopAudio() {
        engine.inputNode.removeTap(onBus: 0)
        player.stop()
        engine.stop()
        #if os(iOS)
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        #endif
    }

    private func sendMic(_ buffer: AVAudioPCMBuffer) {
        guard let converter else { return }
        let ratio = wireFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 32)
        guard let out = AVAudioPCMBuffer(pcmFormat: wireFormat, frameCapacity: capacity) else { return }
        var fed = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, out.frameLength > 0, let samples = out.int16ChannelData?[0] else { return }
        let data = Data(bytes: samples, count: Int(out.frameLength) * 2)
        DispatchQueue.main.async {
            self.send(["type": "input_audio_buffer.append", "audio": data.base64EncodedString()])
        }
    }

    private func play(_ pcm: Data, item: String?) {
        let frames = pcm.count / 2
        guard frames > 0, let buffer = AVAudioPCMBuffer(pcmFormat: playFormat, frameCapacity: AVAudioFrameCount(frames)),
              let out = buffer.floatChannelData?[0] else { return }
        buffer.frameLength = AVAudioFrameCount(frames)
        pcm.withUnsafeBytes { raw in
            let ints = raw.bindMemory(to: Int16.self)
            for i in 0..<frames { out[i] = Float(Int16(littleEndian: ints[i])) / 32768 }
        }
        if item != currentItem {
            currentItem = item
            itemSamples = 0
            itemStarted = nil
        }
        if itemStarted == nil { itemStarted = Date() }
        itemSamples += frames
        pendingBuffers += 1
        setState(.speaking)
        player.scheduleBuffer(buffer) { [weak self] in
            DispatchQueue.main.async {
                guard let self else { return }
                self.pendingBuffers = max(0, self.pendingBuffers - 1)
                if self.pendingBuffers == 0 { self.level = 0 }
                self.finishIfQuiet()
            }
        }
    }

    /// You started talking over him: stop his audio and tell the server how much you actually heard.
    private func interrupt() {
        guard pendingBuffers > 0 else { return }
        if let item = currentItem, let started = itemStarted {
            let heardMs = min(Double(itemSamples) / 24, Date().timeIntervalSince(started) * 1000)
            send(["type": "conversation.item.truncate", "item_id": item, "content_index": 0, "audio_end_ms": Int(heardMs)])
        }
        player.stop()
        player.play()
        pendingBuffers = 0
        level = 0
        currentItem = nil
        setState(.listening)
    }
}
