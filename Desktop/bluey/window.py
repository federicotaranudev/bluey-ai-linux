"""The desktop's setup window stays available even without a system tray."""
from __future__ import annotations

import html
import platform

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QPushButton, QScrollArea, QSlider,
    QTabWidget, QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)

from .settings import MOODS, Preferences
from .visuals import BerryPreview, berry_icon

STYLE = """
QWidget { background: #17151F; color: #F3F1FA; font-family: 'IBM Plex Sans'; font-size: 14px; }
QMainWindow { background: #17151F; }
QLabel { background: transparent; }
QLabel#title { font-family: 'Fredoka'; font-size: 38px; font-weight: 600; }
QLabel#heading { font-family: 'Fredoka'; font-size: 24px; }
QLabel#subtle { color: #B9B2CC; }
QLabel#badge { color: #A9BCFF; background: #29273F; border-radius: 12px; padding: 7px 13px; }
QLabel#notice { color: #F9DAA5; background: #302A28; border-radius: 10px; padding: 12px; }
QFrame#panel { background: #211E30; border: 1px solid #353047; border-radius: 18px; }
QPushButton { background: #302B43; border: 1px solid #48405F; border-radius: 10px; padding: 10px 18px; font-weight: 600; }
QPushButton:hover { background: #413956; border-color: #8A82B0; }
QPushButton:pressed { background: #4E4269; }
QPushButton:focus { border: 2px solid #A9BCFF; }
QPushButton#primary { background: #A9BCFF; border-color: #A9BCFF; color: #17151F; }
QPushButton#primary:hover { background: #C3D0FF; }
QPushButton:disabled { background: #302B43; color: #938AA6; border-color: #48405F; }
QPushButton#primary:disabled { background: #302B43; color: #938AA6; border-color: #48405F; }
QPushButton#stop { color: #FFB4BE; border-color: #815361; }
QLineEdit, QTextEdit, QTextBrowser, QComboBox { background: #211E30; border: 1px solid #48405F; border-radius: 8px; padding: 9px; selection-background-color: #4254D6; }
QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border-color: #A9BCFF; }
QComboBox QAbstractItemView { background: #211E30; selection-background-color: #4254D6; }
QCheckBox { spacing: 10px; padding: 5px 0; }
QCheckBox::indicator { width: 19px; height: 19px; border-radius: 5px; border: 1px solid #756B90; background: #211E30; }
QCheckBox::indicator:checked { background: #A9BCFF; border: 4px solid #4254D6; }
QSlider::groove:horizontal { height: 5px; background: #48405F; border-radius: 2px; }
QSlider::handle:horizontal { background: #A9BCFF; width: 17px; margin: -6px 0; border-radius: 8px; }
QTabWidget::pane { border: 0; }
QTabBar::tab { color: #B9B2CC; padding: 12px 22px; border-bottom: 2px solid #353047; }
QTabBar::tab:selected { color: #A9BCFF; border-bottom: 2px solid #A9BCFF; }
QScrollArea { border: 0; }
QToolTip { background: #302B43; color: white; border: 1px solid #A9BCFF; }
"""


def label(text, name=None, wrap=False):
    item = QLabel(text)
    if name:
        item.setObjectName(name)
    item.setWordWrap(wrap)
    return item


def button(text, callback, name=None):
    item = QPushButton(text)
    if name:
        item.setObjectName(name)
    item.clicked.connect(callback)
    return item


