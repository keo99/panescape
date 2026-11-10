"""The desk drawn to scale: housings, lit areas, and an optional picture across them.

Click a monitor to select it. Drag it (or use the arrow keys) to move it:
the editor turns the movement into gap / v_pos / h_pos changes.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..layout import Layout, Rect

BG = QColor(30, 30, 34)
HOUSING = QColor(62, 62, 68)
LIT = QColor(16, 16, 20)
OUTLINE = QColor(110, 110, 118)
ACCENT = QColor(80, 160, 255)
TEXT = QColor(235, 235, 235)
MUTED = QColor(160, 160, 165)
WARN = QColor(255, 120, 90)


class DeskView(QWidget):
    selected = Signal(int)                 # monitor index
    nudged = Signal(int, float, float)     # monitor index, dx, dy in mm (y down)
    nudge_done = Signal()                  # end of a drag
    resized = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(320, 200)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.layout_: Layout | None = None
        self.valid = True
        self.error = ""
        self.sel = -1
        self.image: QImage | None = None
        self.image_is_pattern = False
        self._frozen = None      # view transform held during a drag
        self._drag = None        # (index, last mm point)
        self._pending = [0.0, 0.0]

    # ------------------------------------------------------------------ api
    def set_layout(self, layout: Layout | None, valid: bool = True, error: str = "") -> None:
        self.layout_, self.valid, self.error = layout, valid, error
        self.update()

    def set_selected(self, i: int) -> None:
        self.sel = i
        self.update()

    def set_image(self, image: QImage | None, pattern: bool = False) -> None:
        """A picture to show across the lit areas (cover-fitted to the desk)."""
        self.image, self.image_is_pattern = image, pattern
        self.update()

    def px_per_mm(self) -> float:
        """Device pixels per mm at the current zoom (for drawing overlays crisply)."""
        t = self._transform()
        return (t[0] if t else 1.0) * self.devicePixelRatioF()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()

    # ------------------------------------------------------------------ geometry
    def _bounds(self) -> Rect | None:
        if not self.layout_:
            return None
        xs = [v for p in self.layout_ for v in (p.outer.x, p.outer.x1)]
        ys = [v for p in self.layout_ for v in (p.outer.y, p.outer.y1)]
        return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def _transform(self):
        if self._frozen:
            return self._frozen
        b = self._bounds()
        if b is None or b.w <= 0 or b.h <= 0:
            return None
        margin = 24
        k = min((self.width() - 2 * margin) / b.w, (self.height() - 2 * margin - 22) / b.h)
        k = max(k, 0.01)
        ox = (self.width() - b.w * k) / 2 - b.x * k
        oy = (self.height() - 22 - b.h * k) / 2 - b.y * k
        return (k, ox, oy)

    def _rect(self, r: Rect, t) -> QRectF:
        k, ox, oy = t
        return QRectF(ox + r.x * k, oy + r.y * k, r.w * k, r.h * k)

    def _to_mm(self, pos: QPointF, t) -> tuple[float, float]:
        k, ox, oy = t
        return ((pos.x() - ox) / k, (pos.y() - oy) / k)

    def _hit(self, pos: QPointF) -> int:
        t = self._transform()
        if not t or not self.layout_:
            return -1
        for p in reversed(self.layout_.placed):
            if self._rect(p.outer, t).contains(pos):
                return p.monitor.index
        return -1

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _event):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.setRenderHint(QPainter.SmoothPixmapTransform)
        qp.fillRect(self.rect(), BG)
        t = self._transform()
        if not t:
            qp.setPen(MUTED)
            qp.drawText(self.rect(), Qt.AlignCenter, self.error or "No monitors")
            return
        k = t[0]
        lay = self.layout_
        for p in lay:
            qp.setPen(QPen(OUTLINE, 1))
            qp.setBrush(HOUSING)
            qp.drawRoundedRect(self._rect(p.outer, t), 3, 3)
        lit = QPainterPath()
        for p in lay:
            lit.addRect(self._rect(p.active, t))
        qp.fillPath(lit, LIT)
        if self.image is not None and not self.image.isNull():
            qp.save()
            qp.setClipPath(lit)
            target = self._rect(lay.canvas, t)
            iw, ih = self.image.width(), self.image.height()
            c = lay.canvas
            if self.image_is_pattern:   # drawn for exactly the canvas
                src = QRectF(0, 0, iw, ih)
            else:                       # cover: crop the picture's edges, centred
                s = min(iw / c.w, ih / c.h)
                w, h = c.w * s, c.h * s
                src = QRectF((iw - w) / 2, (ih - h) / 2, w, h)
            qp.drawImage(target, self.image, src)
            qp.restore()

        small = QFont(self.font())
        small.setPointSizeF(max(7.0, min(10.0, 3.2 * k)))
        big = QFont(self.font())
        big.setPointSizeF(max(8.0, min(14.0, 4.5 * k)))
        big.setBold(True)
        for p in lay:
            m = p.monitor
            r = self._rect(p.active, t)
            if m.index == self.sel:
                qp.setPen(QPen(ACCENT, 3))
                qp.setBrush(Qt.NoBrush)
                qp.drawRect(self._rect(p.outer, t).adjusted(-2, -2, 2, 2))
            # label with a dark backing so it reads over pictures
            lines = [(m.name, big), (f"{m.pixels[0]}×{m.pixels[1]}", small),
                     (f"{p.active.w:.0f} × {p.active.h:.0f} mm", small)]
            qp.save()
            y = r.center().y() - 18
            for text, font in lines:
                qp.setFont(font)
                fm = qp.fontMetrics()
                w = fm.horizontalAdvance(text) + 8
                box = QRectF(r.center().x() - w / 2, y, w, fm.height() + 2)
                if self.image is not None:
                    qp.fillRect(box, QColor(0, 0, 0, 150))
                qp.setPen(TEXT if font is big else MUTED)
                qp.drawText(box, Qt.AlignCenter, text)
                y += fm.height() + 3
            qp.restore()
            if m.index == 0:
                qp.setPen(MUTED)
                qp.setFont(small)
                qp.drawText(self._rect(p.outer, t).adjusted(4, 2, -4, -2), Qt.AlignTop | Qt.AlignLeft, "⚓")

        # footer: size of the desk + status
        qp.setFont(self.font())
        b = self._bounds()
        footer = f"desk {b.w:.0f} × {b.h:.0f} mm  ·  picture area {lay.canvas.w:.0f} × {lay.canvas.h:.0f} mm"
        if self.sel > 0:
            footer += "  ·  drag or use arrow keys to move (Shift ×10, Alt ×0.2)"
        qp.setPen(MUTED)
        qp.drawText(QRectF(8, self.height() - 22, self.width() - 16, 20), Qt.AlignLeft | Qt.AlignVCenter, footer)
        if not self.valid:
            qp.fillRect(self.rect(), QColor(0, 0, 0, 120))
            qp.setPen(WARN)
            qp.drawText(self.rect().adjusted(20, 20, -20, -20), Qt.AlignCenter | Qt.TextWordWrap,
                        f"Not valid yet:\n{self.error}")

    # ------------------------------------------------------------------ interaction
    def mousePressEvent(self, e):
        i = self._hit(e.position())
        if i >= 0:
            self.selected.emit(i)
            if e.button() == Qt.LeftButton and i > 0:
                t = self._transform()
                self._frozen = t
                self._drag = (i, self._to_mm(e.position(), t))
                self._pending = [0.0, 0.0]
                self.setCursor(Qt.ClosedHandCursor)
        self.setFocus()

    def mouseMoveEvent(self, e):
        if not self._drag:
            self.setCursor(Qt.OpenHandCursor if self._hit(e.position()) > 0 else Qt.ArrowCursor)
            return
        i, (x0, y0) = self._drag
        x, y = self._to_mm(e.position(), self._frozen)
        step = 0.1 if e.modifiers() & Qt.AltModifier else 0.5
        self._pending[0] += x - x0
        self._pending[1] += y - y0
        dx = round(self._pending[0] / step) * step
        dy = round(self._pending[1] / step) * step
        if e.modifiers() & Qt.ShiftModifier:  # constrain to one axis
            if abs(self._pending[0]) > abs(self._pending[1]):
                dy = 0.0
                self._pending[1] = 0.0
            else:
                dx = 0.0
                self._pending[0] = 0.0
        self._drag = (i, (x, y))
        if dx or dy:
            self._pending[0] -= dx
            self._pending[1] -= dy
            self.nudged.emit(i, dx, dy)

    def mouseReleaseEvent(self, _e):
        if self._drag:
            self._drag = None
            self._frozen = None
            self.setCursor(Qt.ArrowCursor)
            self.nudge_done.emit()
            self.update()

    def keyPressEvent(self, e):
        moves = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
        if self.sel > 0 and e.key() in moves:
            step = 5.0 if e.modifiers() & Qt.ShiftModifier else 0.1 if e.modifiers() & Qt.AltModifier else 0.5
            dx, dy = moves[e.key()]
            self.nudged.emit(self.sel, dx * step, dy * step)
            self.nudge_done.emit()
            return
        super().keyPressEvent(e)
