import AppKit
import GooglyShared

/// Where the big character cursor wants to be.
enum CursorMode: Equatable {
    /// Waiting at the bottom edge of the screen, right above the phone.
    case docked
    /// Hidden at home; the phone's eyes follow your own mouse instead.
    case following
    /// Flew to a spot and stays there while you move the mouse away.
    case pinned(CGPoint)
}

extension NSColor {
    convenience init(hex: UInt32, alpha: CGFloat = 1) {
        let c = Palette.rgb(hex)
        self.init(srgbRed: c.r, green: c.g, blue: c.b, alpha: alpha)
    }
}

/// A click-through, transparent window over the whole main screen that draws the cursor.
final class CursorOverlay {
    let view = CursorView()
    private var window: NSWindow?
    private var timer: Timer?
    private var lastTick = CACurrentMediaTime()

    /// Called every frame with the face the phone should show.
    var onFace: ((FaceState) -> Void)?

    var mode: CursorMode {
        get { view.engine.mode }
        set { view.engine.setMode(newValue) }
    }

    /// Where the cursor rests when nobody is pointing: hidden with eyes on your mouse, or parked above the phone.
    var idleMode: CursorMode { Settings.shared.followMouse ? .following : .docked }

    func goHome() { mode = idleMode }

    func start() {
        mode = idleMode
        makeWindow()
        NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification,
                                               object: nil, queue: .main) { [weak self] _ in
            self?.makeWindow()
        }
        let timer = Timer(timeInterval: 1.0 / 120.0, repeats: true) { [weak self] _ in self?.tick() }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    func talkTest(seconds: Double = 3) {
        view.engine.talkUntil = CACurrentMediaTime() + seconds
    }

    private func makeWindow() {
        guard let screen = NSScreen.screens.first else { return }
        if let window {
            window.setFrame(screen.frame, display: true)
            return
        }
        let window = NSWindow(contentRect: screen.frame, styleMask: .borderless, backing: .buffered, defer: false)
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = false
        window.ignoresMouseEvents = true
        window.level = .screenSaver
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        window.contentView = view
        window.orderFrontRegardless()
        self.window = window
    }

    private func tick() {
        guard let window else { return }
        let now = CACurrentMediaTime()
        let dt = min(now - lastTick, 1.0 / 20.0)
        lastTick = now

        // Mouse position in the view's top-left, y-down coordinates.
        let m = NSEvent.mouseLocation
        let f = window.frame
        let mouse = CGPoint(x: m.x - f.minX, y: f.maxY - m.y)

        view.step(dt: dt, now: now, mouse: mouse)
        onFace?(view.engine.face(in: view.bounds.size, now: now))
    }
}

/// The physics and state behind the cursor. Coordinates are top-left origin, y down.
final class CursorEngine {
    private(set) var mode: CursorMode = .docked
    var tip = CGPoint(x: -500, y: -500)
    private(set) var velocity = CGVector.zero
    var angle: CGFloat = .pi / 4
    private var angleVelocity: CGFloat = 0
    private var placed = false
    /// Fades the cursor out while the eyes are just following your mouse.
    var opacity: CGFloat = 0
    private var mouse = CGPoint.zero

    struct Dot { var point: CGPoint; var born: Double; var size: CGFloat; var color: UInt32 }
    struct Ring { var point: CGPoint; var born: Double }
    struct Sparkle { var point: CGPoint; var velocity: CGVector; var born: Double; var size: CGFloat; var spin: CGFloat }
    var sparkles: [Sparkle] = []
    /// When it last landed on a spot (for the little "click" squish).
    var landedAt = -10.0
    var trail: [Dot] = []
    var rings: [Ring] = []
    private var lastDot = 0.0
    private var ringPending = false

