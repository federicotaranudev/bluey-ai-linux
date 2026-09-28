import AVFoundation

/// Plays his voice from the phone's own speaker, so the sound comes from the character.
final class SpeechPlayer: NSObject, AVAudioPlayerDelegate {
    private var player: AVAudioPlayer?
    private var currentID: Int?
    /// Reports "playing" and "done" back to the Mac, which keeps the pointing in sync.
    var onEvent: ((_ event: String, _ id: Int) -> Void)?

    /// Voice volume on this phone, 0…1.
    var volume: Double {
        get { UserDefaults.standard.object(forKey: "volume") as? Double ?? 1 }
        set {
            UserDefaults.standard.set(newValue, forKey: "volume")
            player?.volume = Float(newValue)
        }
    }

    /// Loudness right now, 0…1, for the talking bounce.
    var level: Double {
        guard let player, player.isPlaying else { return 0 }
        player.updateMeters()
        return min(1, max(0, pow(10, Double(player.averagePower(forChannel: 0)) / 20) * 3.2))
    }

    override init() {
        super.init()
        #if os(iOS)
        // Play even with the ring/silent switch on.
        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
        try? AVAudioSession.sharedInstance().setActive(true)
        #endif
    }

    func play(base64: String, id: Int) {
        stop()
        guard let data = Data(base64Encoded: base64), let player = try? AVAudioPlayer(data: data) else {
            onEvent?("done", id)
            return
        }
        self.player = player
        currentID = id
        player.delegate = self
        player.isMeteringEnabled = true
        player.volume = Float(volume)
        player.prepareToPlay()
        player.play()
        onEvent?("playing", id)
    }

    func stop() {
        player?.stop()
        player = nil
        currentID = nil
    }

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        DispatchQueue.main.async {
            guard player === self.player, let id = self.currentID else { return }
            self.stop()
            self.onEvent?("done", id)
        }
    }
}
