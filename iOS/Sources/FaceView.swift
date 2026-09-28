import SwiftUI

/// Smooths what the Mac asks for into lifelike motion: springy eyes, blinks, breathing, idle wandering.
final class FaceAnimator {
    struct Frame {
        var gaze: CGPoint
        var mood: Mood
        var talk: Double
        var closed: Double   // 0 open … 1 shut (blink)
        var breathe: Double
        var time: Double
    }

    private var target = FaceState()
    private var lastPacket = -100.0
    private var gaze = CGPoint.zero
    private var gazeVelocity = CGVector.zero
    private var talk = 0.0
    private var mood: Mood = .listening
    private var blinkStart = -1.0
    private var nextBlink = 2.0
    private var lastTime: Double?
    private var nextSaccade = 0.0
    private var wanderTarget = CGPoint(x: 0, y: -0.3)

    /// Set while a finger is on the screen, in -1…1 gaze units.
    var touchGaze: CGPoint?
    /// Mood picked on the phone itself (for testing without the Mac).
    var localMood: Mood?

    func receive(_ face: FaceState, at time: Double) {
        target = face
        lastPacket = time
    }

    func step(_ now: Double) -> Frame {
        let dt = min(now - (lastTime ?? now), 1.0 / 20)
        lastTime = now

        let live = now - lastPacket < 2.5
        let wantedMood = localMood ?? (live ? target.mood : .listening)
        if wantedMood != mood {
            mood = wantedMood
            blinkStart = now  // blink through every mood change
        }

        // Where to look.
        var want: CGPoint
        if let touchGaze {
            want = touchGaze
        } else if live {
            want = CGPoint(x: target.gazeX, y: target.gazeY)
        } else {
            if now > nextSaccade {
                wanderTarget = CGPoint(x: .random(in: -0.8...0.8), y: .random(in: -0.8...0.3))
                nextSaccade = now + .random(in: 0.8...2.6)
            }
            want = wanderTarget
        }
        if mood == .thinking { want = CGPoint(x: 0.55, y: -0.8) }

        // Fast, slightly bouncy spring so the eyes dart like real ones.
        let k = 320.0, c = 2 * sqrt(k) * 0.75
        gazeVelocity.dx += (k * (want.x - gaze.x) - c * gazeVelocity.dx) * dt
        gazeVelocity.dy += (k * (want.y - gaze.y) - c * gazeVelocity.dy) * dt
        gaze.x += gazeVelocity.dx * dt
        gaze.y += gazeVelocity.dy * dt

        let wantTalk = live ? target.talk : (localMood == .talking ? 0.5 + 0.5 * sin(now * 19) * sin(now * 7.3) : 0)
        talk += (wantTalk - talk) * min(1, dt * 25)

        if now > nextBlink {
            blinkStart = now
            nextBlink = now + .random(in: 2.2...5.5)
        }
        let p = (now - blinkStart) / 0.16
        let closed = (0...1).contains(p) ? sin(.pi * p) : 0

        let breathe = sin(now * (mood == .resting ? 1.1 : 1.8))
        return Frame(gaze: gaze, mood: mood, talk: talk, closed: closed, breathe: breathe, time: now)
    }
}

/// The landscape face on pure black: he fills the screen and peeks up from the bottom edge.
struct FaceView: View {
    let animator: FaceAnimator

    // The design is drawn on an 844 × 390 landscape phone.
    static let design = CGSize(width: 844, height: 390)

    var body: some View {
        TimelineView(.animation) { timeline in
            Canvas { ctx, size in
                let frame = animator.step(timeline.date.timeIntervalSinceReferenceDate)
                FaceView.draw(frame, in: &ctx, size: size)
            }
        }
        .background(Color.black)
        .ignoresSafeArea()
    }

