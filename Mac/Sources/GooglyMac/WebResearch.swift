import Foundation

/// A short researched answer: a title, up to four paragraphs, and where it came from.
struct ResearchReport {
    var question: String
    var title: String
    var paragraphs: [String]
    var sources: [(title: String, url: URL)]

    /// Plain text, for him (and the phone's saved transcript).
    var plainText: String {
        ([title] + paragraphs).joined(separator: "\n\n")
    }
}

/// Looks things up on the web with OpenAI's Responses API and its web search tool.
enum WebResearch {
    static let model = "gpt-5.5"

    enum ResearchError: LocalizedError {
        case noKey, failed(String)
        var errorDescription: String? {
            switch self {
            case .noKey: return "I need an OpenAI API key for research. Add one under OpenAI Key in the menu bar."
            case .failed(let why): return "The research didn't come back: \(why.prefix(160))"
            }
        }
    }

    static func research(_ question: String, context: String?) async throws -> ResearchReport {
        guard let key = Keychain.get(.openai) else { throw ResearchError.noKey }
        var request = URLRequest(url: URL(string: "https://api.openai.com/v1/responses")!)
        request.httpMethod = "POST"
        request.timeoutInterval = 90
        request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var input = question
        if let context, !context.isEmpty { input += "\n\nWhat's on the user's screen, for context: \(context)" }
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "model": model,
            "reasoning": ["effort": "low"],
            "tools": [["type": "web_search"]],
            "instructions": """
            Research the question on the web and write a short, friendly report for a busy person. \
            First line: a plain title of under eight words. Then one to four short paragraphs (four at most), \
            leading with the direct answer, then the most useful details. Plain text only: no markdown, \
            no headings, no bullet lists, and no links or citations in the text.
            """,
            "input": input,
        ])
        let (data, response) = try await URLSession.shared.data(for: request)
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200, let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw ResearchError.failed("\(code) " + (String(data: data, encoding: .utf8) ?? ""))
        }

        var text = ""
        var sources: [(String, URL)] = []
        for item in json["output"] as? [[String: Any]] ?? [] where item["type"] as? String == "message" {
            for part in item["content"] as? [[String: Any]] ?? [] {
                text += part["text"] as? String ?? ""
                for note in part["annotations"] as? [[String: Any]] ?? [] {
                    guard let link = note["url"] as? String, let url = URL(string: Self.clean(link)),
                          !sources.contains(where: { $0.1 == url }) else { continue }
                    sources.append((note["title"] as? String ?? url.host() ?? link, url))
                }
            }
        }
        // Drop inline citations like "([site.com](https://…))" and any stray markdown.
        text = text.replacingOccurrences(of: #"\s*\(\[[^\]]*\]\([^)]*\)\)"#, with: "", options: .regularExpression)
        text = text.replacingOccurrences(of: #"\[([^\]]*)\]\([^)]*\)"#, with: "$1", options: .regularExpression)
        text = text.replacingOccurrences(of: #"\s*\[https?://[^\]]*\]"#, with: "", options: .regularExpression)
        text = text.replacingOccurrences(of: "**", with: "").replacingOccurrences(of: "#", with: "")
        let blocks = text.components(separatedBy: "\n").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        guard let title = blocks.first else { throw ResearchError.failed("empty answer") }
        return ResearchReport(question: question, title: title, paragraphs: Array(blocks.dropFirst().prefix(4)),
                              sources: Array(sources.prefix(5)))
    }

    /// Removes tracking parameters the search tool adds.
    private static func clean(_ link: String) -> String {
        guard var parts = URLComponents(string: link) else { return link }
        parts.queryItems = parts.queryItems?.filter { $0.name != "utm_source" }
        if parts.queryItems?.isEmpty == true { parts.queryItems = nil }
        return parts.string ?? link
    }
}
