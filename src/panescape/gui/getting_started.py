"""The getting-started strip: three steps from an empty folder to a wallpaper.

Shown above the tabs until every step is done or it is closed. Each step
says whether it's done and jumps to the place that does it.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton

from ..project import Project, ProjectError

STEPS = (
    ("monitors", "Describe your monitors",
     "Make a layout from the monitors connected now, then measure the gaps and bezels (Layout tab)."),
    ("pictures", "Add pictures",
     "Copy in your own pictures, or find some on Unsplash (Pictures & tags, Unsplash tabs)."),
    ("apply", "Apply to the desktop",
     "Choose one picture or a slideshow and put it on your screens (Apply tab)."),
)


def step_state(project: Project) -> dict[str, bool]:
    try:
        project.config_path(None)
        monitors = True
    except (ProjectError, OSError, ValueError):
        monitors = False
    try:
        pictures = bool(project.all_pictures())
    except (ProjectError, OSError):
        pictures = False
    try:
        applied = project.state_path().exists()
    except (ProjectError, OSError):
        applied = False
    return {"monitors": monitors, "pictures": pictures, "apply": applied}


class GettingStarted(QFrame):
    step_clicked = Signal(str)   # a key from STEPS

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setObjectName("getting_started")
        self.setStyleSheet("#getting_started { background: palette(alternate-base); border: 1px solid "
                           "palette(mid); border-radius: 6px; }")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 6, 6)
        title = QLabel("<b>Getting started</b>")
        row.addWidget(title)
        self.buttons: dict[str, QPushButton] = {}
        for i, (key, text, tip) in enumerate(STEPS, 1):
            if i > 1:
                row.addWidget(QLabel("→"))
            b = QPushButton(f"{i}. {text}")
            b.setToolTip(tip)
            b.setFlat(True)
            b.clicked.connect(lambda _c=False, k=key: self.step_clicked.emit(k))
            row.addWidget(b)
            self.buttons[key] = b
        row.addStretch(1)
        close = QToolButton()
        close.setText("✕")
        close.setAutoRaise(True)
        close.setToolTip("Hide this guide (Help ▸ Getting started brings it back)")
        close.clicked.connect(self.dismiss)
        row.addWidget(close)
        self.refresh()

    def _key(self) -> str:
        return f"getting_started_hidden/{self.project.root}"

    def dismiss(self) -> None:
        QSettings().setValue(self._key(), True)
        self.setVisible(False)

    def bring_back(self) -> None:
        QSettings().remove(self._key())
        self.refresh(force_show=True)

    def refresh(self, force_show: bool = False) -> None:
        state = step_state(self.project)
        first_open = next((k for k, _t, _x in STEPS if not state[k]), None)
        for i, (key, text, _tip) in enumerate(STEPS, 1):
            b = self.buttons[key]
            done = state[key]
            b.setText(f"✓ {text}" if done else f"{i}. {text}")
            f = b.font()
            f.setBold(key == first_open)
            b.setFont(f)
        hidden = QSettings().value(self._key(), False) in (True, "true")
        self.setVisible(force_show or (not hidden and first_open is not None))
