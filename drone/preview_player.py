# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live preview of the edit: plays a list of pieces of the raw clips as one film.

Nothing is rendered. Two video players take turns: while one shows the
current shot, the other has already opened the next one and sits paused on its
first frame, so a cut is a swap instead of a file load. A wall clock drives the
film; the music (if any) plays alongside on its own player, kept in step with
that clock. Transitions are shown as straight cuts at the right moment — the
timing is exact, the dissolves appear only in the rendered video.

Without QtMultimedia (or with ``CATAVOO_NO_VIDEO_PREVIEW`` set) the
widget is a plain still-frame monitor and :attr:`available` is False.
``CATAVOO_NO_AUDIO`` keeps it silent (no sound device needed).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import QElapsedTimer, QObject, QRectF, QSizeF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView, QSizePolicy)

VERTICAL = 9 / 16


class _Monitor(QGraphicsView):
    """One scene for everything the monitor shows: the paused still, the two
    video decks and, in 9:16, the crop frame — so the frame can sit on top of
    the playing video and be dragged sideways."""
    crop_moved = Signal(float)          # dragging: new centre (0..1 of the frame width)
    crop_done = Signal(float)           # released

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QBrush(QColor(0, 0, 0)))
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(160, 90)
        self.setStyleSheet("background:#000; border:0; border-radius:8px")
        self.aspect = 16 / 9            # of the source frame
        self.crop = None                # centre 0..1, or None = no 9:16 frame
        self.pix = QGraphicsPixmapItem()
        self.pix.setTransformationMode(Qt.SmoothTransformation)
        self.scene().addItem(self.pix)
        self.text = QGraphicsSimpleTextItem()
        self.text.setBrush(QBrush(QColor(90, 90, 90)))
        self.scene().addItem(self.text)
        self.videos: list = []
        shade = QColor(0, 0, 0, 165)
        self.masks = [QGraphicsRectItem(), QGraphicsRectItem()]
        for m in self.masks:
            m.setBrush(QBrush(shade))
            m.setPen(QPen(Qt.NoPen))
            m.setZValue(10)
            self.scene().addItem(m)
        self.frame = QGraphicsRectItem()
        self.frame.setPen(QPen(QColor(255, 255, 255), 2))
        self.frame.setBrush(Qt.NoBrush)
        self.frame.setZValue(11)
        self.scene().addItem(self.frame)
        self.caption = QGraphicsPixmapItem()      # the shot's text, same drawing as the render
        self.caption.setZValue(12)
        self.caption.setAcceptedMouseButtons(Qt.NoButton)
        self.scene().addItem(self.caption)
        self._caption = None
        self._caption_vertical = False
        self._still = None
        self._drag = None

    def add_video(self, item) -> None:
        item.setZValue(1)
        item.setVisible(False)
        self.scene().addItem(item)
        self.videos.append(item)
        item.nativeSizeChanged.connect(self._native)

    def _native(self, size) -> None:
        if size.width() > 0 and size.height() > 0:
            self.aspect = size.width() / size.height()
            self.relayout()

    def area(self) -> QRectF:
        """Where the frame is drawn: the source aspect, fitted and centred."""
        w, h = max(1, self.viewport().width()), max(1, self.viewport().height())
        if w / h > self.aspect:
            fw, fh = h * self.aspect, h
        else:
            fw, fh = w, w / self.aspect
        return QRectF((w - fw) / 2, (h - fh) / 2, fw, fh)

    def crop_rect(self) -> QRectF | None:
        if self.crop is None:
            return None
        r = self.area()
        if self.aspect <= VERTICAL:
            return QRectF(r)
        cw = r.height() * VERTICAL
        x = min(max(self.crop * r.width() - cw / 2, 0.0), r.width() - cw)
        return QRectF(r.left() + x, r.top(), cw, r.height())

    def relayout(self) -> None:
        w, h = max(1, self.viewport().width()), max(1, self.viewport().height())
        self.scene().setSceneRect(0, 0, w, h)
        r = self.area()
        for v in self.videos:
            v.setPos(r.topLeft())
            v.setSize(QSizeF(r.width(), r.height()))
        if self._still is not None:
            pm = self._still.scaled(int(r.width()), int(r.height()), Qt.KeepAspectRatio,
                                    Qt.SmoothTransformation)
            self.pix.setPixmap(pm)
            self.pix.setPos(r.left() + (r.width() - pm.width()) / 2, r.top() + (r.height() - pm.height()) / 2)
        tb = self.text.boundingRect()
        self.text.setPos((w - tb.width()) / 2, (h - tb.height()) / 2)
        c = self.crop_rect()
        on = c is not None
        for m in self.masks:
            m.setVisible(on)
        self.frame.setVisible(on)
        if on:
            self.masks[0].setRect(QRectF(r.left(), r.top(), c.left() - r.left(), r.height()))
            self.masks[1].setRect(QRectF(c.right(), r.top(), r.right() - c.right(), r.height()))
            self.frame.setRect(c.adjusted(1, 1, -1, -1))
        self.setMouseTracking(on)
        if not on:
            self.setCursor(Qt.ArrowCursor)
        self._place_caption()

    def set_caption(self, spec, vertical: bool = False) -> None:
        spec = spec or None
        if spec == self._caption and vertical == self._caption_vertical:
            return
        self._caption, self._caption_vertical = (dict(spec) if spec else None), vertical
        self._place_caption()

    def _place_caption(self) -> None:
        if not self._caption:
            self.caption.setVisible(False)
            return
        r = self.area()
        if self._caption_vertical:
            c = self.crop_rect()
            if c is None:                   # 9:16 without the frame: the centred 9:16 part
                cw = min(r.width(), r.height() * VERTICAL)
                c = QRectF(r.center().x() - cw / 2, r.top(), cw, r.height())
            r = c
        from drone import text_overlay
        img = text_overlay.draw(self._caption, int(r.width()), int(r.height()))
        self.caption.setPixmap(QPixmap.fromImage(img))
        self.caption.setPos(r.topLeft())
        self.caption.setVisible(True)

    def set_crop(self, centre) -> None:
        self.crop = None if centre is None else float(centre)
        self.relayout()

    def show_still(self, pm: QPixmap | None, text: str = "") -> None:
        if pm is not None and not pm.isNull():
            self._still = pm
            self.aspect = pm.width() / max(1, pm.height())
            self.text.setText("")
        else:
            self._still = None
            self.pix.setPixmap(QPixmap())
            self.text.setText(text)
        self.pix.setVisible(True)
        for v in self.videos:
            v.setVisible(False)
        self.relayout()

    def show_video(self, item) -> None:
        for v in self.videos:
            v.setVisible(v is item)
        self.pix.setVisible(False)
        self.text.setText("")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.relayout()

    # ---- dragging the 9:16 frame sideways
    def _centre_at(self, x: float) -> float:
        r = self.area()
        return min(1.0, max(0.0, (x - r.left()) / max(1.0, r.width())))

    def _inside_frame(self, pos) -> bool:
        c = self.crop_rect()
        return c is not None and c.contains(pos)

    def mousePressEvent(self, e):
        c = self.crop_rect()
        if c is not None and e.button() == Qt.LeftButton:
            # only the bright part (inside the frame) grabs it; the dark sides do nothing
            if self._inside_frame(e.position()):
                self._drag = e.position().x() - c.center().x()
                self.setCursor(Qt.ClosedHandCursor)
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is None and self.crop is not None:
            self.setCursor(Qt.OpenHandCursor if self._inside_frame(e.position()) else Qt.ArrowCursor)
        if self._drag is not None and self.crop is not None:
            self.crop = self._centre_at(e.position().x() - self._drag)
            self.relayout()
            self.crop_moved.emit(self.crop)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.setCursor(Qt.OpenHandCursor if self._inside_frame(e.position()) else Qt.ArrowCursor)
            if self.crop is not None:
                self.crop_done.emit(self.crop)
            return
        super().mouseReleaseEvent(e)


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
        self.widget = widget                 # a QGraphicsVideoItem in the monitor
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
        self.stack = _Monitor()              # the monitor (name kept from the widget-stack days)
        self.pieces: list[Piece] = []
        self.music_path = ""
        self.music_offset = 0.0
        self.t = 0.0
        self.playing = False
        self.loop = False                    # start over at the end (one shot, being edited)
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
        # raw clip → the file actually played (a light proxy once it exists)
        self.resolve = lambda path: path
        if os.environ.get("CATAVOO_NO_VIDEO_PREVIEW"):
            self.stack.show_still(None, "Prévia em vídeo desligada")
            return
        try:
            from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
            for _ in range(2):
                item = QGraphicsVideoItem()
                self.stack.add_video(item)
                self._decks.append(_Deck(self, item))
            self.available = True
        except Exception as exc:  # noqa: BLE001 — no multimedia: still frames only
            self.stack.show_still(None, f"Prévia em vídeo indisponível ({exc})")

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
        self.stack.show_still(pm, text)

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
        src = self.resolve(self.pieces[i].path)
        if other.piece == i and other.path == src and i != self.cur:
            self._active = nxt
            deck, other = other, deck
            want = self._src_time(i, self.t)
            if abs(deck.position() - want) > 0.08:
                deck.load(deck.path, want, i, play)
            elif play:
                deck.play()
        else:
            deck.load(src, self._src_time(i, self.t), i, play)
        self.stack.show_video(deck.widget)
        other.pause()
        self.cur = i
        self.piece_changed.emit(i)
        if i + 1 < len(self.pieces):
            p = self.pieces[i + 1]
            other.load(self.resolve(p.path), p.src_start, i + 1, play=False)

    def _on_tick(self) -> None:
        self.t = self._t0 + self._clock.elapsed() / 1000.0
        if self.t >= self.duration:
            if self.loop and self.duration > 0.05:
                self.t, self._t0 = 0.0, 0.0
                self._clock.restart()
                self.cur = -1
                self._enter(0, play=True)
                self.position_changed.emit(self.t)
                return
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
        if not self.music_path or os.environ.get("CATAVOO_NO_AUDIO"):
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
