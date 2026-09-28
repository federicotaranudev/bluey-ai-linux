import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

@main
struct GooglyApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}

struct RootView: View {
    @StateObject private var link = MacLink()
    @StateObject private var live = LiveVoice()
    @Environment(\.scenePhase) private var scenePhase
    @State private var animator = FaceAnimator()
    @State private var showPairing = true

    var body: some View {
        ZStack {
            FaceView(animator: animator)
                .contentShape(Rectangle())
                .onTapGesture(count: 2) { live.toggle() }  // double tap: wake up / back to follow mode
                .simultaneousGesture(lookAtFinger)

            SoundButton(link: link, live: live)

            if showPairing && !link.connected {
                PairingView { withAnimation(.easeOut(duration: 0.3)) { showPairing = false } }
                    .transition(.opacity)
            }
        }
        .background(Color.black)
        .ignoresSafeArea()
        .phoneChrome()
        .onAppear {
            link.onFace = { [animator] face in animator.receive(face, at: Date().timeIntervalSinceReferenceDate) }
            animator.localTalk = { [live] in live.level }
            wireLiveVoice()
            link.start()
        }
        .onChange(of: link.connected) { _, connected in
            if connected { withAnimation(.easeOut(duration: 0.4)) { showPairing = false } }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { link.start() } else if phase == .background { link.stop() }
        }
    }

    /// Drag a finger to make him look at it, handy for testing without the Mac.
    private var lookAtFinger: some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                #if canImport(UIKit)
                let bounds = UIScreen.main.bounds.size
                #else
                let bounds = CGSize(width: 844, height: 390)
                #endif
                animator.touchGaze = CGPoint(x: (value.location.x / bounds.width) * 2 - 1,
                                             y: (value.location.y / bounds.height) * 2 - 1.2)
            }
            .onEnded { _ in animator.touchGaze = nil }
    }

    /// Connects the live voice to the Mac: keys, tools, captions and wake/sleep.
    private func wireLiveVoice() {
        live.requestToken = { [link] done in
            link.request(Packet(command: "realtimeToken")) { reply in done(reply?.text) }
        }
        live.runTool = { [link] name, arguments, done in
            link.request(Packet(command: "tool", tool: name, text: arguments)) { reply in
                done(reply?.text ?? "The Mac didn't answer.", reply?.image)
            }
        }
        live.onCaption = { [link] text, finished in
            link.send(Packet(command: finished ? "captionDone" : "caption", text: text))
        }
        live.onStateChange = { [link, animator] state in
            switch state {
            case .asleep:
                animator.awake = false
                animator.localMood = nil
                link.send(Packet(command: "asleep"))
            case .waking:
                animator.awake = true
                animator.localMood = .happy
            case .listening:
                animator.localMood = nil
                link.send(Packet(command: "awake"))
                link.send(Packet(command: "quiet"))
            case .speaking:
                animator.localMood = .talking
                link.send(Packet(command: "speaking"))
            }
        }
        link.onCommand = { [live] command in
            switch command {
            case "wake": live.wake()
            case "sleep": live.sleep()
            default: break
            }
        }
    }
}

private extension View {
    /// Hides the status bar and home indicator and keeps the screen awake.
    func phoneChrome() -> some View {
        #if os(iOS)
        return self
            .statusBarHidden(true)
            .persistentSystemOverlays(.hidden)
            .onAppear { UIApplication.shared.isIdleTimerDisabled = true }
        #else
        return self
        #endif
    }
}
