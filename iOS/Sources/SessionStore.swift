import Foundation

/// One line in a session: something you said (overheard as context), a question you asked by holding
/// the screen, or one of his replies.
struct TranscriptEntry: Codable, Identifiable, Equatable {
    enum Kind: String, Codable { case heard, asked, reply, report }

    var id = UUID()
    var kind: Kind
    var text: String
    var time: Date
    /// The Realtime conversation item this came from, so its transcript can be filled in when it arrives.
    var itemID: String?
}

/// Everything from one wake-to-sleep session.
struct BlueySession: Codable, Identifiable, Equatable {
    var id = UUID()
    var started: Date
    var ended: Date?
    var entries: [TranscriptEntry] = []

    var questionCount: Int { entries.filter { $0.kind == .asked }.count }

    /// A short line for the session list: the first thing you asked, or the first thing you said.
    var summary: String {
        let first = entries.first { $0.kind == .asked && !$0.text.isEmpty } ?? entries.first { !$0.text.isEmpty }
        return first?.text ?? "Nothing said yet"
    }

    var duration: TimeInterval { (ended ?? Date()).timeIntervalSince(started) }

    /// Plain text, for sharing.
    var exportText: String {
        let time = DateFormatter()
        time.dateFormat = "h:mm:ss a"
        var lines = ["Bluey session, \(started.formatted(date: .abbreviated, time: .shortened))", ""]
        for entry in entries where !entry.text.isEmpty {
            let who: String
            switch entry.kind {
            case .heard: who = "You"
            case .asked: who = "You asked"
            case .reply: who = "Bluey"
            case .report: who = "Research"
            }
            lines.append("[\(time.string(from: entry.time))] \(who): \(entry.text)")
        }
        return lines.joined(separator: "\n")
    }
}

/// Keeps every session on the phone, as one JSON file in the app's Documents folder.
final class SessionStore: ObservableObject {
    @Published private(set) var sessions: [BlueySession] = []  // newest first
    @Published private(set) var currentID: UUID?

    private let fileURL: URL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("sessions.json")
    private var saveWork: DispatchWorkItem?

    init() {
        if let data = try? Data(contentsOf: fileURL),
           let saved = try? JSONDecoder().decode([BlueySession].self, from: data) {
            // A session left open by a crash or force-quit is closed at its last line.
            sessions = saved.map { session in
                var session = session
                if session.ended == nil { session.ended = session.entries.last?.time ?? session.started }
                return session
            }
        }
    }

    var current: BlueySession? { sessions.first { $0.id == currentID } }

    func start() {
        end()
        let session = BlueySession(started: Date())
        sessions.insert(session, at: 0)
        currentID = session.id
        save()
    }

    func end() {
        guard let id = currentID, let index = sessions.firstIndex(where: { $0.id == id }) else { return }
        currentID = nil
        if sessions[index].entries.allSatisfy({ $0.text.isEmpty }) {
            sessions.remove(at: index)  // nothing was said: don't keep an empty session
        } else {
            sessions[index].ended = Date()
            sessions[index].entries.removeAll { $0.text.isEmpty }
        }
        save()
    }

    /// Holds a spot for something you said, in the order you said it. The words arrive a moment later.
    func placeholder(itemID: String, asked: Bool) {
        update { session in
            guard !session.entries.contains(where: { $0.itemID == itemID }) else { return }
            session.entries.append(TranscriptEntry(kind: asked ? .asked : .heard, text: "", time: Date(), itemID: itemID))
        }
    }

    func heard(itemID: String, text: String, asked: Bool) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        update { session in
            if let i = session.entries.firstIndex(where: { $0.itemID == itemID }) {
                session.entries[i].text = text
                if asked { session.entries[i].kind = .asked }
            } else if !text.isEmpty {
                session.entries.append(TranscriptEntry(kind: asked ? .asked : .heard, text: text, time: Date(), itemID: itemID))
            }
        }
    }

    func reply(_ text: String) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        update { $0.entries.append(TranscriptEntry(kind: .reply, text: text, time: Date())) }
    }

    func report(_ text: String) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        update { $0.entries.append(TranscriptEntry(kind: .report, text: text, time: Date())) }
    }

    func delete(_ id: UUID) {
        if id == currentID { currentID = nil }
        sessions.removeAll { $0.id == id }
        save()
    }

    private func update(_ change: (inout BlueySession) -> Void) {
        guard let id = currentID, let index = sessions.firstIndex(where: { $0.id == id }) else { return }
        change(&sessions[index])
        scheduleSave()
    }

    private func scheduleSave() {
        saveWork?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.save() }
        saveWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 1, execute: work)
    }

    private func save() {
        saveWork?.cancel()
        guard let data = try? JSONEncoder().encode(sessions) else { return }
        try? data.write(to: fileURL, options: .atomic)
    }
}
