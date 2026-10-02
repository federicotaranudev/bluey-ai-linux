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
    @State private var holdStart: DispatchWorkItem?
    @State private var holdingToAsk = false

    var body: some View {
        ZStack {
            FaceView(animator: animator)
                .contentShape(Rectangle())
                .onTapGesture(count: 2) { live.toggle() }  // double tap: wake up / back to follow mode
                .simultaneousGesture(holdToAsk)

            ModeIndicator(state: live.state)

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

    /// Press and hold the screen to ask him something; let go and he answers.
    private var holdToAsk: some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { _ in
                guard holdStart == nil, !holdingToAsk else { return }
                let work = DispatchWorkItem {
                    holdingToAsk = true
                    #if canImport(UIKit)
                    UIImpactFeedbackGenerator(style: .soft).impactOccurred()
                    #endif
                    live.beginAsk()
                }
                holdStart = work
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3, execute: work)  // a quick tap isn't a hold
            }
            .onEnded { _ in
                holdStart?.cancel()
                holdStart = nil
                if holdingToAsk {
                    holdingToAsk = false
                    #if canImport(UIKit)
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                    #endif
                    live.endAsk()
                }
            }
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
                animator.awake = true
                animator.localMood = nil
                link.send(Packet(command: "awake"))
            case .asking:
                animator.awake = true
                animator.localMood = .listening  // all ears while you hold
                link.send(Packet(command: "awake"))
            case .thinking:
                animator.localMood = .thinking
            case .speaking:
                animator.localMood = .talking
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

/// Shows which mode he's in: a colored glow around the screen edge plus a little label in the corner.
struct ModeIndicator: View {
    let state: LiveVoice.State

    private struct Look {
        let color: Color
        let label: String
        let icon: String
        let glow: Double      // how strong the edge glow is, 0 = none
        let pulse: Double     // pulses per second
    }

    private var look: Look {
        switch state {
        case .asleep:    return Look(color: Color(hex: Palette.inkSoft), label: "Following your mouse", icon: "eye", glow: 0, pulse: 0)
        case .waking:    return Look(color: Color(hex: 0xFFD66B), label: "Waking up", icon: "sun.max.fill", glow: 0.55, pulse: 1.6)
        case .listening: return Look(color: Color(hex: 0x5BE49B), label: "Listening · hold to ask", icon: "ear", glow: 0.45, pulse: 0.5)
        case .asking:    return Look(color: Color(hex: Palette.berry1), label: "I'm all ears", icon: "mic.fill", glow: 1, pulse: 1.4)
        case .thinking:  return Look(color: Color(hex: 0xC79BFF), label: "Thinking", icon: "sparkles", glow: 0.75, pulse: 1.1)
        case .speaking:  return Look(color: Color(hex: 0xFF9AD0), label: "Replying", icon: "bubble.left.fill", glow: 0.7, pulse: 0.8)
        }
    }

    var body: some View {
        let look = look
        TimelineView(.animation) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            let wave = look.pulse > 0 ? 0.5 + 0.5 * sin(t * look.pulse * 2 * .pi) : 1
            ZStack(alignment: .topLeading) {
                if look.glow > 0 {
                    RoundedRectangle(cornerRadius: 46, style: .continuous)
                        .strokeBorder(look.color, lineWidth: 10 + 8 * look.glow)
                        .blur(radius: 16)
                        .opacity(look.glow * (0.55 + 0.45 * wave))
                    RoundedRectangle(cornerRadius: 46, style: .continuous)
                        .strokeBorder(look.color.opacity(0.9), lineWidth: 2.5)
                        .opacity(look.glow * (0.4 + 0.6 * wave))
                }
                HStack(spacing: 8) {
                    Circle()
                        .fill(look.color)
                        .frame(width: 9, height: 9)
                        .opacity(look.pulse > 0 ? 0.45 + 0.55 * wave : 0.8)
                    Image(systemName: look.icon)
                        .font(.system(size: 14, weight: .bold))
                    Text(look.label)
                        .font(.fredoka(16))
                }
                .foregroundStyle(look.color)
                .padding(.horizontal, 14)
                .frame(height: 34)
                .background(Capsule().fill(look.color.opacity(state == .asleep ? 0.08 : 0.16)))
                .overlay(Capsule().strokeBorder(look.color.opacity(state == .asleep ? 0.2 : 0.45), lineWidth: 1.5))
                .opacity(state == .asleep ? 0.6 : 1)
                .padding(.top, 14)
                .padding(.leading, 22)
            }
        }
        .ignoresSafeArea()
        .allowsHitTesting(false)
        .animation(.easeInOut(duration: 0.3), value: state)
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
