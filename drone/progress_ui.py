# SPDX-License-Identifier: AGPL-3.0-or-later
"""Progress that shows it is alive.

Some steps report rarely (loading the CLIP model, analysing a long music
track, encoding a 4K video with the music), so the bar can sit still for a
while and look frozen. :class:`Heartbeat` drives a bar, a status line and a
percentage label: a spinner and the elapsed time tick every half second
whatever the work is doing, and after a long quiet stretch the line says so
in plain words on a second line ("Ainda trabalhando, normal nas etapas longas") instead of leaving the user guessing.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer

SPIN = "◐◓◑◒"
QUIET = 15.0            # seconds without news before saying "still working"


def clock(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


class Heartbeat(QObject):
    def __init__(self, bar, status, pct_label, parent=None):
        super().__init__(parent)
        self.bar, self.status, self.pct = bar, status, pct_label
        self.text = ""
        self.value = 0.0
        self.t0 = self.last = 0.0
        self.k = 0
        self.running = False
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)

    def start(self, text: str = "Começando…") -> None:
        self.running = True
        self.t0 = self.last = time.monotonic()
        self.value = 0.0
        self.text = text
        self.bar.setValue(0)
        self.timer.start()
        self._tick()

    def update(self, pct: float, text: str = "") -> None:
        pct = max(0.0, min(100.0, float(pct)))
        if pct != self.value or (text and text != self.text):
            self.last = time.monotonic()
        self.value = pct
        if text:
            self.text = text
        self.bar.setValue(int(pct * 10))
        if not self.running:
            self.pct.setText(f"{pct:.0f}%")
            self.status.setText(self.text)
        else:
            self._tick()

    def touch(self, *_):
        """Any news (a log line) counts as a sign of life."""
        self.last = time.monotonic()

    def finish(self, text: str, ok: bool = True) -> None:
        self.running = False
        self.timer.stop()
        if ok:
            self.bar.setValue(1000)
            self.pct.setText("100%")
        else:
            self.pct.setText("")
        self.status.setText(text)

    def _tick(self) -> None:
        if not self.running:
            return
        now = time.monotonic()
        self.k = (self.k + 1) % len(SPIN)
        line = f"{SPIN[self.k]}  {self.text}   ·   {clock(now - self.t0)}"
        quiet = now - self.last
        if quiet >= QUIET:                    # second line: this step's own time
            line += f"\nAinda trabalhando, normal nas etapas longas  ·  nesta etapa há {clock(quiet)}"
        self.status.setText(line)
        self.pct.setText(f"{self.value:.0f}%")