    var talkUntil = 0.0
    /// Set by the conductor while listening, thinking or talking.
    var brainMood: Mood?
    /// Where the phone's eyes should look instead of at the cursor (e.g. at you while listening).
    var gazeOverride: CGPoint?
    /// Live voice loudness, 0…1.
    var talkLevel: () -> Double = { 0 }
    var blinkUntil = 0.0
    private var nextBlink = CACurrentMediaTime() + 3

    let settings = Settings.shared
    var size: CGFloat { CGFloat(settings.cursorSize) }

    func setMode(_ newMode: CursorMode) {
        mode = newMode
        if case .pinned = newMode { ringPending = true }
    }

    /// Where the phone sits, just below the bottom edge of the screen.
    func phonePoint(in bounds: CGSize) -> CGPoint {
        CGPoint(x: bounds.width * settings.phonePosition, y: bounds.height + bounds.height * 0.18)
    }

    func dockPoint(in bounds: CGSize) -> CGPoint {
        CGPoint(x: bounds.width * settings.phonePosition, y: bounds.height - size * 0.42)
    }

    func step(dt: Double, now: Double, mouse: CGPoint, bounds: CGSize) {
        let target: CGPoint
        switch mode {
        case .docked: target = dockPoint(in: bounds)
        case .following: target = dockPoint(in: bounds)
        case .pinned(let p): target = p
        }
        self.mouse = mouse
        if !placed {
            tip = dockPoint(in: bounds)
            placed = true
        }

        // A slightly bouncy spring: quick, with a small overshoot when it lands.
        let k: CGFloat = 150
        let damping: CGFloat = 2 * sqrt(k) * 0.72
        let t = CGFloat(dt)
        velocity.dx += (k * (target.x - tip.x) - damping * velocity.dx) * t
        velocity.dy += (k * (target.y - tip.y) - damping * velocity.dy) * t
        tip.x += velocity.dx * t
        tip.y += velocity.dy * t

        // Visible whenever it's pointing or parked; in follow mode it fades out once it's home.
        let nearHome = hypot(tip.x - target.x, tip.y - target.y) < 12
        let wantOpacity: CGFloat = (mode == .following && nearHome) ? 0 : 1
        opacity += (wantOpacity - opacity) * min(1, t * (wantOpacity > opacity ? 14 : 6))

        // The tip points away from the phone, so it always reads as the character pointing.
        let phone = phonePoint(in: bounds)
        let away = CGVector(dx: tip.x - phone.x, dy: tip.y - phone.y)
        let home: Bool
        switch mode { case .docked, .following: home = true; case .pinned: home = false }
        let targetAngle = home ? .pi / 4 : atan2(-away.dy, -away.dx) - .pi / 4
        let ka: CGFloat = 120
        angleVelocity += (ka * (targetAngle - angle) - 2 * sqrt(ka) * 0.9 * angleVelocity) * t
        angle += angleVelocity * t

        // Bubble trail while flying fast.
        let speed = hypot(velocity.dx, velocity.dy)
        if speed > 700, now - lastDot > 0.035 {
            lastDot = now
            let colors = [Palette.berry1, Palette.berry2, Palette.berry3]
            trail.append(Dot(point: bodyCenter, born: now, size: size * CGFloat.random(in: 0.12...0.2),
                             color: colors.randomElement()!))
        }
        trail.removeAll { now - $0.born > 0.45 }

        // A soft ring when it lands on a pinned spot.
        if ringPending, speed < 60, hypot(target.x - tip.x, target.y - tip.y) < 4 {
            ringPending = false
            rings.append(Ring(point: tip, born: now))
            landedAt = now
            // A burst of tiny stars, like he just clicked on it.
            for i in 0..<7 {
                let a = Double(i) / 7 * 2 * .pi + .random(in: -0.3...0.3)
                let speed = CGFloat.random(in: 140...260)
                sparkles.append(Sparkle(point: tip, velocity: CGVector(dx: cos(a) * speed, dy: sin(a) * speed),
                                        born: now, size: .random(in: 7...13), spin: .random(in: -4...4)))
            }
        }
        rings.removeAll { now - $0.born > 0.9 }
        for i in sparkles.indices {
            sparkles[i].point.x += sparkles[i].velocity.dx * t
            sparkles[i].point.y += sparkles[i].velocity.dy * t
            sparkles[i].velocity.dx *= 0.9
            sparkles[i].velocity.dy = sparkles[i].velocity.dy * 0.9 + 200 * t
        }
        sparkles.removeAll { now - $0.born > 0.7 }

        if now > nextBlink {
            blinkUntil = now + 0.12
            nextBlink = now + Double.random(in: 2.5...6)
        }
    }

