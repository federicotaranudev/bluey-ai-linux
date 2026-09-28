import Foundation

/// What the character decided to do.
struct Reply {
    struct Point {
        let target: String
        /// Index of the word in `say` at which the cursor should land.
        let atWord: Int
    }

    let say: String
    let points: [Point]
    let mood: String
}

enum BrainError: LocalizedError {
    case noKey
    case http(Int, String)
    case refused
    case badReply

    var errorDescription: String? {
        switch self {
        case .noKey: return "I need a Claude API key. Add one under API Keys in the menu bar."
        case .http(let code, let body): return "Claude returned an error (\(code)): \(body.prefix(160))"
        case .refused: return "Hmm, I can't help with that one."
        case .badReply: return "I got a reply I couldn't read."
        }
    }
}

/// Asks Claude what to say and which on-screen targets to point at, over the Messages API.
final class Brain {
    private var history: [(question: String, answer: String)] = []

    var model: String {
        UserDefaults.standard.string(forKey: "claudeModel") ?? "claude-opus-5"
    }

    private let system = """
    You are a small, cute blueberry character with big googly eyes. You live on an iPhone that sits just under the \
    user's computer screen, and you point at things on that screen with your own big cursor. The user is filming a \
    video, so answer out loud like a friendly co-host: short, warm, natural spoken sentences, usually one to three. \
    No lists, markdown, emoji, or reading out coordinates.

    You get a screenshot of the screen plus a list of every piece of text on it. Lines have ids like L12 and single \
    words have ids like W87. When you talk about something visible, point at it: add a point with that target's id \
    and at_word, the 0-based index of the word in your "say" text where the cursor should land (the word that \
    refers to it, like "this" or the name itself). Prefer the most specific id (a single word or number over a whole \
    line). Only use ids from the list. Point at nothing when nothing on screen is relevant. Keep points in speaking \
    order.

    mood is how your face looks while you talk: "happy" for good news or jokes, "thinking" when unsure, otherwise \
    "listening".
    """

    private let schema: [String: Any] = [
        "type": "object",
        "properties": [
            "say": ["type": "string"],
            "points": [
                "type": "array",
                "items": [
                    "type": "object",
                    "properties": [
                        "target": ["type": "string"],
                        "at_word": ["type": "integer"],
                    ],
                    "required": ["target", "at_word"],
                    "additionalProperties": false,
                ],
            ],
            "mood": ["type": "string", "enum": ["listening", "happy", "thinking"]],
        ],
        "required": ["say", "points", "mood"],
        "additionalProperties": false,
    ]

    func ask(_ question: String, screen: ScreenSnapshot?) async throws -> Reply {
        guard let key = Keychain.get(.anthropic) else { throw BrainError.noKey }

        var messages: [[String: Any]] = []
        for turn in history.suffix(6) {
            messages.append(["role": "user", "content": turn.question])
            messages.append(["role": "assistant", "content": turn.answer])
        }
        var content: [[String: Any]] = []
        if let screen {
            content.append(["type": "image",
                            "source": ["type": "base64", "media_type": "image/jpeg", "data": screen.jpeg.base64EncodedString()]])
            content.append(["type": "text", "text": "Text on screen (id @x,y on a 0-1000 grid \"text\" | word ids):\n" + screen.targetList])
        }
        content.append(["type": "text", "text": "The user says: \(question)"])
        messages.append(["role": "user", "content": content])

        let body: [String: Any] = [
            "model": model,
            "max_tokens": 16000,
            "system": system,
            "thinking": ["type": "adaptive"],
            "output_config": [
                "effort": "low",  // quick answers matter more than deep reasoning for a live co-host
                "format": ["type": "json_schema", "schema": schema],
            ],
            "fallbacks": "default",
            "messages": messages,
        ]

        var request = URLRequest(url: URL(string: "https://api.anthropic.com/v1/messages")!)
        request.httpMethod = "POST"
        request.timeoutInterval = 90
        request.setValue("application/json", forHTTPHeaderField: "content-type")
        request.setValue(key, forHTTPHeaderField: "x-api-key")
        request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
        request.setValue("server-side-fallback-2026-07-01", forHTTPHeaderField: "anthropic-beta")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, response) = try await URLSession.shared.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200 else { throw BrainError.http(status, String(data: data, encoding: .utf8) ?? "") }

        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw BrainError.badReply }
        if json["stop_reason"] as? String == "refusal" { throw BrainError.refused }
        let blocks = json["content"] as? [[String: Any]] ?? []
        guard let text = blocks.last(where: { $0["type"] as? String == "text" })?["text"] as? String,
              let replyJSON = try JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any],
              let say = replyJSON["say"] as? String else { throw BrainError.badReply }

        let points = (replyJSON["points"] as? [[String: Any]] ?? []).compactMap { p -> Reply.Point? in
            guard let target = p["target"] as? String else { return nil }
            return Reply.Point(target: target, atWord: p["at_word"] as? Int ?? 0)
        }
        history.append((question, say))
        return Reply(say: say, points: points, mood: replyJSON["mood"] as? String ?? "listening")
    }
}
