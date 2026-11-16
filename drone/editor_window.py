# SPDX-License-Identifier: AGPL-3.0-or-later
"""Edição manual — a mini video editor for the automatic cut.

Layout (opens maximised; F11 = full screen):

* **Monitor** (top left): plays the film as it stands — the shots in order, on
  the music — without rendering anything (drone.preview_player). It also plays a
  single shot, or a whole raw clip, on demand.
* **Inspector** (top right): the selected shot, its in/out frames, the colour
  legend of the landscapes, and the log.
* **Sequência final** (middle): the film's order. Drag a card to move it,
  Delete removes it, click selects it.
* **Clipes originais** (bottom): one lane per raw clip with its filmstrip.
  Coloured, numbered blocks are the shots in the film (colour = landscape);
  grey dashed blocks are good takes the film does not use (double-click one to
  add it). Drag a block's edge to trim it, its middle to slide it. Click an
  empty spot of a lane to see that frame, double-click to play the clip there.

Switching *Estilo* shows that style's automatic cut instantly — no rendering —
and keeps your edits per style (✎ marks the edited ones).

Keyboard:
    clique / Espaço  play / pause the selected shot   duplo clique / T  it from the start
    L              loop the selected shot            C   play its raw clip
    ▶ (button)     play / pause the whole film
    ← / →          active edge ±1 frame
    Shift + ← / →  ±1 s
    [  /  ]        choose the in / out edge          Tab  toggle edge
    Alt + ← / →    slide the whole shot ±1 frame     Delete  remove the shot
    ↑ / ↓          previous / next shot              F11  full screen

The window only edits; analysis and rendering go through drone.pipeline.Session.
"""
from __future__ import annotations

import html
import os
import traceback

import numpy as np
from PySide6.QtCore import QObject, QPoint, QRectF, QRunnable, Qt, QThread, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame, QGridLayout,
                               QGraphicsItem, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMenu, QScrollArea,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QSlider,
                               QSplitter, QVBoxLayout, QWidget)

from drone.config import STYLES
from drone.edit_session import EditPlan, EditStore, fmt_time
from drone.landscape import LABEL_PT
from drone.preview_player import Piece, PreviewPlayer
from drone.theme import category_colour, role
from drone.progress_ui import Heartbeat

LABEL_W = 170          # px: clip names column
LANE_H = 64
LANE_GAP = 8
RULER_H = 26
EDGE_PX = 7            # grab zone of a shot's edge
TAGS = [("16x9", "Horizontal 16:9"), ("9x16", "Vertical 9:16")]


def _colour(category: str) -> QColor:
    """Fixed colour per landscape (same every time the program opens)."""
    return QColor(category_colour(category))


def _clock(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 60)}:{t % 60:04.1f}"