    var bodyCenter: CGPoint {
        let r = size * 0.5 * sqrt(2)
        return CGPoint(x: tip.x + cos(angle + .pi / 4) * r, y: tip.y + sin(angle + .pi / 4) * r)
    }

    /// Eyes on the phone look at the cursor.
    func face(in bounds: CGSize, now: Double) -> FaceState {
        let phone = phonePoint(in: bounds)
        let c = mode == .following ? mouse : bodyCenter
        var gx = max(-1, min(1, (c.x - phone.x) / (bounds.width * 0.5)))
        var gy = -max(0.15, min(1, (phone.y - c.y) / (bounds.height * 1.1)))
        if let g = gazeOverride { gx = g.x; gy = g.y }

        let testing = now < talkUntil
        let talk = testing ? 0.5 + 0.5 * sin(now * 19) * sin(now * 7.3) : talkLevel()
        var mood = settings.mood
        if let brainMood {
            mood = brainMood
        } else if testing {
            mood = .talking
        } else if case .pinned = mode, mood == .listening {
            mood = .pointing
        }
        return FaceState(gazeX: gx, gazeY: gy, mood: mood, talk: talk)
    }
}

final class CursorView: NSView {
    let engine = CursorEngine()
    private var lastDirty = CGRect.null
    private var captionText: NSAttributedString?
    private var captionRect = CGRect.null

    /// Live caption shown near the bottom of the screen while he talks.
    var caption: String? {
        didSet {
            guard caption != oldValue else { return }
            setNeedsDisplay(captionRect.insetBy(dx: -4, dy: -4))
            layoutCaption()
            setNeedsDisplay(captionRect.insetBy(dx: -4, dy: -4))
        }
    }

    private func layoutCaption() {
        guard let caption, !caption.isEmpty else {
            captionText = nil
            captionRect = .null
            return
        }
        let style = NSMutableParagraphStyle()
        style.alignment = .center
        style.lineSpacing = 4
        let text = NSAttributedString(string: caption, attributes: [
            .font: Fonts.display(30),
            .foregroundColor: NSColor.white,
            .paragraphStyle: style,
        ])
        let maxWidth = min(bounds.width - 80, 960)
        let size = text.boundingRect(with: CGSize(width: maxWidth, height: 400),
                                     options: [.usesLineFragmentOrigin, .usesFontLeading]).size
        let pad = CGSize(width: 28, height: 18)
        let box = CGSize(width: ceil(size.width) + pad.width * 2, height: ceil(size.height) + pad.height * 2)
        let bottom = bounds.height - engine.size * 1.4 - 24
        captionText = text
        captionRect = CGRect(x: (bounds.width - box.width) / 2, y: bottom - box.height, width: box.width, height: box.height)
    }

    override var isFlipped: Bool { true }
    override var isOpaque: Bool { false }

    func step(dt: Double, now: Double, mouse: CGPoint) {
        engine.step(dt: dt, now: now, mouse: mouse, bounds: bounds.size)
        let dirty = contentRect(now: now)
        setNeedsDisplay(dirty.union(lastDirty))
        lastDirty = dirty
    }

