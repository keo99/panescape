"""The desk drawn to scale: housings, lit areas, and an optional picture across them.

Click a monitor to select it. Drag it (or use the arrow keys) to move it:
the editor turns the movement into gap / v_pos / h_pos changes.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..layout import Layout, Rect
from ..render import CropSpec

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
    crop_changed = Signal(object)          # CropSpec, while framing a picture
    crop_done = Signal()                   # end of a framing drag / zoom

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
        self.framing = False     # picture mode: drag pans the picture, wheel zooms
        self.crop = CropSpec()
        self.labels = True

    # ------------------------------------------------------------------ api
    def set_layout(self, layout: Layout | None, valid: bool = True, error: str = "") -> None:
        self.layout_, self.valid, self.error = layout, valid, error
        self.update()

    def set_selected(self, i: int) -> None:
        self.sel = i
        self.update()

    def set_image(self, image: QImage | None, pattern: bool = False, crop: CropSpec | None = None) -> None:
        """A picture to show across the lit areas, framed by ``crop`` (default: centred, cover)."""
        self.image, self.image_is_pattern = image, pattern
        self.crop = crop or CropSpec()
        self.update()

    def set_framing(self, on: bool) -> None:
        """Picture mode: dragging moves the picture and the wheel zooms (monitors stay put)."""
        self.framing = on
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

    def _image_mm(self) -> tuple[Rect, float] | None:
        """Where the whole picture lies on the desk (mm), and its pixels per mm."""
        if self.image is None or self.image.isNull() or not self.layout_:
            return None
        c = self.layout_.canvas
        iw, ih = self.image.width(), self.image.height()
        cr = self.crop
        s = (min(iw / c.w, ih / c.h) if cr.fit == "cover" else max(iw / c.w, ih / c.h)) * cr.zoom
        ox = (iw - c.w * s) * cr.anchor[0]
        oy = (ih - c.h * s) * cr.anchor[1]
        return Rect(c.x - ox / s, c.y - oy / s, iw / s, ih / s), s

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
            qp.drawText(self.rect().adjusted(20, 20, -20, -20), Qt.AlignCenter | Qt.TextWordWrap,
                        self.error or "No monitors")
            return
        k = t[0]
        lay = self.layout_
        has_picture = self.image is not None and not self.image.isNull() and not self.image_is_pattern
        if has_picture and self.framing:   # the parts that get cut off, faintly, behind the monitors
            qp.setOpacity(0.28)
            qp.drawImage(self._rect(self._image_mm()[0], t), self.image)
            qp.setOpacity(1.0)
        for p in lay:
            qp.setPen(QPen(OUTLINE, 1))
            qp.setBrush(HOUSING)
            qp.drawRoundedRect(self._rect(p.outer, t), 3, 3)
        lit = QPainterPath()
        for p in lay:
            lit.addRect(self._rect(p.active, t))
        qp.fillPath(lit, LIT)
        if self.image is not None and not self.image.isNull():
            if self.image_is_pattern:   # drawn for exactly the canvas
                where = self._rect(lay.canvas, t)
            else:
                where = self._rect(self._image_mm()[0], t)
                qp.fillPath(lit, QColor(self.crop.background))
            qp.save()
            qp.setClipPath(lit)
            qp.drawImage(where, self.image)
            qp.restore()

        small = QFont(self.font())
        small.setPointSizeF(max(7.0, min(10.0, 3.2 * k)))
        big = QFont(self.font())
        big.setPointSizeF(max(8.0, min(14.0, 4.5 * k)))
        big.setBold(True)
        for p in lay if self.labels else []:
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
        if self.framing and self.image is not None:
            footer = (f"zoom {self.crop.zoom:.2f}×  ·  drag the picture to move it, scroll to zoom")
        elif self.sel > 0:
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
        if self.framing:
            if e.button() == Qt.LeftButton and self._image_mm():
                t = self._transform()
                self._frozen = t
                self._drag = (-1, self._to_mm(e.position(), t))
                self.setCursor(Qt.ClosedHandCursor)
            return
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
        if self.framing:
            if not self._drag:
                self.setCursor(Qt.OpenHandCursor if self._image_mm() else Qt.ArrowCursor)
                return
            _, (x0, y0) = self._drag
            x, y = self._to_mm(e.position(), self._frozen)
            self._drag = (-1, (x, y))
            self._pan(x - x0, y - y0)
            return
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
        if self._drag and self.framing:
            self._drag = None
            self._frozen = None
            self.setCursor(Qt.OpenHandCursor)
            self.crop_done.emit()
            return
        if self._drag:
            self._drag = None
            self._frozen = None
            self.setCursor(Qt.ArrowCursor)
            self.nudge_done.emit()
            self.update()

    def _pan(self, dx: float, dy: float) -> None:
        """Move the picture by (dx, dy) mm: turns into a new anchor."""
        found = self._image_mm()
        if not found:
            return
        _, s = found
        c = self.layout_.canvas
        iw, ih = self.image.width(), self.image.height()
        ax, ay = self.crop.anchor
        span_x, span_y = iw - c.w * s, ih - c.h * s   # px of slack on each axis
        if abs(span_x) > 1e-6:
            ax = min(1.0, max(0.0, ax - dx * s / span_x))
        if abs(span_y) > 1e-6:
            ay = min(1.0, max(0.0, ay - dy * s / span_y))
        self._set_crop(replace(self.crop, anchor=(round(ax, 4), round(ay, 4))))

    def _set_crop(self, crop: CropSpec) -> None:
        self.crop = crop
        self.update()
        self.crop_changed.emit(crop)

    def wheelEvent(self, e):
        if not (self.framing and self._image_mm()):
            return super().wheelEvent(e)
        steps = e.angleDelta().y() / 120
        zoom = min(8.0, max(0.5, self.crop.zoom * (1.1 ** steps)))
        if abs(zoom - 1.0) < 0.03:
            zoom = 1.0  # snap back to the exact fit
        self._set_crop(replace(self.crop, zoom=round(zoom, 3)))
        self.crop_done.emit()

    def keyPressEvent(self, e):
        moves = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, -1), Qt.Key_Down: (0, 1)}
        if self.sel > 0 and e.key() in moves:
            step = 5.0 if e.modifiers() & Qt.ShiftModifier else 0.1 if e.modifiers() & Qt.AltModifier else 0.5
            dx, dy = moves[e.key()]
            self.nudged.emit(self.sel, dx * step, dy * step)
            self.nudge_done.emit()
            return
        super().keyPressEvent(e)
