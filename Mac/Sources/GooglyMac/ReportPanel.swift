import AppKit
import GooglyShared
import SwiftUI

/// The research report card. It shows up while he researches, then previews the first few lines.
/// Click it to expand; it stays on screen until you press the minus button.
@MainActor
final class ReportPanel {
    private var panel: NSPanel?
    private let model = ReportModel()

    func showLoading(_ question: String) {
        model.question = question
        model.report = nil
        model.error = nil
        model.expanded = false
        show()
    }

    func show(_ report: ResearchReport) {
        model.report = report
        model.error = nil
        show()
    }

    func showError(_ message: String) {
        model.error = message
        show()
    }

    /// Saves a picture of the card (for checking its look) and calls back when done.
    func snapshot(expanded: Bool, to path: String) {
        if model.expanded != expanded { toggle() }
        guard let view = panel?.contentView else { return }
        view.layoutSubtreeIfNeeded()
        guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
    }

    func close() {
        panel?.orderOut(nil)
    }

    private func show() {
        if panel == nil { makePanel() }
        layout(animated: false)
        panel?.orderFrontRegardless()
    }

    private func makePanel() {
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 400, height: 200),
                            styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.level = NSWindow.Level(rawValue: NSWindow.Level.screenSaver.rawValue + 1)  // above his cursor overlay
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        panel.hidesOnDeactivate = false
        panel.isMovableByWindowBackground = true
        let host = NSHostingView(rootView: ReportCard(model: model,
                                                      onToggle: { [weak self] in self?.toggle() },
                                                      onClose: { [weak self] in self?.close() }))
        panel.contentView = host
        self.panel = panel
    }

    private func toggle() {
        model.expanded.toggle()
        layout(animated: true)
    }

    /// Sits in the top-right corner of the main screen, out of the way of what he points at.
    private func layout(animated: Bool) {
        guard let panel, let screen = NSScreen.screens.first else { return }
        let visible = screen.visibleFrame
        let width: CGFloat = model.expanded ? 520 : 400
        let height: CGFloat
        if model.expanded, let report = model.report {
            // Roughly fit the text (about 58 characters a line), up to most of the screen; it scrolls past that.
            let lines = report.paragraphs.reduce(0) { $0 + Int(ceil(Double($1.count) / 58)) }
            let content = CGFloat(lines) * 21 + CGFloat(report.paragraphs.count) * 12
                + (report.sources.isEmpty ? 0 : 30 + CGFloat(report.sources.count) * 22)
            height = min(visible.height * 0.75, 110 + content)
        } else {
            height = model.report == nil ? 140 : 190
        }
        let frame = NSRect(x: visible.maxX - width - 24, y: visible.maxY - height - 24, width: width, height: height)
        panel.setFrame(frame, display: true, animate: animated)
    }
}

final class ReportModel: ObservableObject {
    @Published var question = ""
    @Published var report: ResearchReport?
    @Published var error: String?
    @Published var expanded = false
}

private struct ReportCard: View {
    @ObservedObject var model: ReportModel
    var onToggle: () -> Void
    var onClose: () -> Void

    private let ink = Color(nsColor: NSColor(hex: Palette.ink))
    private let berry = Color(nsColor: NSColor(hex: Palette.berry2))
    private let soft = Color(nsColor: NSColor(hex: 0x5D5873))

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            if let report = model.report {
                if model.expanded {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 12) {
                            ForEach(Array(report.paragraphs.enumerated()), id: \.offset) { _, paragraph in
                                Text(paragraph)
                                    .font(.custom("IBM Plex Sans", size: 15))
                                    .foregroundStyle(ink)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            if !report.sources.isEmpty {
                                Text("SOURCES")
                                    .font(.custom("IBM Plex Mono", size: 11))
                                    .foregroundStyle(soft)
                                    .padding(.top, 4)
                                ForEach(Array(report.sources.enumerated()), id: \.offset) { _, source in
                                    Link(destination: source.url) {
                                        Text(source.title)
                                            .font(.custom("IBM Plex Sans", size: 13))
                                            .foregroundStyle(berry)
                                            .lineLimit(1)
                                    }
                                }
                            }
                        }
                        .padding(.trailing, 6)
                    }
                } else {
                    Text(report.paragraphs.joined(separator: " "))
                        .font(.custom("IBM Plex Sans", size: 14))
                        .foregroundStyle(ink)
                        .lineLimit(3)
                    Text("Click to read more")
                        .font(.custom("IBM Plex Mono", size: 11))
                        .foregroundStyle(berry)
                }
            } else if let error = model.error {
                Text(error)
                    .font(.custom("IBM Plex Sans", size: 14))
                    .foregroundStyle(ink)
            } else {
                HStack(spacing: 10) {
                    ProgressView().controlSize(.small)
                    Text("Doing some research on \u{201C}\(model.question)\u{201D}")
                        .font(.custom("IBM Plex Sans", size: 14))
                        .foregroundStyle(soft)
                        .lineLimit(2)
                }
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .fill(LinearGradient(colors: [.white, Color(nsColor: NSColor(hex: 0xEEF0FF))], startPoint: .top, endPoint: .bottom))
        )
        .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).strokeBorder(berry, lineWidth: 2.5))
        .padding(10)  // room for the glow
        .shadow(color: Color(nsColor: NSColor(hex: Palette.berry1)).opacity(0.5), radius: 14, y: 4)
        .contentShape(Rectangle())
        .onTapGesture { if model.report != nil, !model.expanded { onToggle() } }
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: model.report == nil && model.error == nil ? "magnifyingglass" : "doc.text.magnifyingglass")
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(berry)
                .padding(.top, 3)
            Text(model.report?.title ?? (model.error == nil ? "Researching" : "Research"))
                .font(.custom("Fredoka", size: 20).weight(.bold))
                .foregroundStyle(ink)
                .lineLimit(model.expanded ? 3 : 2)
            Spacer(minLength: 4)
            if model.expanded {
                circleButton("chevron.up", action: onToggle)  // back to the preview
            }
            circleButton("minus", action: onClose)
        }
    }

    private func circleButton(_ icon: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: icon)
                .font(.system(size: 12, weight: .heavy))
                .foregroundStyle(.white)
                .frame(width: 26, height: 26)
                .background(Circle().fill(berry))
        }
        .buttonStyle(.plain)
    }
}