    /// Everything drawn this frame, padded for the glow.
    private func contentRect(now: Double) -> CGRect {
        let s = engine.size
        var r = CGRect(x: engine.tip.x - s * 1.6, y: engine.tip.y - s * 1.6, width: s * 3.2, height: s * 3.2)
        for dot in engine.trail { r = r.union(CGRect(x: dot.point.x - 20, y: dot.point.y - 20, width: 40, height: 40)) }
        for ring in engine.rings { r = r.union(CGRect(x: ring.point.x - 90, y: ring.point.y - 90, width: 180, height: 180)) }
        for sparkle in engine.sparkles { r = r.union(CGRect(x: sparkle.point.x - 16, y: sparkle.point.y - 16, width: 32, height: 32)) }
        if let tether = tetherPoints() {
            for p in [tether.from, tether.control, tether.to] { r = r.union(CGRect(x: p.x - 8, y: p.y - 8, width: 16, height: 16)) }
        }
        return r.insetBy(dx: -40, dy: -40)
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let ctx = NSGraphicsContext.current?.cgContext else { return }
        ctx.clear(dirtyRect)
        guard Settings.shared.showCursor else { return }
        let now = CACurrentMediaTime()

        // A dotted "remote control" line from the phone to the cursor while he's driving it.
        if let tether = tetherPoints() {
            let path = CGMutablePath()
            path.move(to: tether.from)
            path.addQuadCurve(to: tether.to, control: tether.control)
            ctx.saveGState()
            ctx.addPath(path)
            ctx.setLineCap(.round)
            ctx.setLineWidth(5)
            ctx.setLineDash(phase: CGFloat(-now * 60), lengths: [0.1, 16])
            ctx.setStrokeColor(NSColor(hex: Palette.berry1, alpha: 0.55 * engine.opacity).cgColor)
            ctx.strokePath()
            ctx.restoreGState()
        }

        for sparkle in engine.sparkles {
            let p = CGFloat((now - sparkle.born) / 0.7)
            drawStar(ctx, at: sparkle.point, size: sparkle.size * (1 - p * 0.6), angle: sparkle.spin * CGFloat(now - sparkle.born),
                     color: NSColor(hex: p < 0.4 ? Palette.berry1 : Palette.berry2, alpha: 1 - p))
        }

        // A dotted "remote control" line from the phone to the cursor while he's driving it.
        if let tether = tetherPoints() {
            let path = CGMutablePath()
            path.move(to: tether.from)
            path.addQuadCurve(to: tether.to, control: tether.control)
            ctx.saveGState()
            ctx.addPath(path)
            ctx.setLineCap(.round)
            ctx.setLineWidth(5)
            ctx.setLineDash(phase: CGFloat(-now * 60), lengths: [0.1, 16])
            ctx.setStrokeColor(NSColor(hex: Palette.berry1, alpha: 0.55 * engine.opacity).cgColor)
            ctx.strokePath()
            ctx.restoreGState()
        }

        for sparkle in engine.sparkles {
            let p = CGFloat((now - sparkle.born) / 0.7)
            drawStar(ctx, at: sparkle.point, size: sparkle.size * (1 - p * 0.6), angle: sparkle.spin * CGFloat(now - sparkle.born),
                     color: NSColor(hex: p < 0.4 ? Palette.berry1 : Palette.berry2, alpha: 1 - p))
        }

        for ring in engine.rings {
            let p = CGFloat((now - ring.born) / 0.9)
            let radius = 16 + 64 * (1 - pow(1 - p, 3))
            ctx.setStrokeColor(NSColor(hex: Palette.berry2, alpha: 0.75 * (1 - p)).cgColor)
            ctx.setLineWidth(5)
            ctx.strokeEllipse(in: CGRect(x: ring.point.x - radius, y: ring.point.y - radius, width: radius * 2, height: radius * 2))
        }

        for dot in engine.trail {
            let p = CGFloat((now - dot.born) / 0.45)
            let s = dot.size * (1 - p * 0.5)
            ctx.setFillColor(NSColor(hex: dot.color, alpha: 0.7 * (1 - p)).cgColor)
            ctx.fillEllipse(in: CGRect(x: dot.point.x - s / 2, y: dot.point.y - s / 2, width: s, height: s))
        }

        if let captionText, captionRect.intersects(dirtyRect) {
            let panel = NSBezierPath(roundedRect: captionRect, xRadius: 22, yRadius: 22)
            NSColor(hex: Palette.ink, alpha: 0.86).setFill()
            panel.fill()
            captionText.draw(with: captionRect.insetBy(dx: 28, dy: 18), options: [.usesLineFragmentOrigin, .usesFontLeading])
        }

        drawCursor(ctx, now: now)
    }