class MainWindow(QMainWindow):
    save_key = Signal(str)
    remove_key = Signal()
    preferences_changed = Signal()
    control_changed = Signal(bool)
    action = Signal(str)

    def __init__(self, prefs: Preferences):
        super().__init__()
        self.prefs = prefs
        self.has_tray = False
        self.setWindowTitle("Bluey · Desktop companion")
        self.setWindowIcon(berry_icon())
        self.setStyleSheet(STYLE)
        self.resize(860, 720)
        self.setMinimumSize(700, 620)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(32, 24, 32, 22)
        layout.setSpacing(16)
        header = QHBoxLayout()
        words = QVBoxLayout()
        words.addWidget(label("Bluey", "title"))
        words.addWidget(label("A little companion. A bigger world to point at.", "subtle"))
        header.addLayout(words)
        header.addStretch()
        badge = label("Windows" if platform.system() == "Windows" else "Linux", "badge")
        header.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)
        self.notice = label("", "notice", True)
        self.notice.hide()
        layout.addWidget(self.notice)
        tabs = QTabWidget()
        self.tabs = tabs
        layout.addWidget(tabs, 1)
        companion = QWidget()
        tabs.addTab(companion, "Companion")
        page = QVBoxLayout(companion)
        page.setContentsMargins(0, 20, 0, 0)
        page.setSpacing(16)
        hero = QHBoxLayout()
        hero.setSpacing(28)
        portrait = QVBoxLayout()
        portrait.addWidget(BerryPreview())
        portrait_text = label("Your phone brings him to life.\nThis computer gives him a cursor.", "subtle", True)
        portrait_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        portrait.addWidget(portrait_text)
        hero.addLayout(portrait, 2)
        connection = QFrame()
        connection.setObjectName("panel")
        card = QVBoxLayout(connection)
        card.setContentsMargins(24, 22, 24, 22)
        card.setSpacing(13)
        card.addWidget(label("Meet your iPhone", "heading"))
        self.connection = label("Waiting for your iPhone", None, True)
        card.addWidget(self.connection)
        self.network = label("Starting local discovery…", "subtle", True)
        card.addWidget(self.network)
        card.addWidget(label("Open the original iOS app on the same Wi-Fi, then allow the connection here. It may still call this computer a “Mac”.", "subtle", True))
        self.wake = button("Wake Bluey", lambda: self.action.emit("wake"), "primary")
        self.wake.setEnabled(False)
        card.addWidget(self.wake)
        card.addWidget(button("Try the cursor", lambda: self.action.emit("demo")))
        card.addStretch()
        hero.addWidget(connection, 3)
        page.addLayout(hero, 1)
        self.latest = label("Double-tap Bluey on your phone to wake him. Hold to ask; release to answer.", "subtle", True)
        page.addWidget(self.latest)
        controls = QHBoxLayout()
        controls.addWidget(button("Point here", lambda: self.action.emit("point")))
        controls.addWidget(button("Go home", lambda: self.action.emit("home")))
        controls.addStretch()
        controls.addWidget(button("Stop actions", lambda: self.action.emit("stop"), "stop"))
        page.addLayout(controls)
        page.addWidget(label("Ctrl + Alt + S stops actions immediately. Computer control starts off.", "subtle", True))
        self._settings(tabs)
        footer = QHBoxLayout()
        footer.addWidget(label("GOOGLY EYES  /  DESKTOP COMPANION", "subtle"))
        footer.addStretch()
        footer.addWidget(button("Quit", lambda: self.action.emit("quit")))
        layout.addLayout(footer)

    def _settings(self, tabs):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        scroll.setWidget(panel)
        tabs.addTab(scroll, "Settings")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 20, 14, 12)
        layout.setSpacing(15)
        layout.addWidget(label("Make yourself at home", "heading"))
        self.key_status = label("Add an OpenAI API key to use voice and research.", "subtle", True)
        layout.addWidget(self.key_status)
        row = QHBoxLayout()
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("OpenAI API key · sk-…")
        self.key.setAccessibleName("OpenAI API key")
        row.addWidget(self.key, 1)
        row.addWidget(button("Save key", lambda: self.save_key.emit(self.key.text())))
        row.addWidget(button("Remove", self.remove_key.emit))
        layout.addLayout(row)
        self.control = QCheckBox("Let Bluey use the computer this session")
        self.control.toggled.connect(self.control_changed)
        layout.addWidget(self.control)
        layout.addWidget(label("When enabled, he can click, type and open apps when you ask. Only approve phones you trust; pairing uses your local network.", "subtle", True))
        form = QFormLayout()
        form.setVerticalSpacing(12)
        size = QSlider(Qt.Orientation.Horizontal)
        size.setRange(48, 120)
        size.setValue(self.prefs.cursor_size)
        size.setAccessibleName("Cursor size")
        size.valueChanged.connect(lambda value: self.change("cursor_size", value))
        form.addRow("Cursor size", size)
        position = QComboBox()
        position.addItems(["Left", "Center", "Right"])
        position.setCurrentIndex(round(self.prefs.phone_position * 2))
        position.currentIndexChanged.connect(lambda value: self.change("phone_position", value / 2))
        form.addRow("Phone position", position)
        mood = QComboBox()
        mood.addItems([v.title() for v in MOODS])
        mood.setCurrentText(self.prefs.mood.title())
        mood.currentTextChanged.connect(lambda value: self.change("mood", value.lower()))
        form.addRow("Mood", mood)
        layout.addLayout(form)
        checks = QGridLayout()
        for i, (key, title) in enumerate((("follow_mouse", "Eyes follow your mouse"), ("show_cursor", "Show cursor and captions"), ("glow", "Cursor glow"), ("reduced_motion", "Reduce motion"))):
            item = QCheckBox(title)
            item.setChecked(getattr(self.prefs, key))
            item.toggled.connect(lambda value, key=key: self.change(key, value))
            checks.addWidget(item, i // 2, i % 2)
        layout.addLayout(checks)
        layout.addWidget(label("Personality", "heading"))
        self.personality = QTextEdit()
        self.personality.setAcceptRichText(False)
        self.personality.setPlaceholderText("Leave blank for Bluey's original cheeky personality.")
        self.personality.setPlainText(self.prefs.personality)
        self.personality.setMaximumHeight(110)
        layout.addWidget(self.personality)
        layout.addWidget(button("Save personality", self.save_personality))
        self.diagnostics = label("", "subtle", True)
        self.diagnostics.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.diagnostics)
        layout.addStretch()

    def change(self, key, value):
        setattr(self.prefs, key, value)
        self.preferences_changed.emit()

    def save_personality(self):
        self.change("personality", self.personality.toPlainText().strip()[:8000])
        self.action.emit("personality")

    def set_phones(self, names):
        self.connection.setText("Connected · " + ", ".join(names) if names else "Waiting for your iPhone")
        self.wake.setEnabled(bool(names))

    def show_notice(self, text):
        self.notice.setText(text)
        self.notice.setVisible(bool(text))

    def closeEvent(self, event):
        if self.has_tray:
            self.hide()
            event.ignore()
        else:
            self.action.emit("quit")
            event.accept()


class ReportWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bluey's research")
        self.setWindowIcon(berry_icon())
        self.setStyleSheet(STYLE)
        self.resize(640, 650)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(self.open_link)
        self.setCentralWidget(self.browser)

    @staticmethod
    def open_link(url):
        if url.scheme() in {"http", "https"} and url.host():
            QDesktopServices.openUrl(url)

    def show_report(self, report):
        title = html.escape(str(report.get("title", "Research")))
        body = "".join(f"<p>{html.escape(str(p))}</p>" for p in report.get("paragraphs", []))
        sources = "".join(f'<p><a style="color:#A9BCFF" href="{html.escape(str(s.get("url", "")), quote=True)}">{html.escape(str(s.get("title", "Source")))}</a></p>' for s in report.get("sources", []))
        self.browser.setHtml(f'<div style="padding:20px"><h1>{title}</h1>{body}<h3>Sources</h3>{sources}</div>')
        self.show()
        self.raise_()
