"""Qt drawings: the shared berry palette, eyes, pointer and speech bubble."""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QFontDatabase, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from .settings import Preferences

INK = "#17151F"
SOFT = "#B9B2CC"
BERRY = "#A9BCFF"


def register_fonts() -> None:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    for folder in (root / "Shared" / "Fonts", root / "fonts"):
        if folder.exists():
            for font in folder.glob("*.ttf"):
                QFontDatabase.addApplicationFont(str(font))
    QApplication.instance().setFont(QFont("IBM Plex Sans", 11))


def draw_berry(p: QPainter, rect: QRectF, gaze: QPointF = QPointF(), happy: bool = False) -> None:
    p.save()
    p.translate(rect.x(), rect.y())
    p.scale(rect.width() / 200, rect.height() / 200)
    gradient = QLinearGradient(45, 15, 150, 200)
    for stop, color in ((0, "#A9BCFF"), (.34, "#6C86F5"), (.68, "#4254D6"), (1, "#2B2F8F")):
        gradient.setColorAt(stop, QColor(color))
    body = QPainterPath()
    body.moveTo(100, 18)
    body.cubicTo(125, 1, 173, 34, 183, 67)
    body.cubicTo(211, 122, 180, 180, 148, 186)
    body.cubicTo(117, 207, 65, 193, 44, 185)
    body.cubicTo(6, 172, 0, 112, 17, 75)
    body.cubicTo(16, 43, 61, 6, 100, 18)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(gradient)
    p.drawPath(body)
    for x, y, size in ((51, 66, 55), (110, 60, 57)):
        p.setBrush(QColor(27, 30, 99, 75))
        p.drawEllipse(QRectF(x - 2, y + 4, size + 4, size + 4))
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, y, size, size))
        p.setBrush(QColor(INK))
        p.drawEllipse(QRectF(x + 18 + gaze.x() * 8, y + 16 + gaze.y() * 8, 23, 26))
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x + 22 + gaze.x() * 8, y + 18 + gaze.y() * 8, 7, 8))
    p.setPen(QPen(QColor("#1C1F66"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    if happy:
        p.drawArc(QRectF(92, 122, 29, 18), 195 * 16, 145 * 16)
    else:
        p.drawLine(QPointF(104, 137), QPointF(113, 136))
    p.restore()


def berry_icon() -> QIcon:
    pix = QPixmap(128, 128)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw_berry(painter, QRectF(0, 0, 128, 128))
    painter.end()
    return QIcon(pix)


class BerryPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(220, 220)
        self.setAccessibleName("Bluey, a blueberry with googly eyes")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(50)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        size = min(self.width(), self.height()) - 26
        mouse = self.mapFromGlobal(QCursor.pos())
        gaze = QPointF(max(-1, min(1, (mouse.x() - self.width() / 2) / 150)), max(-1, min(1, (mouse.y() - self.height() / 2) / 150)))
        draw_berry(p, QRectF((self.width() - size) / 2, (self.height() - size) / 2, size, size), gaze, True)


class CursorOverlay(QWidget):
    def __init__(self, prefs: Preferences):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.prefs = prefs
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.caption = ""
        self.caption_until = 0.0
        self.pinned = False
        self.awake = False
        self.suspended = False
        self.destination = QPointF()
        self.tip = QPointF()
        self.trail: list[QPointF] = []
        self.refresh_geometry()
        self.tip = self.home_point()
        self.destination = self.tip
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(16)
        QApplication.instance().primaryScreenChanged.connect(self.refresh_geometry)

    def refresh_geometry(self, *_):
        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())

    def home_point(self) -> QPointF:
        return QPointF(70 + (self.width() - 140) * self.prefs.phone_position, self.height() - 96)

    def point(self, x: float, y: float):
        self.pinned = True
        self.destination = QPointF(max(4, min(self.width() - 8, x)), max(4, min(self.height() - 8, y)))

    def home(self):
        self.pinned = False
        self.destination = self.home_point()
        self.trail.clear()

    def speak(self, text: str, seconds: float = 0):
        self.caption = text[:3000]
        self.caption_until = time.monotonic() + seconds if seconds else 0
        self.update()

    def face(self) -> dict:
        point = self.tip if self.pinned else self.mapFromGlobal(QCursor.pos())
        gx = max(-1, min(1, (point.x() / max(1, self.width()) - self.prefs.phone_position) * 2))
        gy = max(-1, min(1, point.y() / max(1, self.height()) * 2 - 1))
        return {"gazeX": gx, "gazeY": gy, "mood": "pointing" if self.pinned else self.prefs.mood, "talk": 0.35 if self.caption and self.awake else 0}

    def animate(self):
        if self.caption_until and time.monotonic() > self.caption_until:
            self.caption = ""
            self.caption_until = 0
        if not self.pinned:
            self.destination = self.home_point()
        delta = self.destination - self.tip
        if self.prefs.reduced_motion:
            self.tip = QPointF(self.destination)
            self.trail.clear()
        else:
            self.tip += delta * .16
            self.trail.append(QPointF(self.tip))
            self.trail = self.trail[-18:]
        self.update()

    def paintEvent(self, event):
        if self.suspended:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        visible = self.prefs.show_cursor and (self.pinned or not self.prefs.follow_mouse)
        if visible:
            if self.prefs.glow:
                p.setPen(Qt.PenStyle.NoPen)
                for radius in (30, 22, 14):
                    p.setBrush(QColor(108, 134, 245, 18))
                    p.drawEllipse(self.tip, radius, radius)
            if not self.prefs.reduced_motion:
                for i, point in enumerate(self.trail):
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(169, 188, 255, int(i / 18 * 70)))
                    p.drawEllipse(point, i / 5 + 1, i / 5 + 1)
            p.save()
            p.translate(self.tip)
            p.scale(self.prefs.cursor_size / 72, self.prefs.cursor_size / 72)
            pointer = QPainterPath()
            pointer.moveTo(0, 0)
            pointer.lineTo(10, 61)
            pointer.quadTo(12, 66, 17, 60)
            pointer.lineTo(27, 45)
            pointer.lineTo(44, 65)
            pointer.quadTo(48, 69, 52, 64)
            pointer.lineTo(57, 60)
            pointer.quadTo(61, 56, 57, 52)
            pointer.lineTo(40, 34)
            pointer.lineTo(57, 28)
            pointer.quadTo(64, 26, 57, 21)
            pointer.closeSubpath()
            grad = QLinearGradient(0, 0, 50, 65)
            grad.setColorAt(0, QColor("#D9E2FF"))
            grad.setColorAt(1, QColor("#6C86F5"))
            p.setBrush(grad)
            p.setPen(QPen(QColor("#4254D6"), 2.5))
            p.drawPath(pointer)
            p.restore()
        if self.caption and self.prefs.show_cursor:
            font = QFont("Fredoka", 17)
            p.setFont(font)
            max_width = min(430, self.width() - 32)
            text_rect = p.fontMetrics().boundingRect(0, 0, max_width - 38, 220, Qt.TextFlag.TextWordWrap, self.caption)
            width, height = min(max_width, text_rect.width() + 38), min(250, text_rect.height() + 28)
            anchor = self.tip if visible else self.home_point()
            x = max(16, min(self.width() - width - 16, anchor.x() - width / 2))
            y = anchor.y() - height - 22
            if y < 16:
                y = min(self.height() - height - 16, anchor.y() + 80)
            box = QRectF(x, y, width, height)
            p.setBrush(QColor("#F1F3FF"))
            p.setPen(QPen(QColor("#A9BCFF"), 1.5))
            p.drawRoundedRect(box, 19, 19)
            p.setPen(QColor(INK))
            p.drawText(box.adjusted(19, 12, -19, -12), Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignVCenter, self.caption)
