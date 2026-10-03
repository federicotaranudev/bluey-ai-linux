import Foundation
import Network

/// Everything the Mac and the iPhone agree on: the Bonjour service name,
/// what a "face update" looks like, and how lines of JSON travel over TCP.
public enum GooglyService {
    public static let type = "_googly._tcp"
}

public enum Mood: String, Codable, CaseIterable, Sendable {
    case listening, resting, thinking, talking, pointing, happy, sleepy

    public var title: String { rawValue.capitalized }
}

/// What the phone should show right now.
public struct FaceState: Codable, Equatable, Sendable {
    /// Where the eyes look. x: -1 left … 1 right. y: -1 up … 1 down.
    public var gazeX: Double
    public var gazeY: Double
    public var mood: Mood
    /// Voice level, 0…1. Drives the talking bounce.
    public var talk: Double

    public init(gazeX: Double = 0, gazeY: Double = 0, mood: Mood = .listening, talk: Double = 0) {
        self.gazeX = gazeX
        self.gazeY = gazeY
        self.mood = mood
        self.talk = talk
    }
}

public struct Packet: Codable, Sendable {
    public var face: FaceState?
    /// Sent once by each side after connecting, with a device name.
    public var hello: String?
    /// Voice volume 0…1. The Mac shares it, the phone sets it.
    public var volume: Double?
    /// A request or event: "testVoice" (phone→Mac), "playing"/"done" (phone→Mac), "stopSpeech" (Mac→phone).
    public var command: String?
    /// Speech audio (mp3, base64) for the phone to play.
    public var audio: String?
    /// Which speech an audio packet or a playing/done event belongs to.
    public var speech: Int?
    /// Pairs a request with its reply (tool calls, realtime tokens).
    public var callID: String?
    /// Tool name for a "tool" request.
    public var tool: String?
    /// Free text: tool arguments or output, a token, a caption.
    public var text: String?
    /// A JPEG (base64) that goes with a tool result.
    public var image: String?
    /// A Realtime WebSocket address supplied by the desktop instead of OpenAI.
    /// The desktop sends this when it runs its own voice proxy (e.g. a Groq key).
    public var endpoint: String?

    public init(face: FaceState? = nil, hello: String? = nil, volume: Double? = nil, command: String? = nil,
                audio: String? = nil, speech: Int? = nil, callID: String? = nil, tool: String? = nil,
                text: String? = nil, image: String? = nil, endpoint: String? = nil) {
        self.face = face
        self.hello = hello
        self.volume = volume
        self.command = command
        self.audio = audio
        self.speech = speech
        self.callID = callID
        self.tool = tool
        self.text = text
        self.image = image
        self.endpoint = endpoint
    }
}

public extension NWParameters {
    static var googly: NWParameters {
        let tcp = NWProtocolTCP.Options()
        tcp.noDelay = true
        tcp.enableKeepalive = true
        tcp.keepaliveIdle = 2
        let params = NWParameters(tls: nil, tcp: tcp)
        params.includePeerToPeer = true
        return params
    }
}

/// Newline-delimited JSON over one NWConnection. All callbacks run on the main queue.
public final class LineConnection {
    public let connection: NWConnection
    public var onPacket: ((Packet) -> Void)?
    public var onState: ((NWConnection.State) -> Void)?

    private var buffer = Data()
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    public init(_ connection: NWConnection) {
        self.connection = connection
    }

    public func start() {
        connection.stateUpdateHandler = { [weak self] state in
            self?.onState?(state)
        }
        connection.start(queue: .main)
        receive()
    }

    public func send(_ packet: Packet) {
        guard var data = try? encoder.encode(packet) else { return }
        data.append(0x0A)
        connection.send(content: data, completion: .contentProcessed { _ in })
    }

    public func cancel() {
        connection.cancel()
    }

    private func receive() {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 1024 * 1024) { [weak self] data, _, isComplete, error in
            guard let self else { return }
            if let data, !data.isEmpty {
                self.buffer.append(data)
                while let newline = self.buffer.firstIndex(of: 0x0A) {
                    let line = self.buffer[self.buffer.startIndex..<newline]
                    self.buffer.removeSubrange(self.buffer.startIndex...newline)
                    if let packet = try? self.decoder.decode(Packet.self, from: line) {
                        self.onPacket?(packet)
                    }
                }
            }
            if isComplete || error != nil {
                self.connection.cancel()
            } else {
                self.receive()
            }
        }
    }
}