    /// The blob teardrop from the design: tip in the top-left corner, eyes looking at the tip.
    private func drawCursor(_ ctx: CGContext, now: Double) {
        let s = engine.size
        let k = s / 96  // the design draws it in a 96 pt box
        ctx.saveGState()
        // Hover gently while pointing, squish like a click when it lands, stretch along its flight.
        var bob: CGFloat = 0
        if case .pinned = engine.mode, now - engine.landedAt > 0.4 { bob = CGFloat(sin(now * 3.2)) * 2.5 }
        ctx.translateBy(x: engine.tip.x, y: engine.tip.y + bob)
        let since = now - engine.landedAt
        if since < 0.45 {
            let press = CGFloat(sin(since / 0.45 * .pi * 2) * exp(-since * 5)) * 0.16
            ctx.scaleBy(x: 1 + press, y: 1 - press)
        }
        let v = engine.velocity
        let speed = hypot(v.dx, v.dy)
        if speed > 60 {
            let dir = atan2(v.dy, v.dx)
            let amount = min(0.28, speed / 5000)
            ctx.rotate(by: dir)
            ctx.scaleBy(x: 1 + amount, y: 1 - amount * 0.6)
            ctx.rotate(by: -dir)
        }
        ctx.rotate(by: engine.angle)

        guard engine.opacity > 0.01 else { ctx.restoreGState(); return }
        ctx.setAlpha(engine.opacity)
        let path = teardrop(size: s, tipRadius: 6 * k)

        // Shadow or glow under the body.
        ctx.saveGState()
        if Settings.shared.glow {
            ctx.setShadow(offset: .zero, blur: 40 * k, color: NSColor(hex: Palette.berry2, alpha: 0.6).cgColor)
        } else {
            ctx.setShadow(offset: CGSize(width: 0, height: -14 * k), blur: 30 * k, color: NSColor(hex: Palette.berry3, alpha: 0.6).cgColor)
        }
        ctx.addPath(path)
        ctx.setFillColor(NSColor.white.cgColor)
        ctx.fillPath()
        ctx.restoreGState()

        // Gradient body.
        ctx.saveGState()
        ctx.addPath(path)
        ctx.clip()
        let space = CGColorSpace(name: CGColorSpace.sRGB)!
        let colors = Palette.gradient.map { NSColor(hex: $0).cgColor } as CFArray
        let stops = Palette.gradientStops.map { CGFloat($0) }
        if let gradient = CGGradient(colorsSpace: space, colors: colors, locations: stops) {
            ctx.drawLinearGradient(gradient, start: CGPoint(x: 0.25 * s, y: 0.067 * s),
                                   end: CGPoint(x: 0.75 * s, y: 0.933 * s), options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
        }
        let shine = [NSColor(white: 1, alpha: 0.5).cgColor, NSColor(white: 1, alpha: 0).cgColor] as CFArray
        if let gradient = CGGradient(colorsSpace: space, colors: shine, locations: [0, 1]) {
            let c = CGPoint(x: 0.3 * s, y: 0.22 * s)
            ctx.drawRadialGradient(gradient, startCenter: c, startRadius: 0, endCenter: c, endRadius: 0.36 * s, options: [])
        }
        // White rim (half the stroke is clipped away, leaving 4 pt inside).
        ctx.addPath(path)
        ctx.setStrokeColor(NSColor.white.cgColor)
        ctx.setLineWidth(8 * k)
        ctx.strokePath()
        ctx.restoreGState()

        // Two little eyes, pupils toward the tip. They blink now and then.
        let blinking = now < engine.blinkUntil
        // Pupils look toward the tip at rest, and ahead in the direction of flight while moving.
        var look = CGPoint(x: -1, y: -1)
        if speed > 120 {
            let local = CGPoint(x: v.dx * cos(-engine.angle) - v.dy * sin(-engine.angle),
                                y: v.dx * sin(-engine.angle) + v.dy * cos(-engine.angle))
            let n = max(hypot(local.x, local.y), 1)
            look = CGPoint(x: local.x / n * 1.4, y: local.y / n * 1.4)
        }
        let eye = 18 * k
        let pupil = 9 * k
        for i in 0..<2 {
            let x = (38 + CGFloat(i) * 24) * k
            let y = 42 * k
            let white = CGRect(x: x, y: y, width: eye, height: eye)
            ctx.setFillColor(NSColor.white.cgColor)
            if blinking {
                ctx.fill(CGRect(x: x, y: y + eye / 2 - 1.5 * k, width: eye, height: 3 * k))
            } else {
                ctx.fillEllipse(in: white)
                ctx.setFillColor(NSColor(hex: Palette.ink).cgColor)
                let px = x + (eye - pupil) / 2 + look.x * 3.2 * k
                let py = y + (eye - pupil) / 2 + look.y * 3.2 * k
                ctx.fillEllipse(in: CGRect(x: px, y: py, width: pupil, height: pupil))
            }
        }
        ctx.restoreGState()
    }

    /// From the phone's spot at the bottom of the screen to the cursor, bowed a little like a string.
    private func tetherPoints() -> (from: CGPoint, control: CGPoint, to: CGPoint)? {
        guard case .pinned = engine.mode, engine.opacity > 0.05 else { return nil }
        let from = CGPoint(x: bounds.width * Settings.shared.phonePosition, y: bounds.height + 4)
        let to = engine.bodyCenter
        let mid = CGPoint(x: (from.x + to.x) / 2, y: (from.y + to.y) / 2)
        let sag = CGFloat(sin(CACurrentMediaTime() * 1.7)) * 18
        let control = CGPoint(x: mid.x + (to.y - from.y) * 0.18 + sag, y: mid.y - (to.x - from.x) * 0.08)
        return (from, control, to)
    }

    private func drawStar(_ ctx: CGContext, at p: CGPoint, size: CGFloat, angle: CGFloat, color: NSColor) {
        let path = CGMutablePath()
        for i in 0..<8 {
            let r = i % 2 == 0 ? size : size * 0.38
            let a = angle + CGFloat(i) * .pi / 4
            let pt = CGPoint(x: p.x + cos(a) * r, y: p.y + sin(a) * r)
            i == 0 ? path.move(to: pt) : path.addLine(to: pt)
        }
        path.closeSubpath()
        ctx.addPath(path)
        ctx.setFillColor(color.cgColor)
        ctx.fillPath()
    }

    /// A square with three fully rounded corners and one sharp one at the origin.
    private func teardrop(size s: CGFloat, tipRadius r: CGFloat) -> CGPath {
        let path = CGMutablePath()
        let big = s / 2
        path.move(to: CGPoint(x: r, y: 0))
        path.addArc(tangent1End: CGPoint(x: s, y: 0), tangent2End: CGPoint(x: s, y: s), radius: big)
        path.addArc(tangent1End: CGPoint(x: s, y: s), tangent2End: CGPoint(x: 0, y: s), radius: big)
        path.addArc(tangent1End: CGPoint(x: 0, y: s), tangent2End: CGPoint(x: 0, y: 0), radius: big)
        path.addArc(tangent1End: CGPoint(x: 0, y: 0), tangent2End: CGPoint(x: s, y: 0), radius: r)
        path.closeSubpath()
        return path
    }
}