    static func draw(_ f: FaceAnimator.Frame, in ctx: inout GraphicsContext, size: CGSize) {
        ctx.fill(Path(CGRect(origin: .zero, size: size)), with: .color(.black))

        let scale = max(size.width / design.width, size.height / design.height)
        ctx.translateBy(x: (size.width - design.width * scale) / 2, y: size.height - design.height * scale)
        ctx.scaleBy(x: scale, y: scale)

        let bounce = -f.talk * 16 + f.breathe * 3
        let body = CGRect(x: 12, y: 44 + bounce, width: 820, height: 700)
        let blob = BlobShape.path(in: body)

        var glow = ctx
        glow.addFilter(.shadow(color: Color(hex: Palette.berry2, opacity: 0.5), radius: 45, x: 0, y: -10))
        glow.fill(blob, with: .color(Color(hex: Palette.berry3)))

        ctx.fill(blob, with: BlobShape.fill(in: body))
        var shine = ctx
        shine.clip(to: blob)
        shine.fill(blob, with: .radialGradient(Gradient(colors: [.white.opacity(0.5), .white.opacity(0)]),
                                               center: CGPoint(x: body.minX + 246, y: body.minY + 126),
                                               startRadius: 0, endRadius: 243))

        let navy = Color(hex: Palette.nose)
        let ink = Color(hex: Palette.ink)
        ctx.fill(Crown.path(in: CGRect(x: body.minX + 368, y: body.minY - 20, width: 84, height: 56)), with: .color(navy))

        let eyeY = body.minY + 157
        let eyes = [CGPoint(x: body.minX + 280, y: eyeY), CGPoint(x: body.minX + 540, y: eyeY)]
        let squash = 1 - f.talk * 0.14

        for c in eyes {
            switch f.mood {
            case .resting:
                let lid = CGRect(x: c.x - 62, y: c.y + 8, width: 124, height: 18)
                ctx.fill(Path(roundedRect: lid, cornerRadius: 9), with: .color(ink))

            case .happy:
                var arc = Path()
                arc.move(to: CGPoint(x: c.x - 58, y: c.y + 36))
                arc.addQuadCurve(to: CGPoint(x: c.x + 58, y: c.y + 36), control: CGPoint(x: c.x, y: c.y - 50))
                ctx.stroke(arc, with: .color(ink), style: StrokeStyle(lineWidth: 24, lineCap: .round))
                let cheek = CGRect(x: c.x - 34 + (c.x < body.midX ? -70 : 70), y: c.y + 92, width: 68, height: 32)
                ctx.fill(Path(ellipseIn: cheek), with: .color(.white.opacity(0.35)))

            default:
                let open = max(0.06, 1 - f.closed) * squash
                let white = CGRect(x: c.x - 95, y: c.y - 95 * open, width: 190, height: 190 * open)
                let whitePath = Path(ellipseIn: white)
                ctx.fill(whitePath, with: .color(.white))
                guard open > 0.2 else { continue }

                var g = f.gaze
                let len = hypot(g.x, g.y)
                if len > 1 { g = CGPoint(x: g.x / len, y: g.y / len) }
                let pupil = CGPoint(x: c.x + g.x * 44, y: c.y + g.y * 44 * open)
                var inside = ctx
                inside.clip(to: whitePath)
                inside.fill(Path(ellipseIn: CGRect(x: pupil.x - 46, y: pupil.y - 46, width: 92, height: 92)), with: .color(ink))
                inside.fill(Path(ellipseIn: CGRect(x: pupil.x - 26, y: pupil.y - 32, width: 26, height: 26)), with: .color(.white))
            }
        }

        if f.mood == .thinking {
            for i in 0..<3 {
                let wobble = sin(f.time * 3 + Double(i)) * 4
                let dot = CGRect(x: body.minX + 690 + CGFloat(i) * 26, y: body.minY + 40 - CGFloat(i) * 12 + wobble, width: 16, height: 16)
                ctx.fill(Path(ellipseIn: dot), with: .color(.white.opacity(1 - Double(i) * 0.3)))
            }
        }

        let nose = CGRect(x: body.minX + 380, y: body.minY + 272 - f.talk * 4, width: 60, height: 38)
        ctx.fill(Path(ellipseIn: nose), with: .color(navy))
    }
}
