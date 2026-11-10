"""Small shared pieces: background tasks, number fields, message boxes."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import QDoubleSpinBox, QMessageBox, QWidget

_live: set = set()  # running tasks (keeps their signal objects alive)


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self):
        try:
            result = self.fn()
        except Exception as e:  # noqa: BLE001 - reported to the window
            self.signals.failed.emit(str(e) or type(e).__name__)
        else:
            self.signals.done.emit(result)


def run_async(fn, on_done=None, on_fail=None) -> None:
    """Run ``fn()`` on a worker thread; the callbacks run on the GUI thread."""
    task = _Task(fn)
    _live.add(task)

    def finish(handler, value):
        _live.discard(task)
        if handler:
            handler(value)

    task.signals.done.connect(lambda r: finish(on_done, r), Qt.QueuedConnection)
    task.signals.failed.connect(lambda e: finish(on_fail, e), Qt.QueuedConnection)
    QThreadPool.globalInstance().start(task)


def mm_spin(lo: float = -2000, hi: float = 2000, step: float = 0.5, decimals: int = 1,
            suffix: str = " mm", special: str | None = None) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)  # typing "12" doesn't fire for "1" first
    s.setAccelerated(True)
    if special:
        s.setSpecialValueText(special)  # shown at the minimum
    return s


def number(v: float):
    """Tidy number for YAML: 12 instead of 12.0, no float noise."""
    v = round(float(v), 2)
    return int(v) if v == int(v) else v


def error(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


def confirm(parent: QWidget, title: str, text: str, ok: str = "OK") -> bool:
    box = QMessageBox(QMessageBox.Question, title, text, parent=parent)
    yes = box.addButton(ok, QMessageBox.AcceptRole)
    box.addButton(QMessageBox.Cancel)
    box.exec()
    return box.clickedButton() is yes
