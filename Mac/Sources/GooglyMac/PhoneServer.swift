import Foundation
import Network
import GooglyShared

/// Advertises the Mac on the local network and streams face updates to any phone that connects.
final class PhoneServer {
    private var listener: NWListener?
    private var phones: [ObjectIdentifier: (link: LineConnection, name: String?)] = [:]
    private var pending: [ObjectIdentifier: LineConnection] = [:]
    private var lastSent: FaceState?
    private var restartWork: DispatchWorkItem?

    /// Called with the names of connected phones whenever that list changes.
    var onPhonesChanged: (([String]) -> Void)?

    /// Called when a phone asks for something (test voice, new volume).
    var onRequest: ((Packet) -> Void)?

    var phoneNames: [String] { phones.values.map { $0.name ?? "iPhone" } }

    func start() {
        do {
            let listener = try NWListener(using: .googly)
            listener.service = NWListener.Service(name: Host.current().localizedName ?? "Mac", type: GooglyService.type)
            listener.newConnectionHandler = { [weak self] connection in
                self?.accept(connection)
            }
            listener.stateUpdateHandler = { [weak self] state in
                if case .failed = state { self?.scheduleRestart() }
            }
            listener.start(queue: .main)
            self.listener = listener
        } catch {
            NSLog("Googly: could not start listener: \(error)")
            scheduleRestart()
        }
    }

    private func scheduleRestart() {
        listener?.cancel()
        listener = nil
        restartWork?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.start() }
        restartWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 2, execute: work)
    }

    private func accept(_ connection: NWConnection) {
        let link = LineConnection(connection)
        let id = ObjectIdentifier(link)
        pending[id] = link  // keep it alive until it is ready
        link.onState = { [weak self, weak link] state in
            guard let self, let link else { return }
            switch state {
            case .ready:
                self.pending[id] = nil
                self.phones[id] = (link, nil)
                link.send(Packet(hello: Host.current().localizedName ?? "Mac", volume: Settings.shared.volume))
                if let face = self.lastSent { link.send(Packet(face: face)) }
                self.onPhonesChanged?(self.phoneNames)
            case .failed, .cancelled:
                self.pending[id] = nil
                self.phones[id] = nil
                self.onPhonesChanged?(self.phoneNames)
            default:
                break
            }
        }
        link.onPacket = { [weak self] packet in
            guard let self, self.phones[id] != nil else { return }
            if let name = packet.hello {
                self.phones[id]?.name = name
                self.onPhonesChanged?(self.phoneNames)
            }
            if packet.command != nil || packet.volume != nil { self.onRequest?(packet) }
        }
        link.start()
    }

    /// Tells every phone the current volume (after it changes on the Mac).
    func sendVolume(_ volume: Double) {
        for phone in phones.values { phone.link.send(Packet(volume: volume)) }
    }

    /// Sends the face to every phone, skipping updates too small to see.
    func send(_ face: FaceState) {
        if let last = lastSent,
           last.mood == face.mood,
           abs(last.gazeX - face.gazeX) < 0.004,
           abs(last.gazeY - face.gazeY) < 0.004,
           abs(last.talk - face.talk) < 0.02 {
            return
        }
        lastSent = face
        for phone in phones.values { phone.link.send(Packet(face: face)) }
    }
}
