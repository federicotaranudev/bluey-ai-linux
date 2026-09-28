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
    @Environment(\.scenePhase) private var scenePhase
    @State private var animator = FaceAnimator()
    @State private var showPairing = true

    var body: some View {
        ZStack {
            FaceView(animator: animator)
                .contentShape(Rectangle())
                .gesture(lookAtFinger)
                .onTapGesture(count: 2) { cycleLocalMood() }

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

    /// Double tap cycles moods locally, so you can try each one on camera.
    private func cycleLocalMood() {
        let order: [Mood?] = [nil, .happy, .thinking, .talking, .resting]
        let index = order.firstIndex(where: { $0 == animator.localMood }) ?? 0
        animator.localMood = order[(index + 1) % order.count]
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