class FilmTimeline(QWidget):
    """The finished film on one line — what used to be the film bar and the
    sequence cards, together. One block per shot, as wide as its time on
    screen, with its picture, number, length and landscape colour.

    * ruler (top strip): click or drag to go anywhere in the film
    * click a block: select that shot (Espaço then plays just it)
    * double-click: play the shot from its first frame
    * drag a block sideways: change the order
    * drag the line between two blocks: one gets longer, the other shorter
    * right button: the shot's menu
    """
    seek = Signal(float)
    picked = Signal(int)
    play_shot = Signal(int)
    moved = Signal(int, int)
    roll = Signal(int, float)
    roll_done = Signal()
    menu = Signal(int, QPoint)
    delete = Signal(int)

    RULER = 18
    EDGE = 5

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(78)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.blocks: list = []          # dicts: start, length, colour, label, pix, caption
        self.total = 0.0
        self.t = 0.0
        self.sel = None
        self._hover = None
        self._mode = None
        self._press = None
        self._drop = None

    # -- data
    def set_blocks(self, blocks: list, total: float, sel=None) -> None:
        self.blocks, self.total, self.sel = blocks, max(0.0, total), sel
        self.update()

    def set_time(self, t: float) -> None:
        self.t = t
        self.update()

    def _x(self, t: float) -> float:
        return 8 + (self.width() - 16) * (t / self.total if self.total > 0 else 0.0)

    def _t(self, x: float) -> float:
        w = max(1, self.width() - 16)
        return max(0.0, min(self.total, (x - 8) / w * self.total))

    def _block_at(self, x: float):
        for i, b in enumerate(self.blocks):
            if self._x(b["start"]) <= x < self._x(b["start"] + b["length"]):
                return i
        return len(self.blocks) - 1 if self.blocks and x >= self._x(self.total) else None

    def _cut_at(self, x: float):
        """Index i of the cut between blocks i and i+1 under ``x``, if any."""
        for i, b in enumerate(self.blocks[:-1]):
            if abs(self._x(b["start"] + b["length"]) - x) <= self.EDGE:
                return i
        return None

    def _slot(self, x: float) -> int:
        for i, b in enumerate(self.blocks):
            if x < self._x(b["start"] + b["length"] / 2):
                return i
        return len(self.blocks)

    # -- drawing
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(10, 10, 10))
        top, h = self.RULER + 2, max(20, self.height() - self.RULER - 6)
        f = p.font()
        f.setPointSizeF(7.5)
        p.setFont(f)
        p.fillRect(QRectF(0, 0, self.width(), self.RULER), QColor(22, 22, 22))
        if self.total > 0:
            step = next(s for s in (1, 2, 5, 10, 15, 30, 60, 120) if (self.width() - 16) * s / self.total >= 48)
            p.setPen(QColor(110, 110, 110))
            k = 0.0
            while k <= self.total + 1e-6:
                x = self._x(k)
                p.drawLine(int(x), self.RULER - 5, int(x), self.RULER)
                p.drawText(int(x) + 3, 11, f"{int(k // 60)}:{int(k % 60):02d}")
                k += step
        f.setPointSizeF(8)
        f.setBold(True)
        p.setFont(f)
        for i, b in enumerate(self.blocks):
            x0, x1 = self._x(b["start"]), self._x(b["start"] + b["length"])
            r = QRectF(x0 + 0.5, top, max(1.0, x1 - x0 - 1), h)
            p.save()
            p.setClipRect(r)
            p.fillRect(r, QColor(30, 30, 30))
            pix = b.get("pix")
            if pix is not None and not pix.isNull():
                ph = int(h)
                pw = max(1, int(pix.width() * ph / max(1, pix.height())))
                sp = pix.scaled(pw, ph, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = r.left()
                while x < r.right():
                    p.drawPixmap(int(x), int(top), sp)
                    x += sp.width()
            if i != self.sel:
                p.fillRect(r, QColor(0, 0, 0, 70))
            c = QColor(b["colour"])
            p.fillRect(QRectF(r.left(), r.bottom() - 5, r.width(), 5), c)
            label = b["label"]
            tw = 0
            if r.width() > 26:
                tw = p.fontMetrics().horizontalAdvance(label) + 8
                p.fillRect(QRectF(r.left(), top, min(tw, r.width()), 16), QColor(0, 0, 0, 170))
                p.setPen(QColor(255, 255, 255))
                p.drawText(QRectF(r.left() + 4, top, r.width() - 6, 16), Qt.AlignVCenter | Qt.AlignLeft, label)
            if b.get("caption") and r.width() > tw + 16:
                tb = QRectF(r.left() + tw + 2, top + 1, 14, 14)
                p.fillRect(tb, QColor(250, 200, 30))
                p.setPen(QColor(10, 10, 10))
                p.drawText(tb, Qt.AlignCenter, "T")
            p.restore()
            if i == self.sel:
                p.setPen(QPen(QColor(255, 255, 255), 2))
                p.setBrush(Qt.NoBrush)
                p.drawRect(r.adjusted(1, 1, -1, -1))
        if self._mode == "drag" and self._drop is not None and self.blocks:
            x = self._x(self.blocks[self._drop]["start"]) if self._drop < len(self.blocks) else self._x(self.total)
            p.setPen(QPen(QColor(250, 200, 30), 3))
            p.drawLine(int(x), top - 2, int(x), top + h + 2)
        elif self._hover is not None and self._mode is None:
            cut = self._cut_at(self._hover)
            if cut is not None:
                x = self._x(self.blocks[cut]["start"] + self.blocks[cut]["length"])
                p.setPen(QPen(QColor(250, 200, 30), 2))
                p.drawLine(int(x), top, int(x), top + h)
        x = self._x(self.t)
        p.setPen(QPen(QColor(255, 80, 80), 2))
        p.drawLine(int(x), 4, int(x), top + h + 2)
        p.setBrush(QColor(255, 80, 80))
        p.drawEllipse(QRectF(x - 4, 2, 8, 8))
        p.end()

    # -- mouse
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or self.total <= 0:
            return
        x, y = e.position().x(), e.position().y()
        if y < self.RULER:
            self._mode = "seek"
            self.seek.emit(self._t(x))
            return
        cut = self._cut_at(x)
        if cut is not None:
            self._mode, self._press = "roll", (cut, x)
            return
        i = self._block_at(x)
        if i is not None:
            self._mode, self._press = "press", (i, x)

    def mouseMoveEvent(self, e):
        x = e.position().x()
        self._hover = x
        if self._mode == "seek":
            self.seek.emit(self._t(x))
        elif self._mode == "roll":
            i, x0 = self._press
            dt = (x - x0) / max(1.0, self.width() - 16) * self.total
            if dt:
                self.roll.emit(i, dt)
                self._press = (i, x)
        elif self._mode == "press" and abs(x - self._press[1]) > 6:
            self._mode = "drag"
            self.setCursor(Qt.ClosedHandCursor)
        if self._mode == "drag":
            self._drop = self._slot(x)
        elif self._mode is None:
            y = e.position().y()
            if y < self.RULER:
                self.setCursor(Qt.PointingHandCursor)
            elif self._cut_at(x) is not None:
                self.setCursor(Qt.SizeHorCursor)
            else:
                self.setCursor(Qt.OpenHandCursor if self._block_at(x) is not None else Qt.ArrowCursor)
        self.update()

    def mouseReleaseEvent(self, e):
        mode, press, drop = self._mode, self._press, self._drop
        self._mode = self._press = self._drop = None
        self.setCursor(Qt.ArrowCursor)
        if mode == "press":
            self.picked.emit(press[0])
        elif mode == "drag" and drop is not None:
            src = press[0]
            dst = drop - 1 if drop > src else drop
            dst = max(0, min(len(self.blocks) - 1, dst))
            if dst != src:          # after the release returns: the editor rebuilds
                QTimer.singleShot(0, lambda: self.moved.emit(src, dst))
        elif mode == "roll":
            self.roll_done.emit()
        self.update()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().y() >= self.RULER:
            i = self._block_at(e.position().x())
            if i is not None:
                self.play_shot.emit(i)

    def contextMenuEvent(self, e):
        i = self._block_at(e.pos().x())
        if i is not None and e.pos().y() >= self.RULER:
            self.menu.emit(i, e.globalPos())

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.sel is not None:
            self.delete.emit(self.sel)
            return
        super().keyPressEvent(e)

    def leaveEvent(self, _e):
        self._hover = None
        self.update()


def _pixmap_from_jpeg(data: bytes) -> QPixmap:
    pm = QPixmap()
    pm.loadFromData(data, "JPG")
    return pm


def _bgr_to_qimage(bgr: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(bgr[..., ::-1])
    h, w, _ = rgb.shape
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


# ------------------------------------------------------------- frame grabs
class _FrameSignals(QObject):
    ready = Signal(str, object)          # key, QImage


class _FrameJob(QRunnable):
    def __init__(self, key: str, path: str, t: float, signals: _FrameSignals):
        super().__init__()
        self.key, self.path, self.t, self.signals = key, path, t, signals

    def run(self):
        try:
            from drone.media_io import grab_frame
            bgr = grab_frame(self.path, self.t, width=getattr(self, "width", 480))
            if bgr is not None:
                self.signals.ready.emit(self.key, _bgr_to_qimage(bgr))
        except Exception:
            pass


# ------------------------------------------------------------- proxies
_KEEP_ALIVE: list = []
class _ProxySignals(QObject):
    ready = Signal(str, str)             # raw clip, proxy


class _ProxyJob(QRunnable):
    def __init__(self, path: str, content_hash: str, signals: _ProxySignals, cancelled):
        super().__init__()
        self.path, self.hash, self.signals, self.cancelled = path, content_hash, signals, cancelled

    def run(self):
        if self.cancelled():
            return
        try:
            from drone import proxy
            out = proxy.make(self.path, self.hash, cancel_check=self.cancelled)
            if out:
                self.signals.ready.emit(self.path, out)
        except Exception:
            pass


# ------------------------------------------------------------- render worker
class _RenderWorker(QObject):
    log = Signal(str)
    progress = Signal(float, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, session, gp, plan, tag):
        super().__init__()
        self.session, self.gp, self.plan, self.tag = session, gp, plan, tag
        self.cancelled = False

    def run(self):
        old = self.session.log_fn
        self.session.set_log(self.log.emit)
        try:
            def prog(i, n, stage="", *a):
                # two passes inside: cut every shot, then join them with the music.
                # One bar: cutting is the first half, joining the second.
                frac = i / max(1, n)
                if str(stage).lower().startswith(("cut", "vertical")):
                    self.progress.emit(50.0 * frac, f"Cortando as tomadas ({i + 1}/{max(1, n - 1)})")
                else:
                    self.progress.emit(50.0 + 50.0 * frac, "Juntando as tomadas com a música e as transições")
            out = self.session.render(self.gp, self.plan, self.tag, progress_fn=prog,
                                      cancel_check=lambda: self.cancelled)
            self.finished.emit(out)
        except Exception as exc:  # noqa: BLE001 — shown to the user
            self.log.emit(traceback.format_exc())
            self.failed.emit(str(exc))
        finally:
            self.session.set_log(old)


class _RedoWorker(QObject):
    """New wishes (and a new Foco, which may run CLIP) off the UI thread."""
    log = Signal(str)
    progress = Signal(float, str)
    finished = Signal(object, str)
    failed = Signal(str)

    def __init__(self, session, feedback: str, variation: int, previous: list, focus):
        super().__init__()
        self.session, self.feedback, self.variation = session, feedback, variation
        self.previous, self.focus = previous, focus

    def run(self):
        try:
            adj = self.session.readjust(self.feedback, self.variation, self.previous)
            msg = ""
            if self.focus is not None:
                msg = self.session.set_focus(
                    self.focus, progress_fn=lambda i, n, name: self.progress.emit(
                        100.0 * i / max(1, n), f"Procurando “{self.focus}” em {name}"))
            self.finished.emit(adj, msg)
        except Exception as exc:  # noqa: BLE001 — shown to the user
            self.log.emit(traceback.format_exc())
            self.failed.emit(str(exc))


class _CapCutWorker(QObject):
    log = Signal(str)
    progress = Signal(float, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, plan, kwargs):
        super().__init__()
        self.plan, self.kwargs = plan, kwargs
        self.cancelled = False

    def run(self):
        try:
            from drone import capcut_export
            folder = capcut_export.export(
                self.plan, log_fn=self.log.emit, cancel_check=lambda: self.cancelled,
                progress_fn=lambda i, n: self.progress.emit(100.0 * i / max(1, n), "CapCut"),
                **self.kwargs)
            self.finished.emit(folder)
        except Exception as exc:  # noqa: BLE001 — shown to the user
            self.log.emit(traceback.format_exc())
            self.failed.emit(str(exc))


# ------------------------------------------------------------- timeline items
class ShotItem(QGraphicsRectItem):
    """A coloured, numbered block: trim at the edges, slide in the middle."""

    def __init__(self, editor, index: int, rect: QRectF, colour: QColor, label: str):
        super().__init__(rect)
        self.editor, self.index = editor, index
        fill = QColor(colour)
        fill.setAlpha(120)                      # the filmstrip shows through
        self.setBrush(QBrush(fill))
        self.setPen(QPen(colour, 2))
        self.setZValue(3)
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self._mode = None
        self._press_x = 0.0
        t = QGraphicsSimpleTextItem(label, self)
        f = QFont()
        f.setBold(True)
        f.setPointSize(9)
        t.setFont(f)
        t.setPos(rect.x() + 5, rect.y() + 3)
        t.setBrush(QBrush(QColor(255, 255, 255)))
        tag = QGraphicsRectItem(rect.x() + 2, rect.y() + 2, t.boundingRect().width() + 7, 16, self)
        tag.setBrush(QBrush(colour))
        tag.setPen(QPen(Qt.NoPen))
        tag.setZValue(-1)
        tag.setAcceptedMouseButtons(Qt.NoButton)
        t.setAcceptedMouseButtons(Qt.NoButton)
        t.setBrush(QBrush(QColor(10, 10, 10)))

    def _zone(self, x: float) -> str:
        r = self.rect()
        if x - r.left() <= EDGE_PX:
            return "start"
        if r.right() - x <= EDGE_PX:
            return "end"
        return "body"

    def hoverMoveEvent(self, e):
        z = self._zone(e.pos().x())
        self.setCursor(QCursor(Qt.SizeHorCursor if z != "body" else Qt.OpenHandCursor))

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        self._mode = self._zone(e.pos().x())
        self._press_x = self._x0 = e.scenePos().x()
        self._moved = False
        self.editor.select(self.index, edge=self._mode if self._mode != "body" else None)
        e.accept()

    def mouseMoveEvent(self, e):
        if self._mode is None:
            return
        x = e.scenePos().x()
        if not self._moved and abs(x - self._x0) < 4:     # a hand's tremble is still a click
            return
        self._moved = True
        if self._mode == "body":
            dt = (x - self._press_x) / self.editor.pps
            if abs(dt) > 0:
                self.editor.drag_slide(self.index, dt)
                self._press_x = x
        else:
            self.editor.drag_edge(self.index, self._mode, self.editor.x_to_time(x))
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._mode is not None:
            self._mode = None
            if getattr(self, "_moved", False):
                # later, not now: the rebuild deletes this very item while Qt is still inside its handler
                QTimer.singleShot(0, self.editor.commit_drag)
            else:                                   # a click: play / pause this shot (like Espaço)
                i = self.index
                QTimer.singleShot(0, lambda: self.editor.click_shot(i))
        e.accept()

    def mouseDoubleClickEvent(self, e):
        """Double-click: play this shot once, from its first frame."""
        if e.button() == Qt.LeftButton:
            i = self.index
            QTimer.singleShot(0, lambda: self.editor.play_shot(i))
        e.accept()

    def contextMenuEvent(self, e):
        i, pos = self.index, e.screenPos()
        QTimer.singleShot(0, lambda: self.editor.shot_menu(i, pos))


class CandidateItem(QGraphicsRectItem):
    """A grey block: a candidate the analysis found but the film does not use."""

    def __init__(self, editor, cand: dict, rect: QRectF):
        super().__init__(rect)
        self.editor, self.cand = editor, cand
        self.setBrush(QBrush(QColor(200, 200, 200, 60)))
        self.setPen(QPen(QColor(200, 200, 200), 1.2, Qt.DashLine))
        self.setZValue(2)
        p = cand.get("parts", {})
        self.setToolTip(f"Candidato {cand['uid']} — {LABEL_PT.get(cand['category'], cand['category'])}, "
                        f"{cand['movement']}\nnota {cand['score']:.2f} · qualidade {p.get('quality', 0):.2f} · "
                        f"suavidade {p.get('smoothness', 0):.2f}\nDuplo clique: adicionar depois da tomada "
                        f"selecionada · botão direito: mais opções")

    def mouseDoubleClickEvent(self, e):
        cand = self.cand
        QTimer.singleShot(0, lambda: self.editor.add_candidate(cand))

    def contextMenuEvent(self, e):
        cand, pos = self.cand, e.screenPos()
        QTimer.singleShot(0, lambda: self.editor.candidate_menu(cand, pos))


class TimelineView(QGraphicsView):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setDragMode(QGraphicsView.NoDrag)

    def keyPressEvent(self, e):
        if not self.editor.on_key(e):
            super().keyPressEvent(e)

    def _blank(self, e) -> bool:
        """No shot or candidate block under the mouse (overlays don't count)."""
        pos = e.position().toPoint() if hasattr(e, "position") else e.pos()
        for it in self.items(pos):
            while it is not None:
                if isinstance(it, (ShotItem, CandidateItem)):
                    return False
                it = it.parentItem()
        return True

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() == Qt.LeftButton and self._blank(e):
            self.editor.lane_click(self.mapToScene(e.position().toPoint()), play=False)
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and self._blank(e):
            self.editor.lane_click(self.mapToScene(e.position().toPoint()), play=True)
            return
        super().mouseDoubleClickEvent(e)


# ------------------------------------------------------------- the window
class EditorWindow(QMainWindow):
    rendered = Signal(object)

    def __init__(self, session, *, style_key: str, tag: str, store: EditStore, title: str = "",
                 main_style: str = "", target: float = 0.0, parent=None):
        super().__init__(parent)
        self.session, self.store, self.title_text = session, store, title
        self.main_style = main_style or style_key
        self._first = (tag, float(target or 0.0))   # the length chosen after the analysis
        self.variation = 0
        self.setWindowTitle("CataVoo — Prévia e edição")
        self.resize(1400, 900)
        self.plans: dict = {}               # (group, style, tag) → EditPlan
        self.preps: dict = {}
        self.plan: EditPlan | None = None
        self.gp = None
        self.sel: int | None = None
        self.edge = "end"
        self.pps = 12.0
        self._thumbs: dict = {}
        self._scaled: dict = {}
        self._frames: dict = {}
        self._pool = QThreadPool(self)          # own pool: closing waits for its frame grabs
        self._pool.setMaxThreadCount(3)         # a dozen ffmpeg at once on 4K footage froze the PC
        self._still_want = None                 # newest monitor frame asked for (path, t)
        self._still_timer = QTimer(self, singleShot=True, interval=45)
        self._still_timer.timeout.connect(self._grab_still)
        self._fsig = _FrameSignals()
        self._fsig.ready.connect(self._frame_ready)
        self._preview_timer = QTimer(self, singleShot=True, interval=140)
        self._preview_timer.timeout.connect(self._request_frames)
        self._render_thread = None
        self._render_worker = None
        session.set_log(self.log)

        self.targets: dict = {}              # (group, format) → film length chosen here, seconds
        self._hist: dict = {}                # (group, style, format) → undo/redo snapshots
        self._focus_text = ""                # the Foco in force (set after a Refazer)
        self._redo_thread = None
        self._redo_worker = None
        self._redo_busy = False
        self.mode = "seq"                    # what the monitor plays: seq | shot | clip
        self._proxies: dict = {}             # raw clip → light copy for the preview
        self._proxy_queued: set = set()
        self._proxy_pool = QThreadPool(self)
        self._proxy_pool.setMaxThreadCount(1)  # one at a time: the PC stays responsive
        self._proxy_sig = _ProxySignals()
        _KEEP_ALIVE.append(self._proxy_sig)    # a proxy may finish after the window is gone
        self._proxy_sig.ready.connect(self._proxy_ready)
        self._closing = False
        self.lane_y: dict = {}
        self.playhead = None

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)

        # ---- top bar
        top = QHBoxLayout()
        top.setSpacing(8)
        self.group_box = QComboBox()
        for g in session.groups:
            self.group_box.addItem(g.label, g.key)
        many = len(session.groups) > 1
        glbl = role(QLabel("Grupo"), "muted")
        glbl.setVisible(many)
        self.group_box.setVisible(many)
        top.addWidget(glbl)
        top.addWidget(self.group_box)
        top.addWidget(role(QLabel("Estilo"), "muted"))
        self.style_box = QComboBox()
        self.style_box.setMinimumWidth(170)
        self.style_box.setMaximumWidth(260)
        top.addWidget(self.style_box)
        top.addWidget(role(QLabel("Formato"), "muted"))
        self.tag_box = QComboBox()
        for k, lbl in TAGS:
            self.tag_box.addItem(lbl, k)
        self.tag_box.setCurrentIndex(max(0, self.tag_box.findData(tag)))
        top.addWidget(self.tag_box)
        top.addWidget(role(QLabel("Duração"), "muted"))
        self.dur_box = QComboBox()
        self.dur_box.setEditable(True)
        self.dur_box.setInsertPolicy(QComboBox.NoInsert)
        self.dur_box.lineEdit().editingFinished.connect(self._duration_typed)
        self.dur_box.setMinimumWidth(110)
        self.dur_box.setToolTip("Remonta o vídeo neste tamanho a partir da mesma análise, sem processar nada de novo.\n"
                                "Menor: ficam as tomadas mais bem avaliadas. Maior: entram mais tomadas e elas esticam.\n"
                                "Escolha na lista ou digite o tempo que quiser (90, 1:30, 4:00…) e tecle Enter.\n"
                                "Vale para todos os estilos deste formato.")
        self.dur_box.activated.connect(self._duration_picked)
        top.addWidget(self.dur_box)
        top.addWidget(role(QLabel("Música"), "muted"))
        self.music_box = QComboBox()
        self.music_box.setMinimumWidth(140)
        self.music_box.setMaximumWidth(230)
        self.music_box.setToolTip("Escolha a música do vídeo entre as faixas da sua biblioteca.\n"
                                  "Ela toca do começo ao fim do vídeo e é cortada no final com o volume abaixando.\n"
                                  "Automática: o programa escolhe pelo estilo.")
        self._fill_music()
        self.music_box.activated.connect(self._music_picked)
        top.addWidget(self.music_box)
        self.text_btn = QPushButton("T  Texto")
        self.text_btn.setToolTip("Escreve um texto sobre a tomada selecionada, durante toda a tomada.\n"
                                 "Caixa à esquerda ou centralizada, fonte, cores e fundo preenchido ou transparente.")
        self.text_btn.clicked.connect(self.edit_text)
        top.addWidget(self.text_btn)
        self.version_btn = QPushButton("✦ Nova versão")
        self.version_btn.setToolTip("Gera outra versão do vídeo a partir da mesma análise.\n"
                                    "Abre uma janela para escrever o que mudar (mais cachoeira, cortes mais rápidos…)\n"
                                    "e escolher um Foco. Ctrl+Z volta à versão anterior.")
        self.version_btn.clicked.connect(self.open_version)
        top.addWidget(self.version_btn)
        self.material_lbl = role(QLabel(""), "muted")
        self.material_lbl.setToolTip("Bruto: tudo o que foi filmado. Bom: tomadas distintas no ritmo normal do estilo.\n"
                                     "Esticando: o máximo sem repetir cenas.")
        top.addSpacing(10)
        self._top = top                         # Ajustes, Foco, Refazer, Outra versão and the outputs follow
        self.total_lbl = QLabel()
        f = QFont()
        f.setBold(True)
        self.total_lbl.setFont(f)
        lay.addLayout(top)

        # ---- monitor + inspector
        split = QSplitter(Qt.Vertical)
        upper = QSplitter(Qt.Horizontal)

        mon = QWidget()
        ml = QVBoxLayout(mon)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(6)
        mh = QHBoxLayout()
        self.monitor_title = role(QLabel("SEQUÊNCIA FINAL"), "section")
        self.proxy_lbl = role(QLabel(""), "muted")
        self.proxy_lbl.setToolTip("Cópias leves (540p) dos vídeos, só para a prévia rodar lisa.\n"
                                  "Feitas uma vez por vídeo; o vídeo final usa sempre os originais.")
        mh.addWidget(self.monitor_title)
        mh.addStretch()
        mh.addWidget(self.proxy_lbl)
        ml.addLayout(mh)
        self.preview = PreviewPlayer(self)
        self.preview.resolve = self._play_source
        self._can_play = self.preview.available
        self.video = self.preview.widget if self._can_play else None
        ml.addWidget(self.preview.widget, 1)
        self.scrub = FilmTimeline()
        self.scrub.setToolTip("O vídeo final. Régua de tempo: clique ou arraste para ir a qualquer ponto (Espaço toca o vídeo todo).\n"
                              "Tomada: clique seleciona (Espaço toca só ela) · duplo clique toca · arraste para reordenar\n"
                              "Linha entre duas tomadas: arraste — uma aumenta e a outra diminui · botão direito: menu")
        self.film = self.scrub
        self.scrub.seek.connect(self._film_seek)
        self.scrub.picked.connect(self._film_picked)
        self.scrub.play_shot.connect(self.play_shot)
        self.scrub.moved.connect(self._strip_moved)
        self.scrub.roll.connect(self._film_roll)
        self.scrub.roll_done.connect(self._changed)
        self.scrub.menu.connect(self.shot_menu)
        self.scrub.delete.connect(self.remove)
        tr = QGridLayout()
        tr.setHorizontalSpacing(8)
        self.prev_btn = role(QPushButton("⏮"), "icon")
        self.prev_btn.setToolTip("Tomada anterior")
        self.play_seq_btn = role(QPushButton("▶"), "icon")
        self.play_seq_btn.setToolTip("Tocar / pausar o vídeo final inteiro")
        self.next_btn = role(QPushButton("⏭"), "icon")
        self.next_btn.setToolTip("Próxima tomada")
        self.stop_btn = role(QPushButton("■"), "icon")
        self.stop_btn.setToolTip("Parar e voltar ao início")
        self.play_btn = QPushButton("Tocar tomada")
        self.play_btn.setToolTip("A tomada selecionada, do início (T ou duplo clique). "
                                 "Clique nela ou Espaço: tocar / pausar. L: em loop.")
        self.clip_btn = QPushButton("Tocar clipe original")
        self.clip_btn.setToolTip("O arquivo bruto inteiro da tomada selecionada (C)")
        self.time_lbl = role(QLabel("0:00.0 / 0:00.0"), "muted")
        self.music_chk = QCheckBox("Música")
        self.music_chk.setChecked(True)
        self.music_chk.setToolTip("Tocar a música junto na prévia da sequência")
        left, mid, right = QHBoxLayout(), QHBoxLayout(), QHBoxLayout()
        left.setSpacing(10)
        mid.setSpacing(8)
        right.setSpacing(8)
        left.addWidget(self.time_lbl)
        left.addWidget(role(QLabel("·"), "muted"))
        left.addWidget(self.total_lbl)
        left.addStretch()
        for w in (self.stop_btn, self.prev_btn, self.play_seq_btn, self.next_btn):
            mid.addWidget(w)
        right.addStretch()
        right.addWidget(self.music_chk)
        right.addWidget(self.play_btn)
        right.addWidget(self.clip_btn)
        self._transport_right = right           # undo / redo / save / more join here
        tr.addLayout(left, 0, 0)
        tr.addLayout(mid, 0, 1)
        tr.addLayout(right, 0, 2)
        tr.setColumnStretch(0, 1)
        tr.setColumnStretch(2, 1)
        ml.addLayout(tr)
        for w in (self.play_seq_btn, self.play_btn, self.clip_btn, self.stop_btn):
            w.setEnabled(self._can_play)
        self.stop_btn.clicked.connect(self.stop)
        self.prev_btn.clicked.connect(lambda: (self._space_film(), self._step(-1)))
        self.next_btn.clicked.connect(lambda: (self._space_film(), self._step(1)))
        self.play_seq_btn.clicked.connect(lambda: (self._space_film(), self.play_sequence()))
        self.play_btn.clicked.connect(self.play)
        self.clip_btn.clicked.connect(self.play_clip)
        self.music_chk.toggled.connect(lambda _on: self._load_program(keep=True))
        self.preview.position_changed.connect(self._play_pos)
        self.preview.piece_changed.connect(self._play_piece)
        self.preview.state_changed.connect(self._play_state)
        self.preview.still_needed.connect(self._want_still)
        self.preview.stack.crop_moved.connect(self._crop_moved)
        self.preview.stack.crop_done.connect(self._crop_done)
        self._crop_i = None
        upper.addWidget(mon)

        side = role(QFrame(), "card")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 12, 14, 12)
        sl.setSpacing(8)
        sl.addWidget(role(QLabel("MATERIAL"), "section"))
        self.material_lbl.setWordWrap(True)
        sl.addWidget(self.material_lbl)
        sl.addSpacing(4)
        sl.addWidget(role(QLabel("TOMADA SELECIONADA"), "section"))
        self.info = QLabel("Selecione uma tomada colorida.")
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.RichText)
        self.info.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)   # never cut the text
        sl.addWidget(self.info)
        frames = QHBoxLayout()
        self.in_img, self.out_img = QLabel(), QLabel()
        for lbl, cap in ((self.in_img, "Entrada"), (self.out_img, "Saída")):
            box = QVBoxLayout()
            t = role(QLabel(cap), "muted")
            lbl.setFixedSize(132, 74)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("background:#000; border-radius:6px")
            box.addWidget(t)
            box.addWidget(lbl)
            frames.addLayout(box)
        frames.addStretch()
        sl.addLayout(frames)
        self.crop_reset_btn = role(QPushButton("Enquadramento automático"), "ghost")
        self.crop_reset_btn.setToolTip("Volta o recorte 9:16 desta tomada para onde o programa tinha posto")
        self.crop_reset_btn.clicked.connect(self.reset_crop)
        self.crop_reset_btn.setVisible(False)
        sl.addWidget(self.crop_reset_btn, 0, Qt.AlignLeft)
        sl.addWidget(role(QLabel("PAISAGENS"), "section"))
        self.legend = QLabel()
        self.legend.setWordWrap(True)
        self.legend.setTextFormat(Qt.RichText)
        sl.addWidget(self.legend)
        sl.addWidget(role(QLabel("REGISTRO"), "section"))
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        sl.addWidget(self.logbox, 1)
        side_scroll = QScrollArea()             # the panel scrolls when the monitor is short
        side_scroll.setWidget(side)
        side_scroll.setWidgetResizable(True)
        side_scroll.setFrameShape(QFrame.NoFrame)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        side_scroll.setMinimumWidth(330)
        side_scroll.setMaximumWidth(400)        # the monitor gets the rest
        self.logbox.setMinimumHeight(90)
        upper.addWidget(side_scroll)
        upper.setStretchFactor(0, 5)
        upper.setStretchFactor(1, 1)
        upper.setChildrenCollapsible(False)
        self._upper = upper
        split.addWidget(upper)

        # ---- sequence strip
        seq = QWidget()
        ql = QVBoxLayout(seq)
        ql.setContentsMargins(0, 0, 0, 0)
        ql.setSpacing(4)
        ql.addWidget(role(QLabel("SEQUÊNCIA  ·  arraste para reordenar  ·  arraste a linha entre duas tomadas "
                                 "para ajustar as duas  ·  botão direito: dividir, texto, transição"), "section"))
        ql.addWidget(self.scrub, 1)
        split.addWidget(seq)

        # ---- timeline of the raw clips
        tl = QWidget()
        tll = QVBoxLayout(tl)
        tll.setContentsMargins(0, 0, 0, 0)
        tll.setSpacing(4)
        thead = QHBoxLayout()
        thead.addWidget(role(QLabel("CLIPES ORIGINAIS  ·  cinza = trecho bom não usado (duplo clique adiciona)"),
                             "section"))
        thead.addStretch()
        thead.addWidget(role(QLabel("Zoom"), "muted"))
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(3, 120)
        self.zoom.setValue(12)
        self.zoom.setFixedWidth(160)
        thead.addWidget(self.zoom)
        fit = role(QPushButton("Ajustar"), "ghost")
        fit.setToolTip("Zoom para caber o vídeo mais longo na largura")
        fit.clicked.connect(lambda: self.fit_zoom())
        thead.addWidget(fit)
        tll.addLayout(thead)
        self.scene = QGraphicsScene()
        self.scene.setBackgroundBrush(QBrush(QColor(10, 10, 10)))
        self.view = TimelineView(self)
        self.view.setScene(self.scene)
        tll.addWidget(self.view, 1)
        split.addWidget(tl)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 0)
        split.setStretchFactor(2, 4)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(7)
        upper.setHandleWidth(7)
        split.splitterMoved.connect(lambda *_: self._save_layout())
        upper.splitterMoved.connect(lambda *_: self._save_layout())
        self._split = split
        self._sized = False
        lay.addWidget(split, 1)

        # ---- bottom buttons
        bot = QHBoxLayout()
        self.undo_btn = QPushButton("↶")
        self.undo_btn.setToolTip("Desfazer: desfaz a última mudança: apagar, mover, aparar, trocar transição, enquadrar,\n"
                                 "Nova versão, Duração… (Ctrl+Z)")
        self.undo_btn.clicked.connect(self.undo)
        self.redo_edit_btn = QPushButton("↷")
        self.redo_edit_btn.setToolTip("Refazer edição: volta o que foi desfeito (Ctrl+Y ou Ctrl+Shift+Z)")
        for b in (self.undo_btn, self.redo_edit_btn):
            b.setFixedWidth(40)
            f = b.font()
            f.setPointSizeF(f.pointSizeF() * 1.3)
            b.setFont(f)
        self.redo_edit_btn.clicked.connect(self.redo_edit)
        self.more_btn = QPushButton("Mais")
        self.more_btn.setToolTip("Mais opções")
        more = QMenu(self.more_btn)
        self.restore_act = more.addAction("Restaurar automático (descarta as edições deste estilo e formato)")
        self.more_btn.setMenu(more)
        self.save_btn = QPushButton("Salvar")
        self.save_btn.setToolTip("Salva a edição deste estilo e formato")
        self.capcut_btn = QPushButton("Exportar")
        self.capcut_btn.setToolTip("Outras saídas além do vídeo renderizado")
        exp = QMenu(self.capcut_btn)
        self.capcut_act = exp.addAction("Enviar para o CapCut")
        self.capcut_act.setToolTip("Corta cada tomada no quadro exato, numera na ordem do vídeo, junta a música\n"
                                   "e abre a pasta (e o CapCut, se estiver instalado) para o acabamento.")
        exp.setToolTipsVisible(True)
        self.capcut_btn.setMenu(exp)
        self.render_btn = role(QPushButton("RENDERIZAR"), "primary")
        self.render_btn.setMinimumHeight(34)
        self.render_btn.setMinimumWidth(130)
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setVisible(False)       # only while something is running
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setProperty("role", "thick")
        self.bar_status = role(QLabel(""), "muted")
        self.bar_status.setWordWrap(True)
        self.bar_pct = QLabel("")
        bf = self.bar_pct.font()
        bf.setBold(True)
        self.bar_pct.setFont(bf)
        self.beat = Heartbeat(self.bar, self.bar_status, self.bar_pct, self)
        # the request and the Foco in force (edited in the Nova versão window, never shown in the bar)
        self.feedback = QLineEdit()
        self.focus_box = QComboBox()
        self.focus_box.setEditable(True)
        self.focus_box.setInsertPolicy(QComboBox.NoInsert)
        self._top.addStretch(1)                 # the request and the Foco live in the Nova versão window
        self._transport_right.addSpacing(10)
        for w in (self.undo_btn, self.redo_edit_btn, self.save_btn, self.more_btn):
            self._transport_right.addWidget(w)
        bot = QHBoxLayout()                     # under the top bar: the progress
        bbox = QVBoxLayout()
        bbox.setSpacing(2)
        brow = QHBoxLayout()
        brow.addWidget(self.bar, 1)
        brow.addWidget(self.bar_pct)
        bbox.addLayout(brow)
        bbox.addWidget(self.bar_status)
        bot.addLayout(bbox, 1)
        lay.insertLayout(1, bot)
        # the outputs sit at the right end of the top bar
        top.addSpacing(10)
        top.addWidget(self.cancel_btn)
        top.addWidget(self.capcut_btn)
        top.addWidget(self.render_btn)
        self.setCentralWidget(root)

        self.restore_act.triggered.connect(self.restore)
        self.save_btn.clicked.connect(self.save)
        self.render_btn.clicked.connect(self.render)
        self.capcut_act.triggered.connect(self.send_to_capcut)
        self.cancel_btn.clicked.connect(self._cancel_render)
        self.zoom.valueChanged.connect(self._zoom)

        self._fill_styles(style_key)
        self.style_box.currentIndexChanged.connect(lambda _i: self.switch())
        self.tag_box.currentIndexChanged.connect(lambda _i: self.switch())
        self.group_box.currentIndexChanged.connect(lambda _i: self.switch())
        self.switch()
        QTimer.singleShot(0, self.fit_zoom)

    def showEvent(self, e):
        super().showEvent(e)
        if not self._sized:
            self._sized = True
            QTimer.singleShot(0, self._initial_sizes)

    def _layout_settings(self):
        from PySide6.QtCore import QSettings
        return QSettings("CataVoo", "CataVoo")

    def _save_layout(self) -> None:
        """Remember where the user left the dividers (monitor / sequence / clips, monitor / panel)."""
        if not self._sized or os.environ.get("CATAVOO_NO_LAYOUT_MEMORY"):
            return
        st = self._layout_settings()
        st.setValue("editor/split2", self._split.saveState())
        st.setValue("editor/upper", self._upper.saveState())

    def _restore_layout(self) -> bool:
        if os.environ.get("CATAVOO_NO_LAYOUT_MEMORY"):
            return False
        st = self._layout_settings()
        split, upper = st.value("editor/split2"), st.value("editor/upper")
        if not split:
            return False
        ok = self._split.restoreState(split)
        if upper:
            self._upper.restoreState(upper)
        return bool(ok)

    def _initial_sizes(self) -> None:
        if self._restore_layout():
            self.fit_zoom()
            return
        h = max(600, self._split.height())
        lanes = len(self.plan.clips) if self.plan is not None else 4
        strip = 118
        need = RULER_H + lanes * (LANE_H + LANE_GAP) + 50
        tl = max(int(h * 0.36), min(need, int(h * 0.44)))
        self._split.setSizes([max(330, h - strip - tl), strip, tl])
        self.fit_zoom()

    def fit_zoom(self) -> None:
        """Zoom so the longest clip fills the timeline's width."""
        if self.plan is None:
            return
        longest = max((c.duration for c in self.plan.clips.values()), default=10.0)
        avail = max(200, self.view.viewport().width() - LABEL_W - 30)
        self.zoom.setValue(int(max(self.zoom.minimum(), min(self.zoom.maximum(), avail / max(1.0, longest)))))

    # ================================================================ state
    @property
    def key(self):
        return (self.group_box.currentData(), self.style_box.currentData(), self.tag_box.currentData())

    def log(self, msg: str) -> None:
        if hasattr(self, "logbox"):
            self.logbox.appendPlainText(str(msg))

    def _fill_styles(self, current: str = "") -> None:
        current = current or self.style_box.currentData()
        g, tag = self.group_box.currentData(), self.tag_box.currentData()
        edited = self.store.edited_styles(g, tag) | {k[1] for k, p in self.plans.items()
                                                      if k[0] == g and k[2] == tag and p.edited}
        self.style_box.blockSignals(True)
        self.style_box.clear()
        for k, st in STYLES.items():
            label = ("✎ " if k in edited else "    ") + st.label + ("   • gerado" if k == self.main_style else "")
            self.style_box.addItem(label, k)
            self.style_box.setItemData(self.style_box.count() - 1, st.description, Qt.ToolTipRole)
        self.style_box.setCurrentIndex(max(0, self.style_box.findData(current)))
        self.style_box.blockSignals(False)

    def _remember(self) -> None:
        if self.plan is not None and self._loaded_key is not None:
            self.plans[self._loaded_key] = self.plan
            self.store.put(self._loaded_key[0], self.plan)

    _loaded_key = None

    def switch(self) -> None:
        """Show the (edited or automatic) cut of the chosen group/style/format."""
        self._remember()
        g, st, tag = self.key
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            gp = self.session.prepare(g, st)
            plan = self.plans.get(self.key)
            if plan is None:
                saved = self.store.get(g, st, tag)
                if saved:
                    from drone.edit_session import candidate_dict
                    base = EditPlan.from_plan(_EmptyPlan(), gp, style_key=st, tag=tag)
                    loaded = EditPlan.from_dict(saved, clips=base.clips,
                                                  candidates=[candidate_dict(c) for c in gp.cands])
                    if loaded.shots:
                        plan = loaded
                        self.log(f"✎ Edição salva carregada: {STYLES[st].label} {tag}")
                if plan is None:
                    _gp, auto_plan = self.session.plan(g, st, tag, self._target_for(g, tag))
                    if auto_plan is None:
                        raise RuntimeError("nenhum trecho utilizável para este grupo/estilo")
                    plan = EditPlan.from_plan(auto_plan, gp, style_key=st, tag=tag,
                                              target=auto_plan.total)
                self.plans[self.key] = plan
        except Exception as exc:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            self.log(f"⚠️ {exc}")
            QMessageBox.warning(self, "Edição manual", str(exc))
            return
        QApplication.restoreOverrideCursor()
        self.gp, self.plan = gp, plan
        self._loaded_key = self.key
        self.plan.magnet = False             # edits are free; the music is re-fitted to the film
        auto = f" (Automático → {gp.style.label})" if gp.auto else ""
        self.log(f"Estilo {STYLES[st].label}{auto}: {len(plan.shots)} tomadas, {plan.total:.1f} s")
        self.sel = 0 if plan.shots else None
        self._crop_i = None
        self._fill_durations()
        self._fill_focus()
        self.rebuild()
        self._queue_proxies()
        self.preview.t = 0.0
        self._load_program()
        self._record()

    # ================================================================ duration
    def _fill_durations(self) -> None:
        """Lengths this material can give, with the suggested and the maximum marked."""
        from drone import material
        g, _st, tag = self.key
        est = self.gp.est
        vertical = tag == "9x16"
        sug = est.suggested_vertical if vertical else est.suggested
        top = max(est.maximum, est.comfortable)
        current = self._target_for(g, tag) or self.plan.target or self.plan.total
        marks = {round(x): "" for x in material.choices(est, vertical)}
        if sug:
            marks[round(sug)] = "sugerida"
        if top >= 15:
            marks[round(top)] = "máximo"
        if round(current) not in marks:
            marks[round(current)] = "atual"
        self.dur_box.blockSignals(True)
        self.dur_box.clear()
        for sec in sorted(marks):
            label = material.clock(sec) + (f"  ·  {marks[sec]}" if marks[sec] else "")
            self.dur_box.addItem(label, float(sec))
        self.dur_box.setCurrentIndex(max(0, self.dur_box.findData(float(round(current)))))
        self.dur_box.blockSignals(False)
        raw = self.session.raw_seconds(g) if hasattr(self.session, "raw_seconds") else 0.0
        self.material_lbl.setText(
            (f"bruto {material.clock(raw)}  ·  " if raw else "")
            + f"bom ~{material.clock(est.comfortable)}  ·  até {material.clock(top)} esticando")

    def _target_for(self, g: str, tag: str):
        """Length chosen for this group/format: here, or after the analysis."""
        if (g, tag) in self.targets:
            return self.targets[(g, tag)]
        return self._first[1] if tag == self._first[0] and self._first[1] > 0 else None

    def _duration_picked(self, _index: int = 0) -> None:
        sec = self.dur_box.currentData()
        if sec:
            self.set_duration(float(sec))

    def _duration_typed(self) -> None:
        """Any length typed by hand: '90', '1:30', '4:00'."""
        from drone import material
        if self.plan is None:
            return
        sec = material.parse_clock(self.dur_box.currentText())
        g, _st, tag = self.key
        now = self._target_for(g, tag) or self.plan.target or self.plan.total
        if sec < 5 or abs(sec - now) < 0.5:
            self._fill_durations()
            return
        self.set_duration(sec)

    def redo(self, another: bool = False, wait: bool = False) -> None:
        """Refazer (with the wishes written in the box and the Foco) / Outra versão
        (other shots, other order), from the analysis already done. A new free-text
        Foco runs CLIP on the cached thumbnails, in the background."""
        if self.plan is None or self._redo_busy:
            return
        if self._redo_thread is not None:            # the last one is over: let its thread end first
            self._redo_thread.wait(2000)
        g, st, tag = self.key
        self.preview.pause()
        previous = [(s.path, s.start, s.end) for s in self.plan.shots] if another else []
        if another:
            self.variation += 1
        focus = self.focus_box.currentText().strip()
        if focus.lower() == "sem foco":
            focus = ""
        new_focus = focus != self._focus_text
        self.version_btn.setEnabled(False)
        self.beat.start("Remontando…" + (f" (procurando “{focus}”)" if new_focus and focus else ""))
        worker = _RedoWorker(self.session, self.feedback.text().strip(), self.variation, previous,
                             focus if new_focus else None)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.beat.update)
        worker.log.connect(self.log)
        # bound methods (not lambdas): Qt then runs them on the window's thread
        self._redo_args = (another, focus if new_focus else None)
        worker.finished.connect(self._redo_done)
        worker.failed.connect(self._redo_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        # Both stay referenced until the next Refazer: dropping the worker while
        # its thread is still winding down deletes it from the wrong thread.
        self._redo_thread, self._redo_worker = thread, worker
        self._redo_busy = True
        thread.start()
        if wait:                                     # tests
            while thread.isRunning() or self._redo_busy:
                QApplication.processEvents()
                thread.wait(20)

    def _redo_failed(self, msg: str) -> None:
        self._redo_busy = False
        self.version_btn.setEnabled(True)
        self.beat.finish("Não deu para remontar.", ok=False)
        QMessageBox.warning(self, "Nova versão", msg)

    def _redo_done(self, adj, msg: str) -> None:
        another, focus = self._redo_args
        self._redo_busy = False
        self.version_btn.setEnabled(True)
        if focus is not None:
            self._focus_text = focus
        if msg:
            self.log(msg)
        g, st, tag = self.key
        if adj.duration:
            self.targets[(g, tag)] = float(adj.duration)
        elif adj.duration_scale != 1.0:
            self.targets[(g, tag)] = max(5.0, (self._target_for(g, tag) or self.plan.total) * adj.duration_scale)
        for key in [k for k, pl in self.plans.items() if k == self.key or not pl.edited]:
            self.plans.pop(key)
        self.store.drop(g, st, tag)
        self._loaded_key = None
        if adj.style in STYLES and adj.style != st and self.style_box.findData(adj.style) >= 0:
            self.style_box.setCurrentIndex(self.style_box.findData(adj.style))   # triggers switch()
        else:
            self.switch()
        self._fill_styles()
        self.beat.finish("Remontado.")
        if self._focus_text and self.plan is not None:
            on = sum(1 for sh in self.plan.shots if getattr(sh, "focus", 0) >= 0.6)
            self.log(f"🎯 {on} de {len(self.plan.shots)} tomadas combinam com “{self._focus_text}”")
        req = self.feedback.text().strip()
        self.log("✦ Nova versão" + (f": {req}" if req else ": outras tomadas e outra ordem")
                 + (f" · foco {self._focus_text}" if self._focus_text else ""))
        self._version_tip()

    def open_version(self) -> None:
        """Nova versão: the request window, then a new cut (shots varied too)."""
        if self.plan is None or self._redo_busy:
            return
        from drone.version_dialog import VersionDialog
        g = self.key[0] if self.key else ""
        opts = self.session.focus_options(g)
        cur = self.focus_box.currentText().strip()
        d = VersionDialog(self, request=self.feedback.text(), focus="" if cur.lower() == "sem foco" else cur,
                          focus_options=opts, landscapes=[lab for lab, _n in opts], edited=self.plan.edited,
                          ai_on=getattr(self.session.ctx, "ai_client", None) is not None)
        if not d.exec():
            return
        self.feedback.setText(d.request)
        self.focus_box.setEditText(d.focus_text or "Sem foco")
        self.redo(True)

    def _version_tip(self) -> None:
        req, foc = self.feedback.text().strip(), self._focus_text
        extra = "\n".join(x for x in (req and f"Pedido em uso: {req}", foc and f"Foco em uso: {foc}") if x)
        self.version_btn.setText("✦ Nova versão" + (" •" if extra else ""))
        self.version_btn.setToolTip("Gera outra versão do vídeo a partir da mesma análise (Ctrl+Z volta)."
                                    + (f"\n\n{extra}" if extra else ""))

    def _fill_focus(self) -> None:
        """Landscapes found in this material, most frequent first; free text allowed."""
        g = self.key[0] if self.key else ""
        keep = self.focus_box.currentText()
        self.focus_box.blockSignals(True)
        self.focus_box.clear()
        self.focus_box.addItem("Sem foco")
        for label, n in self.session.focus_options(g):
            self.focus_box.addItem(f"{label} ({n} s)")
        self.focus_box.setEditText(keep or "Sem foco")
        self.focus_box.blockSignals(False)

    PICK_FOLDER = "\x00pasta"

    def _fill_music(self) -> None:
        box = self.music_box
        box.blockSignals(True)
        box.clear()
        box.addItem("Automática (pelo estilo)", "")
        for path in self.session.music_tracks():
            box.addItem(os.path.splitext(os.path.basename(path))[0], path)
            box.setItemData(box.count() - 1, path, Qt.ToolTipRole)
        box.addItem("Escolher pasta de músicas…", self.PICK_FOLDER)
        current = getattr(self.session.job, "music_file", "") or ""
        box.setCurrentIndex(max(0, box.findData(current)))
        box.blockSignals(False)

    def _music_picked(self, _i: int) -> None:
        data = self.music_box.currentData() or ""
        if data == self.PICK_FOLDER:
            from PySide6.QtWidgets import QFileDialog
            folder = QFileDialog.getExistingDirectory(self, "Pasta de músicas",
                                                      self.session.job.music_dir or os.path.expanduser("~"))
            if folder:
                self.session.job.music_dir = folder
                n = len(self.session.music_tracks())
                self.log(f"🎶 Biblioteca: {folder} ({n} faixa(s))")
            self._fill_music()
            return
        self.set_music(data)

    def set_music(self, path: str) -> None:
        """Use this track for the films of this group. Automatic cuts are planned
        again on its beats; edited ones keep their shots and get the new track."""
        if self.plan is None:
            return
        self.preview.pause()
        self.session.set_music(path)
        g = self.key[0]
        for key in [k for k, pl in self.plans.items() if k[0] == g and not pl.edited]:
            self.plans.pop(key)
        self._loaded_key = None
        self.switch()
        m = self.gp.music
        self.log(f"🎶 Música: {os.path.basename(m.path)} ({m.bpm:.0f} BPM)" if m else "🎶 Sem música")

    def set_duration(self, seconds: float, ask: bool = True) -> bool:
        """Re-fit the film of this group/format to ``seconds`` (all styles), from
        the analysis already done. Hand edits of the style on screen are replaced."""
        if self.plan is None:
            return False
        g, st, tag = self.key
        if ask and self.plan.edited and QMessageBox.question(
                self, "Duração", "Remontar o vídeo neste tamanho substitui os ajustes manuais feitos em "
                                 f"{STYLES[st].label} ({tag}). Continuar?") != QMessageBox.Yes:
            self._fill_durations()
            return False
        self.preview.pause()
        self.targets[(g, tag)] = float(seconds)
        for key in [k for k, pl in self.plans.items() if k[0] == g and k[2] == tag
                    and (k == self.key or not pl.edited)]:
            self.plans.pop(key)                 # unedited styles re-plan at the new length when shown
        self.store.drop(g, st, tag)
        self._loaded_key = None
        self.switch()
        self._fill_styles()
        return True

    # ================================================================ drawing
    def x_to_time(self, x: float) -> float:
        return max(0.0, (x - LABEL_W) / self.pps)

    def t_to_x(self, t: float) -> float:
        return LABEL_W + t * self.pps

    def _lanes(self) -> list:
        clips = sorted(self.plan.clips.values(), key=lambda c: (c.order, c.name))
        return [(c, RULER_H + i * (LANE_H + LANE_GAP)) for i, c in enumerate(clips)]

    def _thumb_list(self, clip) -> list:
        if clip.path not in self._thumbs:
            self._thumbs[clip.path] = [(t, _pixmap_from_jpeg(j)) for t, j in self.session.thumbs(clip.content_hash)]
        return self._thumbs[clip.path]

    def _fresh_scene(self) -> None:
        """A new scene for every redraw; the old one is deleted later, by Qt's
        event loop. ``scene.clear()`` deleted the blocks while Qt could still
        hold one of them (the block just dragged, the one under the mouse) —
        a hard crash that closed the program."""
        old = self.scene
        self.scene = QGraphicsScene(self)
        self.scene.setBackgroundBrush(QBrush(QColor(10, 10, 10)))
        self.view.setScene(self.scene)
        if old is not None:
            old.deleteLater()

    def rebuild(self) -> None:
        """Redraw everything — where the user was looking stays where it was."""
        keep = (self.view.horizontalScrollBar().value(), self.view.verticalScrollBar().value())
        self._rebuild()
        self.view.horizontalScrollBar().setValue(keep[0])
        self.view.verticalScrollBar().setValue(keep[1])
        QTimer.singleShot(0, lambda: (self.view.horizontalScrollBar().setValue(keep[0]),
                                      self.view.verticalScrollBar().setValue(keep[1])))

    def _rebuild(self) -> None:
        self._fresh_scene()
        # everything that pointed into the old scene is gone now
        self.sel_box = self.sel_edge = self.playhead = None
        self.shot_items = []
        if self.plan is None:
            return
        lanes = self._lanes()
        longest = max((c.duration for c, _ in lanes), default=10.0)
        width = self.t_to_x(longest) + 40
        height = RULER_H + len(lanes) * (LANE_H + LANE_GAP) + 10
        self.scene.setSceneRect(0, 0, width, height)
        # ruler
        step = next(s for s in (1, 2, 5, 10, 15, 30, 60, 120) if s * self.pps >= 45)
        pen = QPen(QColor(150, 150, 150))
        t = 0
        while t <= longest:
            x = self.t_to_x(t)
            self.scene.addLine(x, RULER_H - 8, x, RULER_H, pen)
            lbl = self.scene.addSimpleText(f"{int(t // 60)}:{int(t % 60):02d}")
            lbl.setPos(x + 2, 2)
            lbl.setBrush(QBrush(QColor(190, 190, 190)))
            t += step
        lane_y = self.lane_y = {}
        self.playhead = None
        for clip, y in lanes:
            lane_y[clip.path] = y
            self.scene.addRect(LABEL_W, y, clip.duration * self.pps, LANE_H,
                               QPen(QColor(38, 38, 38)), QBrush(QColor(20, 20, 20)))
            name = self.scene.addSimpleText(clip.name)
            name.setPos(6, y + 6)
            name.setBrush(QBrush(QColor(225, 225, 225)))
            sub = self.scene.addSimpleText(f"{fmt_time(clip.duration, clip.fps)} · {clip.fps:.0f} fps")
            sub.setPos(6, y + 26)
            sub.setBrush(QBrush(QColor(120, 120, 120)))
            thumbs = self._thumb_list(clip)
            if thumbs:
                th = LANE_H - 6
                tw = th * 16 / 9
                every = max(1e-6, tw / self.pps)
                next_t = 0.0
                lane_end = self.t_to_x(clip.duration)
                for tt, pm in thumbs:
                    if tt + 1e-6 < next_t or pm.isNull():
                        continue
                    x = self.t_to_x(tt)
                    skey = (clip.path, tt, int(th))
                    scaled = self._scaled.get(skey)
                    if scaled is None:          # scaling every thumbnail on each redraw was slow
                        scaled = self._scaled[skey] = pm.scaledToHeight(int(th), Qt.SmoothTransformation)
                    room = int(lane_end - x)
                    if room <= 2:
                        break
                    if scaled.width() > room:          # never draw past the clip's end
                        scaled = scaled.copy(0, 0, room, scaled.height())
                    item = self.scene.addPixmap(scaled)
                    item.setPos(x, y + 3)
                    item.setOpacity(0.45)
                    item.setZValue(1)
                    next_t = tt + every
        used = {(s.path, round(s.start, 2)) for s in self.plan.shots}

        def covered(c) -> bool:
            """Mostly inside a shot already in the film: drawing it would only clutter."""
            n = c["end"] - c["start"]
            return any(s.path == c["path"] and min(s.end, c["end"]) - max(s.start, c["start"]) > 0.5 * n
                       for s in self.plan.shots)
        for c in self.plan.candidates:
            if c["path"] not in lane_y or (c["path"], round(c["start"], 2)) in used or covered(c):
                continue
            y = lane_y[c["path"]]
            self.scene.addItem(CandidateItem(self, c, QRectF(self.t_to_x(c["start"]), y + 14,
                                                            (c["end"] - c["start"]) * self.pps, LANE_H - 28)))
        self.shot_items = []
        for i, s in enumerate(self.plan.shots):
            if s.path not in lane_y:
                continue
            y = lane_y[s.path]
            label = f"#{i + 1}"
            item = ShotItem(self, i, QRectF(self.t_to_x(s.start), y + 4, max(3.0, s.length * self.pps), LANE_H - 8),
                            _colour(s.category), label)
            item.setToolTip(f"#{i + 1} {LABEL_PT.get(s.category, s.category)} · {s.movement}\n"
                            f"{fmt_time(s.start)} → {fmt_time(s.end)} ({s.length:.2f} s)\n"
                            "Arraste as bordas para aparar, o meio para deslizar.")
            self.scene.addItem(item)
            self.shot_items.append(item)
        # selection overlays: moved, never recreated, so a drag in progress survives
        self.sel_box = QGraphicsRectItem()
        self.sel_box.setPen(QPen(QColor(255, 255, 255), 2))
        self.sel_box.setBrush(Qt.NoBrush)
        self.sel_box.setZValue(4)
        self.sel_box.setAcceptedMouseButtons(Qt.NoButton)      # the drag must reach the shot underneath
        self.scene.addItem(self.sel_box)
        self.sel_edge = self.scene.addLine(0, 0, 0, 0, QPen(QColor(255, 255, 255), 3))
        self.sel_edge.setZValue(5)
        self.sel_edge.setAcceptedMouseButtons(Qt.NoButton)
        self._place_selection()
        self.playhead = self.scene.addLine(0, 0, 0, LANE_H, QPen(QColor(255, 80, 80), 2))
        self.playhead.setZValue(10)
        self.playhead.setAcceptedMouseButtons(Qt.NoButton)
        self.playhead.setVisible(False)
        self._fill_strip()
        self._fill_legend()
        self._program_blocks()
        self._update_info()

    def _place_selection(self) -> None:
        box, edge = getattr(self, "sel_box", None), getattr(self, "sel_edge", None)
        if box is None:
            return
        ok = self.plan is not None and self.sel is not None and self.sel < len(self.shot_items)
        box.setVisible(ok)
        edge.setVisible(ok)
        if not ok:
            return
        r = self.shot_items[self.sel].rect()
        box.setRect(r.adjusted(-2, -2, 2, 2))
        s = self.plan.shots[self.sel]
        ex = self.t_to_x(s.start if self.edge == "start" else s.end)
        edge.setLine(ex, r.top() - 4, ex, r.bottom() + 4)

    def _refresh_selection(self) -> None:
        """Selection changed without the cut changing: no scene rebuild."""
        self._place_selection()
        self._update_crop()
        self._program_blocks()
        self._update_info()

    def _fill_legend(self) -> None:
        cats = []
        for s in self.plan.shots:
            if s.category not in cats:
                cats.append(s.category)
        chips = [f"<span style='color:{category_colour(c)}; font-size:13pt'>■</span>&nbsp;"
                 f"{LABEL_PT.get(c, c or 'sem categoria')}" for c in cats]
        chips.append("<span style='color:#9a9a9a; font-size:13pt'>□</span>&nbsp;não usado")
        self.legend.setText("&nbsp;&nbsp;&nbsp; ".join(chips))

    def _fill_strip(self) -> None:
        self._program_blocks()

    def _shot_picture(self, s):
        clip = self.plan.clips.get(s.path)
        if clip is None:
            return None
        thumbs = self._thumb_list(clip)
        if not thumbs:
            return None
        return min(thumbs, key=lambda tp: abs(tp[0] - s.start - 0.3))[1]

    def _update_info(self) -> None:
        p = self.plan

        self.total_lbl.setText(f"{_clock(p.total)} · {len(p.shots)} tomadas")
        self.total_lbl.setToolTip(f"Duração do vídeo: {p.total:.1f} s")
        if self.sel is None or self.sel >= len(p.shots):
            self.info.setText("Selecione uma tomada colorida.")
            return
        s = p.shots[self.sel]
        clip = p.clips.get(s.path)
        fps = clip.fps if clip else 30
        self.info.setText(
            f"<b>Tomada #{self.sel + 1}</b> — {os.path.basename(s.path)}<br>"
            f"{LABEL_PT.get(s.category, s.category)} · {s.movement} · nota {s.score:.2f}<br>"
            f"Entrada <b>{fmt_time(s.start, fps)}</b> · Saída <b>{fmt_time(s.end, fps)}</b> · "
            f"{s.length:.2f} s ({int(round(s.length * fps))} quadros)<br>"
            f"No vídeo: {p.cut_times()[self.sel]:.2f} s · na tela {p.advance(self.sel):.2f} s · "
            f"transição: {'corte seco' if not p.trans_out(self.sel) else f'fusão {p.trans_out(self.sel):.1f} s'}<br>"
            f"Borda ativa: <b>{'entrada' if self.edge == 'start' else 'saída'}</b> ([ / ] ou Tab)"
            + (f"<br>Texto: <b>{html.escape(s.caption['text'][:40])}</b>" if s.caption else "")
            + (f"<br>Enquadramento 9:16: <b>{s.crop_x * 100:.0f}%</b> da largura "
               "(arraste o quadro branco no monitor)" if self.key[2] == "9x16" else ""))
        self.crop_reset_btn.setVisible(self.key[2] == "9x16")
        self._preview_timer.start()

    # ================================================================ preview
    def _request_frames(self) -> None:
        if self.plan is None or self.sel is None or self.sel >= len(self.plan.shots):
            return
        s = self.plan.shots[self.sel]
        f = self.plan.frame(s.path)
        for which, t in (("in", s.start), ("out", max(s.start, s.end - f))):
            key = f"{s.path}|{t:.4f}"
            if key in self._frames:
                self._show_frame(which, self._frames[key])
            else:
                self._pending = getattr(self, "_pending", {})
                self._pending[key] = which
                self._pool.start(_FrameJob(key, self._proxies.get(s.path, s.path), t, self._fsig))

    def _want_still(self, path: str, t: float) -> None:
        """Monitor paused: show the exact frame (grabbed with ffmpeg, cached).

        Dragging along the film asks for a frame at every mouse move; only the
        newest one is fetched, a moment after the mouse settles."""
        key = f"{path}|{t:.4f}"
        self._monitor_key = key
        if key in self._frames:
            self.preview.show_still(QPixmap.fromImage(self._frames[key]))
            return
        self._still_want = (path, t, key)
        self._still_timer.start()

    def _grab_still(self) -> None:
        if self._still_want is None:
            return
        path, t, key = self._still_want
        self._still_want = None
        if key != self._monitor_key or key in self._frames:
            return
        self._pending = getattr(self, "_pending", {})
        self._pending[key] = "monitor"
        job = _FrameJob(key, self._proxies.get(path, path), t, self._fsig)
        job.width = 960
        self._pool.start(job)

    def _frame_ready(self, key: str, img) -> None:
        self._frames[key] = img
        while len(self._frames) > 150:          # oldest out: a long session must not eat the memory
            self._frames.pop(next(iter(self._frames)))
        if key == getattr(self, "_monitor_key", None):
            self.preview.show_still(QPixmap.fromImage(img))
        which = getattr(self, "_pending", {}).pop(key, None)
        if which and self.plan is not None and self.sel is not None and self.sel < len(self.plan.shots):
            s = self.plan.shots[self.sel]
            want = {"in": f"{s.path}|{s.start:.4f}",
                    "out": f"{s.path}|{max(s.start, s.end - self.plan.frame(s.path)):.4f}"}
            if want.get(which) == key:
                self._show_frame(which, img)

    def _show_frame(self, which: str, img) -> None:
        lbl = self.in_img if which == "in" else self.out_img
        lbl.setPixmap(QPixmap.fromImage(img).scaled(lbl.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # ================================================================ playback
    def _play_source(self, path: str) -> str:
        return self._proxies.get(path, path)

    def _queue_proxies(self) -> None:
        """Light copies for the preview: the film's clips first, then the rest."""
        if self.plan is None or not self._can_play:
            return
        from drone import proxy
        used = [s.path for s in self.plan.shots]
        order = sorted(self.plan.clips.values(), key=lambda c: (c.path not in used, c.order))
        for c in order:
            if c.path in self._proxies or c.path in self._proxy_queued:
                continue
            ready = proxy.existing(c.content_hash)
            if ready:
                self._proxies[c.path] = ready
                continue
            self._proxy_queued.add(c.path)
            self._proxy_pool.start(_ProxyJob(c.path, c.content_hash, self._proxy_sig,
                                             lambda: self._closing))
        self._proxy_status()

    def _proxy_ready(self, path: str, out: str) -> None:
        if self._closing:
            return
        self._proxies[path] = out
        self._proxy_queued.discard(path)
        self._proxy_status()

    def _proxy_status(self) -> None:
        n = len(self.plan.clips) if self.plan is not None else 0
        done = sum(1 for c in (self.plan.clips if self.plan else {}) if c in self._proxies)
        self.proxy_lbl.setText("" if done >= n else f"preparando prévia leve {done}/{n}…")

    def _sequence_pieces(self) -> list:
        p, out = self.plan, []
        cuts = p.cut_times()
        for i, sh in enumerate(p.shots):
            length = p.advance(i) if i < len(p.shots) - 1 else sh.length
            out.append(Piece(sh.path, sh.start, sh.start + max(0.04, length), cuts[i]))
        return out

    def _music(self) -> tuple[str, float]:
        m = getattr(self.gp, "music", None)
        if m is None or not self.music_chk.isChecked():
            return "", 0.0
        return m.path, float(m.start)

    def _load_program(self, keep: bool = True) -> None:
        """Monitor back on the film as it now stands (after any edit)."""
        if self.plan is None:
            return
        if self.mode != "seq":
            self.preview.pause()
            self.preview.loop = False
            self.mode = "seq"
            self.monitor_title.setText("SEQUÊNCIA FINAL")
        path, off = self._music()
        self.preview.set_program(self._sequence_pieces(), path, off)
        if not self.preview.playing:
            self.preview.seek(self.preview.t)
        self._play_pos(self.preview.t)
        self._update_crop()

    def play_sequence(self) -> None:
        """Play / pause the film, from the selected shot when starting."""
        if not self._can_play or self.plan is None:
            return
        if self.mode != "seq":
            self._load_program()
            start = self.plan.cut_times()[self.sel] if self.sel is not None else 0.0
            self.preview.play(start)
            return
        if self.preview.playing:
            self.preview.pause()
            return
        t = self.preview.t
        if self.sel is not None and not (self.preview.cur == self.sel):
            t = self.plan.cut_times()[self.sel]
        self.preview.play(t)

    def play_shot(self, i: int | None = None) -> None:
        """Play one shot once, from its first frame (double-click, T)."""
        if not self._can_play or self.plan is None or not self.plan.shots:
            return
        if i is not None and i != self.sel:
            self.sel = i
            self._refresh_selection()
        if self.sel is None:
            return
        s = self.plan.shots[self.sel]
        self.mode = "shot"
        self.preview.loop = False
        self._loop_uid = s.uid
        self.monitor_title.setText(f"TOMADA #{self.sel + 1}  ·  clique ou Espaço: tocar / pausar  ·  "
                                   "duplo clique: do início")
        self.preview.set_program([Piece(s.path, s.start, s.end, 0.0)])
        self._update_crop()
        self.preview.play(0.0)

    def click_shot(self, i: int | None = None) -> None:
        """Click on a shot (or Espaço): play / pause it. Another shot starts from
        its beginning; the one already in the monitor pauses or goes on (from the
        start again once it has finished)."""
        if not self._can_play or self.plan is None or not self.plan.shots:
            return
        i = self.sel if i is None else i
        if i is None:
            return
        if self.mode == "shot" and getattr(self, "_loop_uid", None) == self.plan.shots[i].uid:
            self.preview.toggle()
        else:
            self.play_shot(i)

    def play_loop(self, i: int | None = None) -> None:
        """Play one shot over and over (double-click it): edit it while it loops —
        trims and slides show up on the next pass."""
        if not self._can_play or self.plan is None or not self.plan.shots:
            return
        if i is not None:
            self.sel = i
            self._refresh_selection()
        if self.sel is None:
            return
        s = self.plan.shots[self.sel]
        self.mode = "shot"
        self.preview.loop = True
        self._loop_uid = s.uid
        self.monitor_title.setText(f"TOMADA #{self.sel + 1} EM LOOP  ·  Espaço pausa  ·  ■ sai do loop")
        self.preview.set_program([Piece(s.path, s.start, s.end, 0.0)])
        self.preview.play(0.0)

    def _refresh_loop(self) -> bool:
        """The shot in the monitor was edited: give the player its new in/out.
        False if there is none (the monitor is on the film or the shot is gone)."""
        if self.mode != "shot":
            return False
        idx = next((k for k, sh in enumerate(self.plan.shots) if sh.uid == getattr(self, "_loop_uid", None)), None)
        if idx is None:
            return False
        s = self.plan.shots[idx]
        self.sel = idx
        if self.preview.loop:
            self.monitor_title.setText(f"TOMADA #{idx + 1} EM LOOP  ·  Espaço pausa  ·  ■ sai do loop")
        self.preview.set_program([Piece(s.path, s.start, s.end, 0.0)])
        return True

    def play(self) -> None:
        """Play only the selected shot, in → out (button "Tocar tomada")."""
        self.play_shot()

    def play_clip(self, path: str = "", t: float | None = None) -> None:
        """Play a whole raw clip (default: the selected shot's), from ``t``."""
        if not self._can_play or self.plan is None:
            return
        if not path:
            if self.sel is None:
                return
            path = self.plan.shots[self.sel].path
            t = self.plan.shots[self.sel].start if t is None else t
        clip = self.plan.clips.get(path)
        if clip is None:
            return
        self.mode = "clip"
        self.preview.loop = False
        self.monitor_title.setText(f"CLIPE ORIGINAL  ·  {clip.name}")
        self.preview.set_program([Piece(path, 0.0, clip.duration, 0.0)])
        self._update_crop()
        self.preview.play(t or 0.0)

    def lane_click(self, pos, play: bool) -> None:
        """Click on an empty spot of a lane: that frame; double-click: play from it."""
        for path, y in self.lane_y.items():
            if y <= pos.y() <= y + LANE_H:
                t = self.x_to_time(pos.x())
                clip = self.plan.clips.get(path)
                if clip is None or t > clip.duration:
                    return
                if play:
                    self.play_clip(path, t)
                elif self._can_play:
                    self.preview.pause()
                    self.mode = "clip"
                    self.monitor_title.setText(f"CLIPE ORIGINAL  ·  {clip.name}")
                    self.preview.set_program([Piece(path, 0.0, clip.duration, 0.0)])
                    self.preview.seek(t)
                return

    def _seek_program(self, t: float) -> None:
        """Click on the film's line: back to the film if a shot/clip was playing."""
        if self.plan is None:
            return
        if self.mode != "seq":
            playing = self.preview.playing
            self._load_program()
            if playing:
                self.preview.play(t)
                return
        self.preview.seek(t)
        i = self.preview.piece_at(t)
        if not self.preview.playing and i != self.sel:
            self.sel = i
            self._refresh_selection()
            self._scroll_to_sel()

    def stop(self) -> None:
        """Stop and go back to the start of the film."""
        self._space = "film"
        self.preview.pause()
        self._load_program()
        self.preview.seek(0.0)
        if self.plan is not None and self.plan.shots:
            self.sel = 0
            self._refresh_selection()
            self._scroll_to_sel()

    def _program_blocks(self) -> None:
        if self.plan is None:
            return
        p, cuts = self.plan, self.plan.cut_times()
        blocks = []
        for i, s in enumerate(p.shots):
            on = p.advance(i) if i < len(p.shots) - 1 else s.length
            blocks.append({"start": cuts[i], "length": on, "colour": category_colour(s.category),
                           "label": f"#{i + 1}  {on:.1f}s" + (" ⇢" if p.trans_out(i) else ""),
                           "pix": self._shot_picture(s), "caption": bool(s.caption)})
        self.scrub.set_blocks(blocks, p.total, self.sel)

    def _play_pos(self, t: float) -> None:
        if self.key[2] == "9x16" and not self.preview.playing and self._crop_index() != self._crop_i:
            self._update_crop()
        d = self.preview.duration
        if self.mode == "seq":
            self.scrub.set_time(t)
        self.time_lbl.setText(f"{_clock(t)} / {_clock(d)}")
        self._move_playhead(t)

    def _play_piece(self, i: int) -> None:
        if self.key[2] == "9x16":
            self._update_crop()
        if self.mode == "seq":
            self.scrub.sel = i
            self.scrub.update()

    def _play_state(self, playing: bool) -> None:
        self.play_seq_btn.setText("⏸" if playing and self.mode == "seq" else "▶")
        if self.playhead is not None:
            self.playhead.setVisible(playing or self.mode != "seq")

    # ---------------------------------------------------------------- 9:16 framing
    def _crop_index(self):
        """The shot whose 9:16 frame the monitor shows (None: no frame)."""
        if self.plan is None or not self.plan.shots or self.key[2] != "9x16":
            return None
        if self.mode == "shot":
            return self.sel
        if self.mode == "seq" and self.preview.pieces:
            i = self.preview.cur if self.preview.playing and self.preview.cur >= 0 \
                else self.preview.piece_at(self.preview.t)
            return i if 0 <= i < len(self.plan.shots) else None
        return None

    def _update_crop(self) -> None:
        i = self._crop_index()
        self._crop_i = i
        self.preview.stack.set_crop(None if i is None else self.plan.shots[i].crop_x)
        self._update_caption()

    def edit_text(self) -> None:
        """Text over the selected shot (or the one on the monitor)."""
        if self.plan is None or not self.plan.shots:
            return
        i = self.sel if self.sel is not None else self._shown_index()
        if i is None:
            i = 0
        self.sel = i
        self.preview.pause()
        from drone.text_dialog import TextDialog
        shot = self.plan.shots[i]
        spec = shot.caption or {**getattr(self, "_last_caption", {}), "text": ""}
        frame = self.in_img.pixmap()
        frame = self._frame_for(i) or (frame if frame is not None and not frame.isNull() else None)
        d = TextDialog(self, spec, frame, i + 1, self.key[2] == "9x16")
        if not d.exec():
            return
        self.plan.set_caption(i, d.result_spec)
        if d.result_spec:
            self._last_caption = {k: v for k, v in d.result_spec.items() if k != "text"}
            self.log(f"🔤 Texto na tomada #{i + 1}: {d.result_spec['text'][:40]}")
        else:
            self.log(f"🔤 Texto removido da tomada #{i + 1}")
        self._remember()
        self._changed()
        self._update_caption()

    def _frame_for(self, i: int):
        """A frame of shot ``i`` for the text preview (9:16: cropped like the film)."""
        s = self.plan.shots[i]
        clip = self.plan.clips.get(s.path)
        thumbs = self._thumb_list(clip) if clip is not None else None
        if not thumbs:
            return None
        _t, pm = min(thumbs, key=lambda tp: abs(tp[0] - s.start - 0.3))
        if self.key[2] != "9x16" or pm.width() <= pm.height():
            return pm
        cw = int(pm.height() * 9 / 16)
        x = int(min(max(s.crop_x * pm.width() - cw / 2, 0), pm.width() - cw))
        return pm.copy(x, 0, cw, pm.height())

    def _shown_index(self):
        """The shot the monitor is showing (None while a raw clip plays)."""
        if self.plan is None or not self.plan.shots:
            return None
        if self.mode == "shot":
            return self.sel
        if self.mode == "seq" and self.preview.pieces:
            i = self.preview.cur if self.preview.playing and self.preview.cur >= 0 \
                else self.preview.piece_at(self.preview.t)
            return i if 0 <= i < len(self.plan.shots) else None
        return None

    def _update_caption(self) -> None:
        i = self._shown_index()
        self.preview.stack.set_caption(None if i is None else self.plan.shots[i].caption,
                                       vertical=self.key[2] == "9x16")

    def _crop_moved(self, centre: float) -> None:
        if self._crop_i is not None and self.plan is not None:
            self.plan.shots[self._crop_i].crop_x = round(centre, 4)   # live; committed on release

    def _crop_done(self, centre: float) -> None:
        i = self._crop_i
        if i is None or self.plan is None:
            return
        self.plan.set_crop(i, centre)
        self._remember()
        self._record()
        self._fill_styles()
        if i == self.sel:
            self._update_info()
        self.log(f"↔ Enquadramento 9:16 da tomada #{i + 1}: {centre * 100:.0f}% da largura")

    def reset_crop(self) -> None:
        """Back to the automatic framing of the selected shot."""
        if self.plan is None or self.sel is None:
            return
        s = self.plan.shots[self.sel]
        auto = next((c.get("crop_x", 0.5) for c in self.plan.candidates
                     if c["path"] == s.path and abs(c["start"] - s.start) < 0.05), None)
        if auto is None:
            auto = 0.5
        self.plan.set_crop(self.sel, auto)
        self._remember()
        self._record()
        self._update_crop()
        self._update_info()

    def _move_playhead(self, t: float) -> None:
        if self.playhead is None or not self.preview.pieces:
            return
        if self.mode == "seq":
            i = self.preview.piece_at(t)
            pc = self.preview.pieces[i]
            path, src = pc.path, pc.src_start + max(0.0, t - pc.film_start)
        else:
            pc = self.preview.pieces[0]
            path, src = pc.path, pc.src_start + t
        y = self.lane_y.get(path)
        if y is None:
            self.playhead.setVisible(False)
            return
        x = self.t_to_x(src)
        self.playhead.setLine(x, y - 2, x, y + LANE_H + 2)
        self.playhead.setVisible(self.preview.playing or self.mode != "seq")

    def _step(self, d: int) -> None:
        if self.plan is None or not self.plan.shots:
            return
        i = 0 if self.sel is None else max(0, min(len(self.plan.shots) - 1, self.sel + d))
        self.sel = i
        self._refresh_selection()
        self._scroll_to_sel()
        if self.mode == "seq":
            self.preview.seek(self.plan.cut_times()[i])

    # ================================================================ editing
    def select(self, i: int, edge: str | None = None) -> None:
        self._space = "shot"
        changed = i != self.sel
        self.sel = i
        if edge in ("start", "end"):
            self.edge = edge
        self._refresh_selection()
        if changed:
            self._cue_selected()

    def drag_edge(self, i: int, edge: str, t: float) -> None:
        self.plan.trim(i, edge, t, snap=False)
        self._move_item(i)

    def drag_slide(self, i: int, dt: float) -> None:
        self.plan.slide(i, dt)
        self._move_item(i)

    def _move_item(self, i: int) -> None:
        if i < len(getattr(self, "shot_items", [])):
            s = self.plan.shots[i]
            it = self.shot_items[i]
            r = it.rect()
            it.setRect(QRectF(self.t_to_x(s.start), r.y(), max(3.0, s.length * self.pps), r.height()))
            for child in it.childItems():          # the number tag follows the block
                if isinstance(child, QGraphicsRectItem):
                    child.setRect(QRectF(self.t_to_x(s.start) + 2, r.y() + 2, child.rect().width(), 16))
                else:
                    child.setPos(self.t_to_x(s.start) + 5, r.y() + 3)
        self._place_selection()
        self._update_info()

    def commit_drag(self) -> None:
        self._changed()

    def _changed(self) -> None:
        self.rebuild()
        self._fill_styles()
        if not self._refresh_loop():
            self._load_program()
        self._record()

    # ---------------------------------------------------------------- undo / redo
    def _history(self) -> dict:
        return self._hist.setdefault(self.key, {"undo": [], "redo": [], "last": None})

    def _record(self) -> None:
        """After any change: the state before it goes on the undo list."""
        if self.plan is None or not self.key:
            return
        h = self._history()
        now = self.plan.to_dict()
        if h["last"] is not None and now != h["last"]:
            h["undo"].append(h["last"])
            del h["undo"][:-100]
            h["redo"].clear()
        h["last"] = now
        self._undo_buttons()

    def _undo_buttons(self) -> None:
        h = self._hist.get(self.key) or {"undo": [], "redo": []}
        self.undo_btn.setEnabled(bool(h["undo"]))
        self.redo_edit_btn.setEnabled(bool(h["redo"]))

    def undo(self) -> None:
        self._step_history("undo", "redo", "↶ Desfeito")

    def redo_edit(self) -> None:
        self._step_history("redo", "undo", "↷ Refeito")

    def _step_history(self, take: str, give: str, word: str) -> None:
        h = self._hist.get(self.key)
        if self.plan is None or not h or not h[take]:
            return
        self.preview.pause()
        h[give].append(self.plan.to_dict())
        state = h[take].pop()
        plan = EditPlan.from_dict(state, clips=self.plan.clips, candidates=self.plan.candidates)
        g, st, tag = self.key
        if plan.target:
            self.targets[(g, tag)] = plan.target
        self.plan = plan
        self.plans[self.key] = plan
        h["last"] = state
        if self.sel is not None and self.sel >= len(plan.shots):
            self.sel = len(plan.shots) - 1 if plan.shots else None
        self.rebuild()
        self._fill_styles()
        self._fill_durations()
        self._load_program()
        self._remember()
        self._undo_buttons()
        self.log(f"{word}: {len(plan.shots)} tomadas, {_clock(plan.total)}")

    def keyPressEvent(self, e):
        if self._undo_key(e):
            return
        if e.key() == Qt.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
            return
        if e.key() == Qt.Key_Escape and self.isFullScreen():
            self.showMaximized()
            return
        if e.key() == Qt.Key_Space:
            self.space()
            return
        super().keyPressEvent(e)

    def _undo_key(self, e) -> bool:
        if not e.modifiers() & Qt.ControlModifier:
            return False
        if e.key() == Qt.Key_Z and e.modifiers() & Qt.ShiftModifier or e.key() == Qt.Key_Y:
            self.redo_edit()
            return True
        if e.key() == Qt.Key_Z:
            self.undo()
            return True
        return False

    def on_key(self, e) -> bool:
        if self._undo_key(e):
            return True
        k, mods = e.key(), e.modifiers()
        if k == Qt.Key_Space:
            self.space()
            return True
        if k == Qt.Key_F11:
            self.keyPressEvent(e)
            return True
        if self.plan is None or self.sel is None:
            return False
        if k == Qt.Key_T:
            self.play_shot()
            return True
        if k == Qt.Key_L:
            self.play_loop()
            return True
        if k == Qt.Key_C:
            self.play_clip()
            return True
        if k in (Qt.Key_Left, Qt.Key_Right):
            sign = -1 if k == Qt.Key_Left else 1
            if mods & Qt.AltModifier:
                self.plan.slide(self.sel, sign * self.plan.frame(self.plan.shots[self.sel].path))
            elif mods & Qt.ShiftModifier:
                self.plan.nudge(self.sel, self.edge, seconds=sign)
            else:
                self.plan.nudge(self.sel, self.edge, frames=sign)
            self._changed()
            return True
        if k == Qt.Key_BracketLeft:
            self.edge = "start"
        elif k == Qt.Key_BracketRight:
            self.edge = "end"
        elif k == Qt.Key_Tab:
            self.edge = "start" if self.edge == "end" else "end"
        elif k == Qt.Key_Up:
            self.sel = max(0, self.sel - 1)
        elif k == Qt.Key_Down:
            self.sel = min(len(self.plan.shots) - 1, self.sel + 1)
        elif k in (Qt.Key_Delete, Qt.Key_Backspace):
            self.remove(self.sel)
            return True
        else:
            return False
        self._refresh_selection()
        self._scroll_to_sel()
        return True

    def remove(self, i: int) -> None:
        if len(self.plan.shots) <= 1:
            QMessageBox.information(self, "Edição manual", "O vídeo precisa de pelo menos uma tomada.")
            return
        self.plan.remove(i)
        self.sel = min(i, len(self.plan.shots) - 1)
        self._changed()

    def add_candidate(self, cand: dict, at: int | None = None) -> None:
        at = (self.sel + 1) if (at is None and self.sel is not None) else at
        self.sel = self.plan.insert(cand, at)
        self._changed()

    def candidate_menu(self, cand: dict, pos) -> None:
        m = QMenu(self)
        a1 = m.addAction("Adicionar depois da tomada selecionada")
        a2 = m.addAction("Adicionar no final")
        a3 = m.addAction("Substituir a tomada selecionada")
        a3.setEnabled(self.sel is not None)
        chosen = m.exec(pos)
        if chosen is a1:
            self.add_candidate(cand)
        elif chosen is a2:
            self.add_candidate(cand, at=len(self.plan.shots))
        elif chosen is a3 and self.sel is not None:
            self.plan.replace(self.sel, cand)
            self._changed()

    def shot_menu(self, i: int, pos) -> None:
        self.sel = i
        m = QMenu(self)
        cut = m.addAction("Corte seco para a próxima")
        fade = m.addAction("Fusão para a próxima")
        m.addSeparator()
        split = m.addAction("Dividir ao meio")
        split.setEnabled(self.plan.shots[i].length >= 1.0)
        txt = m.addAction("Texto…" if not self.plan.shots[i].caption else "Editar texto…")
        rem = m.addAction("Remover do vídeo")
        last = i == len(self.plan.shots) - 1
        cut.setEnabled(not last)
        fade.setEnabled(not last)
        chosen = m.exec(pos)
        if chosen is cut:
            self.plan.set_transition(i, "cut")
        elif chosen is fade:
            self.plan.set_transition(i, "crossfade", 0.6)
        elif chosen is txt:
            self.edit_text()
            return
        elif chosen is split:
            self.split_shot(i)
            return
        elif chosen is rem:
            self.remove(i)
            return
        else:
            return
        self._changed()

    # ---------------------------------------------------------------- Espaço and the film line
    def _space_film(self) -> None:
        self._space = "film"

    def space(self) -> None:
        """Espaço: plays / pauses the whole film after a click on the time ruler
        or the player buttons; the marked shot after a click on a shot."""
        if getattr(self, "_space", "shot") == "film":
            self.toggle_film()
        else:
            self.click_shot()

    def toggle_film(self) -> None:
        if not self._can_play or self.plan is None:
            return
        if self.mode != "seq":
            self._load_program()
            self.preview.play(self.preview.t)
        elif self.preview.playing:
            self.preview.pause()
        else:
            self.preview.play(self.preview.t)

    def _film_seek(self, t: float) -> None:
        self._space = "film"
        self._seek_program(t)

    def _film_picked(self, i: int) -> None:
        self._space = "shot"
        if self.preview.playing:
            self.preview.pause()
        if i != self.sel:
            self.sel = i
            self._refresh_selection()
            self._scroll_to_sel()
        self._cue_selected()

    def _film_roll(self, i: int, dt: float) -> None:
        """Drag of the line between shots i and i+1 (live; recorded on release)."""
        if self.plan.roll(i, dt):
            self.sel = i
            self._program_blocks()
            self._move_item(i)
            self._move_item(i + 1)

    def split_shot(self, i: int | None = None) -> None:
        i = self.sel if i is None else i
        if self.plan is None or i is None:
            return
        j = self.plan.split(i)
        if j is None:
            self.log("✂ Tomada curta demais para dividir")
            return
        self.sel = i
        self.log(f"✂ Tomada #{i + 1} dividida em #{i + 1} e #{j + 1}")
        self._changed()

    def _strip_selected(self, row: int) -> None:
        if row >= 0 and row != self.sel:
            self.sel = row
            self._refresh_selection()
            self._scroll_to_sel()
            self._cue_selected()

    def _cue_selected(self) -> None:
        """Paused on the film: show the selected shot's first frame in the monitor."""
        if self.mode == "seq" and not self.preview.playing and self.sel is not None and self._can_play:
            self.preview.seek(self.plan.cut_times()[self.sel])

    def _scroll_to_sel(self) -> None:
        """Bring the selected shot into view — only if it is not already."""
        if self.sel is not None and self.sel < len(getattr(self, "shot_items", [])):
            self.view.ensureVisible(self.shot_items[self.sel], 80, 20)

    def _strip_moved(self, src: int, dst: int) -> None:
        self.plan.move(src, dst)
        self.sel = dst
        self._changed()
        self._scroll_to_sel()

    def _zoom(self, v: int) -> None:
        self.pps = float(v)
        self.rebuild()
        self._scroll_to_sel()

    def restore(self) -> None:
        g, st, tag = self.key
        if QMessageBox.question(self, "Restaurar", f"Descartar as edições de {STYLES[st].label} ({tag}) "
                                                   "e voltar ao corte automático?") != QMessageBox.Yes:
            return
        self.plans.pop(self.key, None)
        self.store.drop(g, st, tag)
        self.store.save()
        self._loaded_key = None
        self.switch()
        self._fill_styles()

    def save(self) -> str:
        self._remember()
        path = self.store.save()
        self.log(f"💾 Edição salva em {path}")
        self._fill_styles()
        return path

    # ================================================================ render
    def render(self, ask: bool = True) -> None:
        if self.plan is None or not self.plan.shots:
            return
        g, st, tag = self.key
        what = (f"{STYLES[st].label} · {'Vertical 9:16' if tag == '9x16' else 'Horizontal 16:9'} · "
                f"{_clock(self.plan.total)} · {len(self.plan.shots)} tomadas"
                + (" · com os seus ajustes" if self.plan.edited else ""))
        if ask and QMessageBox.question(self, "Renderizar", f"Renderizar exatamente o que está na prévia?\n\n{what}") \
                != QMessageBox.Yes:
            return
        self.preview.pause()
        self.save()
        plan = self.plan.to_plan(self.gp.music, self.title_text)
        if any(sh.caption for sh in self.plan.shots):
            from drone import text_overlay
            job = self.session.job
            size = job.vertical_size if tag == "9x16" else job.horizontal_size
            plan.overlays = text_overlay.write_pngs(
                self.plan, size, os.path.join(self.session.ctx.out_dir, "projeto", "textos"))
        self.log(f"🎬 Renderizando {what}. Ordem: "
                 + ", ".join(f"#{i + 1} {os.path.splitext(os.path.basename(s.path))[0]}"
                             for i, s in enumerate(self.plan.shots)))
        self.beat.start("Renderizando…")
        self.render_btn.setEnabled(False)
        self.capcut_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setVisible(True)
        self._render_thread = QThread()
        self._render_worker = _RenderWorker(self.session, self.gp, plan, self.plan.tag)
        self._render_worker.moveToThread(self._render_thread)
        self._render_thread.started.connect(self._render_worker.run)
        self._render_worker.log.connect(self.log)
        self._render_worker.progress.connect(self.beat.update)
        self._render_worker.log.connect(self.beat.touch)
        self._render_worker.finished.connect(self._rendered)
        self._render_worker.failed.connect(self._render_failed)
        self._render_worker.finished.connect(self._render_thread.quit)
        self._render_worker.failed.connect(self._render_thread.quit)
        self._render_thread.start()

    def _cancel_render(self) -> None:
        if self._render_worker is not None:
            self._render_worker.cancelled = True

    def send_to_capcut(self) -> None:
        """Cut the shots (frame-exact, full quality) into a numbered folder for CapCut."""
        if self.plan is None or not self.plan.shots or self._render_thread is not None \
                and self._render_thread.isRunning():
            return
        self.save()
        self.preview.pause()
        cfg = getattr(self.session.ctx, "cfg", None)
        kwargs = {"out_dir": self.session.ctx.out_dir, "music": self.gp.music,
                  "src_sizes": getattr(self.gp, "src_sizes", None), "title": self.title_text,
                  "mode": getattr(cfg, "render_mode", "gpu")}
        self.beat.start("Preparando a pasta para o CapCut…")
        self.render_btn.setEnabled(False)
        self.capcut_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setVisible(True)
        self._render_thread = QThread()
        self._render_worker = _CapCutWorker(self.plan, kwargs)
        self._render_worker.moveToThread(self._render_thread)
        self._render_thread.started.connect(self._render_worker.run)
        self._render_worker.log.connect(self.log)
        self._render_worker.progress.connect(self.beat.update)
        self._render_worker.log.connect(self.beat.touch)
        self._render_worker.finished.connect(self._capcut_done)
        self._render_worker.failed.connect(self._render_failed)
        self._render_worker.finished.connect(self._render_thread.quit)
        self._render_worker.failed.connect(self._render_thread.quit)
        self._render_thread.start()

    def _capcut_done(self, folder: str) -> None:
        from PySide6.QtGui import QDesktopServices
        from drone.capcut_export import find_capcut
        self.beat.finish("Pronto.")
        self.render_btn.setEnabled(True)
        self.capcut_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setVisible(False)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        exe = find_capcut()
        if exe:
            try:
                os.startfile(exe)  # noqa: S606 — the user's own CapCut
            except OSError:
                exe = ""
        where = "O CapCut está abrindo." if exe else "Abra o CapCut (não o encontrei instalado neste PC)."
        QMessageBox.information(
            self, "Enviar para o CapCut",
            f"Pasta pronta, já aberta:\n{folder}\n\n{where}\n\nNo CapCut: Novo projeto → Importar → "
            "selecione todos os arquivos da pasta → arraste para a linha do tempo (entram na ordem pelo "
            "número) → arraste a 00_musica.m4a para a faixa de áudio. A ordem e os tempos estão em ORDEM.txt.")

    def _rendered(self, out: dict) -> None:
        self.beat.finish("Pronto.")
        self.render_btn.setEnabled(True)
        self.capcut_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setVisible(False)
        self.rendered.emit(out)
        box = QMessageBox(self)
        box.setWindowTitle("Edição manual")
        box.setText(f"Vídeo pronto:\n{out['video']}")
        watch = box.addButton("Assistir", QMessageBox.AcceptRole)
        folder = box.addButton("Abrir pasta", QMessageBox.ActionRole)
        box.addButton("Fechar", QMessageBox.RejectRole)
        box.exec()
        from PySide6.QtGui import QDesktopServices
        if box.clickedButton() is watch:
            QDesktopServices.openUrl(QUrl.fromLocalFile(out["video"]))
        elif box.clickedButton() is folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(out["video"])))

    def _render_failed(self, msg: str) -> None:
        self.beat.finish("Cancelado." if "cancel" in msg else "Falhou.", ok=False)
        self.render_btn.setEnabled(True)
        self.capcut_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setVisible(False)
        if "cancel" not in msg:
            QMessageBox.critical(self, "Edição manual", msg)

    def closeEvent(self, e):
        self._closing = True
        if self._redo_thread is not None and self._redo_thread.isRunning():
            self._redo_thread.quit()
            self._redo_thread.wait(10000)
        self._proxy_pool.clear()
        self._pool.clear()
        self._pool.waitForDone(5000)
        self._remember()
        if any(p.edited for p in self.plans.values()):
            self.store.save()
        self._save_layout()
        self.preview.stop()
        super().closeEvent(e)


class _EmptyPlan:
    """Stand-in used to build the clip table for a saved edit."""
    shots: list = []
    total = 0.0

