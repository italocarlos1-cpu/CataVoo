# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live preview of the edit: plays a list of pieces of the raw clips as one film.

Nothing is rendered. Two video players take turns: while one shows the
current shot, the other has already opened the next one and sits paused on its
first frame, so a cut is a swap instead of a file load. A wall clock drives the
film; the music (if any) plays alongside on its own player, kept in step with
that clock. Transitions are shown as straight cuts at the right moment — the
timing is exact, the dissolves appear only in the rendered video.

Without QtMultimedia (or with ``DRONEHIGHLIGHTER_NO_VIDEO_PREVIEW`` set) the
widget is a plain still-frame monitor and :attr:`available` is False.
``DRONEHIGHLIGHTER_NO_AUDIO`` keeps it silent (no sound device needed).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import QElapsedTimer, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QStackedWidget


@dataclass
class Piece:
    path: str
    src_start: float          # seconds into the raw clip
    src_end: float
    film_start: float         # where it starts in the film

    @property
    def length(self) -> float:
        return self.src_end - self.src_start

    @property
    def film_end(self) -> float:
        return self.film_start + self.length


class _Deck:
    """One QMediaPlayer + its video widget, with a seek that waits for the file."""

    def __init__(self, owner, widget):
        from PySide6.QtMultimedia import QMediaPlayer
        self.widget = widget
        self.player = QMediaPlayer(owner)
        self.player.setVideoOutput(widget)
        self.path = ""
        self.piece = -1
        self._seek = None
        self._play_after = False
        self.player.mediaStatusChanged.connect(self._status)

    def load(self, path: str, t: float, piece: int, play: bool) -> None:
        self.piece = piece
        self._play_after = play
        if path != self.path:
            self.path = path
            self._seek = t
            self.player.setSource(QUrl.fromLocalFile(path))
            if play:
                self.player.play()
            else:
                self.player.pause()
        else:
            self._seek = None
            self.player.setPosition(int(t * 1000))
            if play:
                self.player.play()
            else:
                self.player.pause()

    def _status(self, status) -> None:
        from PySide6.QtMultimedia import QMediaPlayer
        if self._seek is not None and status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia,
                                                 QMediaPlayer.BufferingMedia):
            t, self._seek = self._seek, None
            self.player.setPosition(int(t * 1000))
            if self._play_after:
                self.player.play()
            else:
                self.player.pause()

    def play(self) -> None:
        self._play_after = True
        self.player.play()

    def pause(self) -> None:
        self._play_after = False
        self.player.pause()

    def position(self) -> float:
        return self.player.position() / 1000.0


