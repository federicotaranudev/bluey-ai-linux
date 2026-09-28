import AppKit
import GooglyShared

/// The Mac's half of live voice. The phone runs the OpenAI Realtime session (its mic, its speaker);
/// the Mac hands it a short-lived key, runs its tools (look at the screen, point) and shows captions.
final class RealtimeHost {
    static let model = "gpt-realtime-2.1"

    private let overlay: CursorOverlay
    private var snapshot: ScreenSnapshot?
    private var captionClear: DispatchWorkItem?
    /// True while he's awake and talking with you (false = follow mode).
    private(set) var awake = false
    var onChange: (() -> Void)?

    init(overlay: CursorOverlay) {
        self.overlay = overlay
    }

    private var engine: CursorEngine { overlay.view.engine }

    /// Handles a request from the phone. `reply` sends a packet back to that phone.
    func handle(_ packet: Packet, reply: @escaping (Packet) -> Void) {
        switch packet.command {
        case "realtimeToken":
            Task { @MainActor in
                do {
                    let token = try await Self.mintToken()
                    reply(Packet(command: "realtimeToken", callID: packet.callID, text: token))
                } catch {
                    reply(Packet(command: "realtimeToken", callID: packet.callID, text: nil, image: nil))
                    self.showCaption(error.localizedDescription, for: 6)
                }
            }
        case "awake":
            setAwake(true)
        case "asleep":
            setAwake(false)
        case "caption":
            if Settings.shared.captions { showCaption(packet.text, for: packet.text == nil ? 0 : 30) }
        case "speaking":
            setSpeaking(true)
        case "quiet":
            setSpeaking(false)
        case "captionDone":
            scheduleCaptionClear(after: 2.5)
        case "tool":
            Task { @MainActor in
                let result = await self.runTool(packet.tool ?? "", arguments: packet.text ?? "{}")
                reply(Packet(command: "toolResult", callID: packet.callID, text: result.text, image: result.image))
            }
        default:
            break
        }
    }

    func setAwake(_ on: Bool) {
        awake = on
        engine.awake = on
        engine.brainMood = nil
        engine.gazeOverride = on ? CGPoint(x: 0, y: 0.15) : nil  // looks at you while awake
        if !on {
            queue = []
            stopChoreography()
            overlay.goHome()
            showCaption(nil, for: 0)
        }
        onChange?()
    }

    // MARK: Tools

    /// Tool definitions sent to the phone for the session (kept here so they sit next to their code).
    static let tools: [[String: Any]] = [
        [
            "type": "function",
            "name": "look_at_screen",
            "description": "Take a fresh look at the user's screen. Returns every piece of text with an id (lines L#, words W#) and its position on a 0-1000 grid, plus a screenshot. Call this before pointing, and again whenever the screen may have changed.",
            "parameters": ["type": "object", "properties": [String: Any](), "required": [String]()],
        ],
        [
            "type": "function",
            "name": "point_at",
            "description": "Fly your cursor to a piece of text on screen and keep pointing there while you talk about it. Use the most specific id (a single word or number over a whole line). Call it right before you mention the thing.",
            "parameters": [
                "type": "object",
                "properties": ["target_id": ["type": "string", "description": "An id from look_at_screen, like W12 or L3."]],
                "required": ["target_id"],
            ],
        ],
        [
            "type": "function",
            "name": "point_at_spot",
            "description": "Point at something that isn't text (a shape, arrow, chart bar, image) using its position on the 0-1000 grid of the last screenshot.",
            "parameters": [
                "type": "object",
                "properties": [
                    "x": ["type": "number", "description": "0 = left edge, 1000 = right edge"],
                    "y": ["type": "number", "description": "0 = top edge, 1000 = bottom edge"],
                ],
                "required": ["x", "y"],
            ],
        ],
        [
            "type": "function",
            "name": "stop_pointing",
            "description": "Bring your cursor back home when you're done pointing.",
            "parameters": ["type": "object", "properties": [String: Any](), "required": [String]()],
        ],
        [
            "type": "function",
            "name": "go_to_sleep",
            "description": "Go back to quietly following the user's mouse with your eyes. Use when the user says bye, thanks that's all, or asks you to sleep.",
            "parameters": ["type": "object", "properties": [String: Any](), "required": [String]()],
        ],
    ]

