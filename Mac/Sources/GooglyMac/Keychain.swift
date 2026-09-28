import Foundation

/// API keys live in a private file in ~/Library/Application Support/Googly (readable only by you),
/// never in the project folder or git. Unlike the Keychain, this never asks for your password.
enum Keychain {
    enum Key: String {
        case anthropic, elevenlabs, openai
    }

    private static var cache: [String: String]?

    private static var fileURL: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Googly", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true,
                                                 attributes: [.posixPermissions: 0o700])
        return dir.appendingPathComponent("keys.json")
    }

    private static func load() -> [String: String] {
        if let cache { return cache }
        let stored = (try? Data(contentsOf: fileURL))
            .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: String] } ?? [:]
        cache = stored
        return stored
    }

    static func get(_ key: Key) -> String? {
        guard let value = load()[key.rawValue], !value.isEmpty else { return nil }
        return value
    }

    static func set(_ key: Key, _ value: String?) {
        var all = load()
        all[key.rawValue] = value?.isEmpty == false ? value : nil
        cache = all
        guard let data = try? JSONSerialization.data(withJSONObject: all, options: [.prettyPrinted, .sortedKeys]) else { return }
        FileManager.default.createFile(atPath: fileURL.path, contents: data, attributes: [.posixPermissions: 0o600])
    }
}
