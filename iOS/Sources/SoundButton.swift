import SwiftUI

/// A tiny, quiet speaker button in the top-right corner. Tap it for the voice volume and a test line.
struct SoundButton: View {
    @ObservedObject var link: MacLink
    @State private var open = false
    @State private var lastTouch = Date()

    var body: some View {
        VStack(alignment: .trailing, spacing: 10) {
            Button {
                withAnimation(.spring(duration: 0.3)) { open.toggle() }
                lastTouch = Date()
            } label: {
                Image(systemName: open ? "xmark" : speakerIcon)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white.opacity(open ? 0.9 : 0.35))
                    .frame(width: 44, height: 44)
                    .background(Circle().fill(Color.white.opacity(open ? 0.14 : 0.06)).frame(width: 34, height: 34))
            }
            .buttonStyle(.plain)
            .accessibilityLabel(open ? "Close sound settings" : "Sound settings")

            if open {
                panel.transition(.opacity.combined(with: .scale(scale: 0.9, anchor: .topTrailing)))
            }
        }
        .padding(.top, 10)
        .padding(.trailing, 14)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
        .task(id: lastTouch) {
            // Tuck the panel away after a few quiet seconds so it never ends up in a shot.
            try? await Task.sleep(for: .seconds(6))
            if !Task.isCancelled { withAnimation(.easeOut(duration: 0.25)) { open = false } }
        }
    }

    private var speakerIcon: String {
        link.volume < 0.01 ? "speaker.slash.fill" : link.volume < 0.5 ? "speaker.wave.1.fill" : "speaker.wave.2.fill"
    }

    private var panel: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Voice volume")
                    .font(.plexMono(12))
                    .textCase(.uppercase)
                    .foregroundStyle(Color(hex: Palette.inkSoft))
                Spacer()
                Text("\(Int((link.volume * 100).rounded()))%")
                    .font(.plexMono(13))
                    .foregroundStyle(.white)
            }
            Slider(value: Binding(get: { link.volume }, set: { link.setVolume($0); lastTouch = Date() }), in: 0...1)
                .tint(Color(hex: Palette.berry2))
            Button {
                link.testVoice()
                lastTouch = Date()
            } label: {
                Text("Test voice")
                    .font(.plexSans(15).weight(.semibold))
                    .foregroundStyle(.white)
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .background(BlobShape.linear, in: RoundedRectangle(cornerRadius: 12))
            }
            .buttonStyle(.plain)
            .disabled(!link.connected)
            .opacity(link.connected ? 1 : 0.4)
            if !link.connected {
                Text("Connect to your Mac to hear him.")
                    .font(.plexSans(12))
                    .foregroundStyle(Color(hex: Palette.inkSoft))
            }
        }
        .padding(16)
        .frame(width: 260)
        .background(Color(hex: Palette.panel).opacity(0.95), in: RoundedRectangle(cornerRadius: 18))
    }
}