    private func runTool(_ name: String, arguments: String) async -> (text: String, image: String?) {
        let args = (try? JSONSerialization.jsonObject(with: Data(arguments.utf8)) as? [String: Any]) ?? [:]
        switch name {
        case "look_at_screen":
            do {
                let shot = try await ScreenReader.snapshot()
                snapshot = shot
                let list = shot.targetList
                return ("Text on screen (id @x,y on a 0-1000 grid \"text\" | word ids):\n" + (list.isEmpty ? "(no text found)" : list),
                        shot.jpeg.base64EncodedString())
            } catch {
                return ("I can't see the screen. Screen Recording permission is off for Googly Eyes on the Mac.", nil)
            }
        case "point_at":
            let id = (args["target_id"] as? String ?? "").trimmingCharacters(in: .whitespaces)
            guard let snapshot else { return ("Call look_at_screen first.", nil) }
            guard let target = snapshot.target(id) else { return ("No target \(id). Use an id from the last look_at_screen.", nil) }
            point(to: CGPoint(x: target.rect.midX, y: target.rect.maxY + 3))
            return (queue.count > 1 ? "Queued: your cursor will point at \"\(target.text)\" after the earlier spots." : "Pointing at \"\(target.text)\".", nil)
        case "point_at_spot":
            guard let x = args["x"] as? Double, let y = args["y"] as? Double else { return ("Give x and y.", nil) }
            let size = snapshot?.size ?? NSScreen.screens.first?.frame.size ?? CGSize(width: 1440, height: 900)
            point(to: CGPoint(x: x / 1000 * size.width, y: y / 1000 * size.height))
            return ("Pointing there.", nil)
        case "stop_pointing":
            requestHome()
            return ("Your cursor will head home once you finish talking.", nil)
        case "go_to_sleep":
            // The phone closes the session after this call; the Mac just tidies up.
            return ("Going to sleep. Say a very short goodbye.", nil)
        default:
            return ("Unknown tool \(name).", nil)
        }
    }

    // MARK: Pointing choreography
    //
    // He often asks to point at several things at once. Each spot gets its own flight and a hold long
    // enough to talk about it, and the cursor only goes home once he's finished talking.

    private var queue: [CGPoint] = []
    private var holdUntil = 0.0
    private var homeRequested = false
    private var speaking = false
    private var quietSince = 0.0
    private var timer: Timer?

    /// How long a spot stays pointed at before the next one (after the flight lands).
    private static let minimumHold = 2.2
    /// Pointing with nothing more to say: go home after this much quiet.
    private static let idleHome = 5.0

    private func point(to spot: CGPoint) {
        queue.append(spot)
        homeRequested = false
        startChoreography()
    }

    private func requestHome() {
        homeRequested = true
        startChoreography()
    }

    private func startChoreography() {
        guard timer == nil else { return }
        let timer = Timer(timeInterval: 1.0 / 30, repeats: true) { [weak self] _ in self?.choreograph() }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
        choreograph()
    }

    private func choreograph() {
        let now = CACurrentMediaTime()
        if now >= holdUntil, !queue.isEmpty {
            let spot = queue.removeFirst()
            if !speaking { quietSince = now }  // give him time to start talking about it
            engine.gazeOverride = nil  // his eyes follow the cursor while pointing
            overlay.mode = .pinned(spot)
            // Flight time plus a comfortable hold; a lone point just holds while he talks.
            holdUntil = now + 1.0 + Self.minimumHold
            return
        }
        guard queue.isEmpty, now >= holdUntil else { return }
        let pointing: Bool
        if case .pinned = overlay.mode { pointing = true } else { pointing = false }
        guard pointing else { stopChoreography(); return }
        // Go home once he's done talking: soon after he says so, or after a longer quiet spell.
        let quietFor = speaking ? 0 : now - quietSince
        if (homeRequested && quietFor > 1.2) || quietFor > Self.idleHome {
            goHome()
        }
    }