class PreviewPlayer(QObject):
    position_changed = Signal(float)        # film seconds
    piece_changed = Signal(int)
    state_changed = Signal(bool)            # playing?
    still_needed = Signal(str, float)       # paused: (raw clip, seconds) to show as a still

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.stack.setStyleSheet("background:#000; border-radius:8px")
        self.still = QLabel()
        self.still.setAlignment(Qt.AlignCenter)
        self.still.setStyleSheet("background:#000; color:#5a5a5a")
        self.still.setMinimumSize(320, 180)
        self.still.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self._still_pm = None
        self.stack.addWidget(self.still)
        self.stack.installEventFilter(self)
        self.pieces: list[Piece] = []
        self.music_path = ""
        self.music_offset = 0.0
        self.t = 0.0
        self.playing = False
        self.cur = -1
        self._decks: list[_Deck] = []
        self._active = 0
        self._music = None
        self._audio = None
        self._clock = QElapsedTimer()
        self._t0 = 0.0
        self._tick = QTimer(self)
        self._tick.setInterval(15)
        self._tick.timeout.connect(self._on_tick)
        self._resync = 0
        self.available = False
        if os.environ.get("DRONEHIGHLIGHTER_NO_VIDEO_PREVIEW"):
            self.still.setText("Prévia em vídeo desligada")
            return
        try:
            from PySide6.QtMultimediaWidgets import QVideoWidget
            for _ in range(2):
                w = QVideoWidget()
                w.setStyleSheet("background:#000")
                self.stack.addWidget(w)
                self._decks.append(_Deck(self, w))
            self.available = True
        except Exception as exc:  # noqa: BLE001 — no multimedia: still frames only
            self.still.setText(f"Prévia em vídeo indisponível ({exc})")

    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        if obj is self.stack and e.type() == QEvent.Resize and self._still_pm is not None:
            self.still.setPixmap(self._still_pm.scaled(self.stack.size(), Qt.KeepAspectRatio,
                                                       Qt.SmoothTransformation))
        return False

    # ------------------------------------------------------------ setup
    @property
    def widget(self):
        return self.stack

    @property
    def duration(self) -> float:
        return self.pieces[-1].film_end if self.pieces else 0.0

    def set_program(self, pieces: list[Piece], music_path: str = "", music_offset: float = 0.0) -> None:
        """What to play. Keeps the position (clamped) so edits don't jump back to 0."""
        was = self.playing
        self.pause()
        self.pieces = list(pieces)
        self.music_path, self.music_offset = music_path or "", music_offset
        self.cur = -1
        self.t = min(self.t, max(0.0, self.duration - 0.05))
        if was:
            self.play()

    def show_still(self, pm: QPixmap | None, text: str = "") -> None:
        if self.playing:
            return
        if pm is not None and not pm.isNull():
            self._still_pm = pm
            self.still.setPixmap(pm.scaled(self.stack.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.still.setPixmap(QPixmap())
            self.still.setText(text)
        self.stack.setCurrentWidget(self.still)

    # ------------------------------------------------------------ transport
    def piece_at(self, t: float) -> int:
        for i, p in enumerate(self.pieces):
            if t < p.film_end - 1e-6:
                return i
        return len(self.pieces) - 1

    def play(self, from_t: float | None = None) -> None:
        if not self.available or not self.pieces:
            return
        if from_t is not None:
            self.t = max(0.0, min(from_t, self.duration))
        if self.t >= self.duration - 0.02:
            self.t = 0.0
        self.playing = True
        self._t0 = self.t
        self._clock.restart()
        self._enter(self.piece_at(self.t), play=True)
        self._music_play()
        self._tick.start()
        self.state_changed.emit(True)

    def pause(self) -> None:
        if not self.playing:
            return
        self.playing = False
        self._tick.stop()
        for d in self._decks:
            d.pause()
        if self._music is not None:
            self._music.pause()
        self.state_changed.emit(False)
        self._ask_still()

    def toggle(self, from_t: float | None = None) -> None:
        if self.playing:
            self.pause()
        else:
            self.play(from_t)

    def seek(self, t: float) -> None:
        """Jump to film time ``t``; paused, the monitor shows that frame."""
        if not self.pieces:
            return
        self.t = max(0.0, min(t, self.duration))
        if self.playing:
            self._t0 = self.t
            self._clock.restart()
            self._enter(self.piece_at(self.t), play=True)
            self._music_play()
        else:
            self._ask_still()
        self.position_changed.emit(self.t)

    def _ask_still(self) -> None:
        """Paused frames come from a still image (exact, and never a black
        widget while a file opens); the video decks are only for playing."""
        if self.pieces:
            i = self.piece_at(self.t)
            self.still_needed.emit(self.pieces[i].path, self._src_time(i, self.t))

    def stop(self) -> None:
        self.pause()
        for d in self._decks:
            d.player.stop()
            d.path = ""
        if self._music is not None:
            self._music.stop()

    # ------------------------------------------------------------ internals
    def _src_time(self, i: int, t: float) -> float:
        p = self.pieces[i]
        return p.src_start + max(0.0, min(p.length, t - p.film_start))

    def _enter(self, i: int, play: bool) -> None:
        """Show piece ``i`` at the current film time; preload the next one."""
        if i < 0 or i >= len(self.pieces):
            return
        nxt = (self._active + 1) % 2
        deck = self._decks[self._active]
        other = self._decks[nxt]
        if other.piece == i and other.path == self.pieces[i].path and i != self.cur:
            self._active = nxt
            deck, other = other, deck
            want = self._src_time(i, self.t)
            if abs(deck.position() - want) > 0.08:
                deck.load(deck.path, want, i, play)
            elif play:
                deck.play()
        else:
            deck.load(self.pieces[i].path, self._src_time(i, self.t), i, play)
        self.stack.setCurrentWidget(deck.widget)
        other.pause()
        self.cur = i
        self.piece_changed.emit(i)
        if i + 1 < len(self.pieces):
            p = self.pieces[i + 1]
            other.load(p.path, p.src_start, i + 1, play=False)

    def _on_tick(self) -> None:
        self.t = self._t0 + self._clock.elapsed() / 1000.0
        if self.t >= self.duration:
            self.t = self.duration
            self.pause()
            self.position_changed.emit(self.t)
            return
        i = self.piece_at(self.t)
        if i != self.cur:
            self._enter(i, play=True)
        else:
            self._resync += 1
            if self._resync >= 30:                  # ~0.5 s: correct drift, gently
                self._resync = 0
                deck = self._decks[self._active]
                want = self._src_time(i, self.t)
                if abs(deck.position() - want) > 0.25:
                    deck.player.setPosition(int(want * 1000))
                if self._music is not None and self.music_path:
                    mw = self.music_offset + self.t
                    if abs(self._music.position() / 1000.0 - mw) > 0.15:
                        self._music.setPosition(int(mw * 1000))
        self.position_changed.emit(self.t)

    def _music_play(self) -> None:
        if not self.music_path or os.environ.get("DRONEHIGHLIGHTER_NO_AUDIO"):
            return
        try:
            if self._music is None:
                from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
                self._audio = QAudioOutput(self)
                self._audio.setVolume(0.8)
                self._music = QMediaPlayer(self)
                self._music.setAudioOutput(self._audio)
            if self._music.source() != QUrl.fromLocalFile(self.music_path):
                self._music.setSource(QUrl.fromLocalFile(self.music_path))
            self._music.setPosition(int((self.music_offset + self.t) * 1000))
            self._music.play()
        except Exception:  # noqa: BLE001 — no sound device: play silent
            self._music = None
            self.music_path = ""

    def set_volume(self, v: float) -> None:
        if self._audio is not None:
            self._audio.setVolume(max(0.0, min(1.0, v)))