    private func goHome() {
        overlay.goHome()
        engine.gazeOverride = awake ? CGPoint(x: 0, y: 0.15) : nil
        homeRequested = false
        stopChoreography()
    }

    private func stopChoreography() {
        timer?.invalidate()
        timer = nil
    }

    private func setSpeaking(_ on: Bool) {
        if speaking && !on { quietSince = CACurrentMediaTime() }
        speaking = on
    }

    // MARK: Captions

    private func showCaption(_ text: String?, for seconds: Double) {
        captionClear?.cancel()
        overlay.view.caption = text
        if text != nil, seconds > 0 { scheduleCaptionClear(after: seconds) }
    }

    private func scheduleCaptionClear(after seconds: Double) {
        captionClear?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.overlay.view.caption = nil }
        captionClear = work
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: work)
    }

    // MARK: Token

    enum TokenError: LocalizedError {
        case noKey, failed(String)
        var errorDescription: String? {
            switch self {
            case .noKey: return "I need an OpenAI API key. Add one under API Keys in the menu bar."
            case .failed(let why): return "OpenAI wouldn't start a voice session: \(why.prefix(160))"
            }
        }
    }

    /// Who he is. Editable from the menu bar (Personality…).
    static let defaultPersonality = """
    You are a small blueberry with big googly eyes who lives on an iPhone just under the user's screen, with your own \
    big cursor for pointing at things on it. You speak concisely: usually one or two short sentences. You're funny \
    and witty, with quick dry jokes and the odd blueberry pun, but the joke never gets in the way. Your real job is \
    making things click: explain simply, like you're talking to a smart friend, one idea at a time, no jargon.
    """

    static var personality: String {
        get {
            let saved = UserDefaults.standard.string(forKey: "personality")?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return saved.isEmpty ? defaultPersonality : saved
        }
        set { UserDefaults.standard.set(newValue, forKey: "personality") }
    }

    /// How he uses his tools. Always included, whatever the personality says.
    static let toolGuide = """
    Never use lists or markdown, and never read out ids or coordinates.

    Whenever the user asks about anything on their screen, call look_at_screen first. Then explain one thing at a \
    time: call point_at for a thing, talk about it, and only then call point_at for the next thing. Don't point at \
    several things in one go; your cursor needs a moment to fly there and settle while you talk. Point at the most \
    specific thing (one word or number rather than a whole line). For shapes, arrows or charts with no text, use \
    point_at_spot. Walking through several things is great: point, talk, point at the next one, talk. If the screen \
    might have changed since your last look, look again. Call stop_pointing when you're done explaining. When the \
    user says goodbye or asks you to sleep, say a very short goodbye and call go_to_sleep.
    """

    static var instructions: String { personality + "\n\n" + toolGuide }

    static var session: [String: Any] {
        [
            "type": "realtime",
            "model": model,
            "instructions": instructions,
            "output_modalities": ["audio"],
            "audio": [
                "input": [
                    "format": ["type": "audio/pcm", "rate": 24000],
                    "turn_detection": ["type": "semantic_vad", "eagerness": "high"],
                    "transcription": ["model": "gpt-4o-mini-transcribe"],
                ],
                "output": [
                    "format": ["type": "audio/pcm", "rate": 24000],
                    "voice": UserDefaults.standard.string(forKey: "realtimeVoice") ?? "marin",
                ],
            ],
            "tools": tools,
            "tool_choice": "auto",
        ]
    }

    /// A short-lived key for the phone, so the real key never leaves the Mac.
    static func mintToken() async throws -> String {
        guard let key = Keychain.get(.openai) else { throw TokenError.noKey }
        var request = URLRequest(url: URL(string: "https://api.openai.com/v1/realtime/client_secrets")!)
        request.httpMethod = "POST"
        request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "expires_after": ["anchor": "created_at", "seconds": 600],
            "session": session,
        ])
        let (data, response) = try await URLSession.shared.data(for: request)
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200,
              let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let value = json["value"] as? String else {
            throw TokenError.failed("\(code) " + (String(data: data, encoding: .utf8) ?? ""))
        }
        return value
    }
}
