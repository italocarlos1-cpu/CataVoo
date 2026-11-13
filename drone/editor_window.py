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
    Espaço         play / pause the film (from the selected shot)
    T              play only the selected shot      C   play its raw clip
    ← / →          active edge ±1 frame (fine adjustment, ignores the ímã)
    Shift + ← / →  ±1 beat with the ímã on, ±1 s with it off
    [  /  ]        choose the in / out edge          Tab  toggle edge
    Alt + ← / →    slide the whole shot ±1 frame     Delete  remove the shot
    ↑ / ↓          previous / next shot              F11  full screen

The window only edits; analysis and rendering go through drone.pipeline.Session.
"""
from __future__ import annotations

import os
import traceback

import numpy as np
from PySide6.QtCore import QEvent, QObject, QRectF, QRunnable, QSize, Qt, QThread, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFrame,
                               QGraphicsItem, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QLabel,
                               QListView, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSlider,
                               QSplitter, QVBoxLayout, QWidget)

from drone.config import STYLES
from drone.edit_session import EditPlan, EditStore, fmt_time
from drone.landscape import LABEL_PT
from drone.preview_player import Piece, PreviewPlayer
from drone.theme import category_colour, role

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


def _card_icon(pm: QPixmap | None, colour: QColor, w: int = 128, h: int = 72) -> QIcon:
    """Strip card: the thumbnail with the landscape colour as a bar underneath."""
    out = QPixmap(w, h + 5)
    out.fill(QColor(20, 20, 20))
    p = QPainter(out)
    if pm is not None and not pm.isNull():
        sc = pm.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        p.drawPixmap(0, 0, sc, (sc.width() - w) // 2, (sc.height() - h) // 2, w, h)
    p.fillRect(0, h + 1, w, 4, colour)
    p.end()
    return QIcon(out)


class SequenceStrip(QListWidget):
    """The film's order as cards. Dragging a card emits ``moved(from, to)``.

    Qt's own InternalMove only repositions icons in IconMode (the rows never
    change), so the drop is handled here: work out the slot under the mouse
    and let the editor move the shot."""
    moved = Signal(int, int)

    def __init__(self):
        super().__init__()
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(False)
        self.setIconSize(QSize(128, 77))
        self.setGridSize(QSize(146, 124))
        self.setWordWrap(True)
        self.setTextElideMode(Qt.ElideNone)
        self.setMovement(QListView.Snap)
        self.setResizeMode(QListView.Adjust)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setFixedHeight(146)

    def drop_slot(self, pos) -> int:
        """Insert-before index for a drop at ``pos`` (viewport coordinates)."""
        n = self.count()
        for r in range(n):
            rect = self.visualItemRect(self.item(r))
            if pos.x() < rect.center().x():
                return r
        return n

    def dragMoveEvent(self, e):
        if e.source() is self:
            e.setDropAction(Qt.MoveAction)
            e.accept()
        else:
            e.ignore()

    def dropEvent(self, e):
        if e.source() is not self:
            e.ignore()
            return
        src = self.currentRow()
        slot = self.drop_slot(e.position().toPoint() if hasattr(e, "position") else e.pos())
        e.setDropAction(Qt.IgnoreAction)        # we move it ourselves
        e.accept()
        if src < 0:
            return
        dst = slot - 1 if slot > src else slot
        dst = max(0, min(self.count() - 1, dst))
        if dst != src:
            self.moved.emit(src, dst)


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
            out = self.session.render(
                self.gp, self.plan, self.tag,
                progress_fn=lambda i, n, *a: self.progress.emit(100.0 * i / max(1, n), "Renderizando"),
                cancel_check=lambda: self.cancelled)
            self.finished.emit(out)
        except Exception as exc:  # noqa: BLE001 — shown to the user
            self.log.emit(traceback.format_exc())
            self.failed.emit(str(exc))
        finally:
            self.session.set_log(old)


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
        self._press_x = e.scenePos().x()
        self.editor.select(self.index, edge=self._mode if self._mode != "body" else None)
        e.accept()

    def mouseMoveEvent(self, e):
        if self._mode is None:
            return
        x = e.scenePos().x()
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
            self.editor.commit_drag()
        e.accept()

    def contextMenuEvent(self, e):
        self.editor.shot_menu(self.index, e.screenPos())


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
        self.editor.add_candidate(self.cand)

    def contextMenuEvent(self, e):
        self.editor.candidate_menu(self.cand, e.screenPos())


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
        return not isinstance(self.itemAt(e.position().toPoint() if hasattr(e, "position") else e.pos()),
                              (ShotItem, CandidateItem, QGraphicsSimpleTextItem))

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
                 main_style: str = "", parent=None):
        super().__init__(parent)
        self.session, self.store, self.title_text = session, store, title
        self.main_style = main_style or style_key
        self.setWindowTitle("DroneHighlighter — Edição manual")
        self.resize(1400, 900)
        self.plans: dict = {}               # (group, style, tag) → EditPlan
        self.preps: dict = {}
        self.plan: EditPlan | None = None
        self.gp = None
        self.sel: int | None = None
        self.edge = "end"
        self.pps = 12.0
        self._thumbs: dict = {}
        self._frames: dict = {}
        self._pool = QThreadPool.globalInstance()
        self._fsig = _FrameSignals()
        self._fsig.ready.connect(self._frame_ready)
        self._preview_timer = QTimer(self, singleShot=True, interval=140)
        self._preview_timer.timeout.connect(self._request_frames)
        self._render_thread = None
        self._render_worker = None
        session.set_log(self.log)

        self.mode = "seq"                    # what the monitor plays: seq | shot | clip
        self._clip_path = ""
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
        self.style_box.setMinimumWidth(300)
        top.addWidget(self.style_box)
        top.addWidget(role(QLabel("Formato"), "muted"))
        self.tag_box = QComboBox()
        for k, lbl in TAGS:
            self.tag_box.addItem(lbl, k)
        self.tag_box.setCurrentIndex(max(0, self.tag_box.findData(tag)))
        top.addWidget(self.tag_box)
        top.addSpacing(10)
        self.magnet = QCheckBox("Ímã na batida")
        self.magnet.setChecked(True)
        self.magnet.setToolTip("Ligado: ao arrastar, o corte pula de batida em batida da música.\n"
                               "As setas ←/→ continuam ajustando quadro a quadro.")
        top.addWidget(self.magnet)
        top.addStretch()
        self.total_lbl = QLabel()
        f = QFont()
        f.setBold(True)
        self.total_lbl.setFont(f)
        top.addWidget(self.total_lbl)
        lay.addLayout(top)

        # ---- monitor + inspector
        split = QSplitter(Qt.Vertical)
        upper = QSplitter(Qt.Horizontal)

        mon = QWidget()
        ml = QVBoxLayout(mon)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(6)
        self.monitor_title = role(QLabel("SEQUÊNCIA FINAL"), "section")
        ml.addWidget(self.monitor_title)
        self.preview = PreviewPlayer(self)
        self._can_play = self.preview.available
        self.video = self.preview.widget if self._can_play else None
        ml.addWidget(self.preview.widget, 1)
        self.scrub = QSlider(Qt.Horizontal)
        self.scrub.setRange(0, 1000)
        self.scrub.sliderMoved.connect(self._scrubbed)
        self.scrub.sliderPressed.connect(lambda: self._scrubbed(self.scrub.value()))
        ml.addWidget(self.scrub)
        tr = QHBoxLayout()
        tr.setSpacing(8)
        self.prev_btn = role(QPushButton("⏮"), "icon")
        self.prev_btn.setToolTip("Tomada anterior")
        self.play_seq_btn = role(QPushButton("▶"), "icon")
        self.play_seq_btn.setToolTip("Tocar / pausar o vídeo final (Espaço)")
        self.next_btn = role(QPushButton("⏭"), "icon")
        self.next_btn.setToolTip("Próxima tomada")
        self.play_btn = QPushButton("Tocar tomada")
        self.play_btn.setToolTip("Só a tomada selecionada, da entrada à saída (T)")
        self.clip_btn = QPushButton("Tocar clipe original")
        self.clip_btn.setToolTip("O arquivo bruto inteiro da tomada selecionada (C)")
        self.time_lbl = role(QLabel("0:00.0 / 0:00.0"), "muted")
        self.music_chk = QCheckBox("Música")
        self.music_chk.setChecked(True)
        self.music_chk.setToolTip("Tocar a música junto na prévia da sequência")
        for w in (self.prev_btn, self.play_seq_btn, self.next_btn):
            tr.addWidget(w)
        tr.addSpacing(6)
        tr.addWidget(self.time_lbl)
        tr.addStretch()
        tr.addWidget(self.music_chk)
        tr.addWidget(self.play_btn)
        tr.addWidget(self.clip_btn)
        ml.addLayout(tr)
        for w in (self.play_seq_btn, self.play_btn, self.clip_btn, self.scrub):
            w.setEnabled(self._can_play)
        self.prev_btn.clicked.connect(lambda: self._step(-1))
        self.next_btn.clicked.connect(lambda: self._step(1))
        self.play_seq_btn.clicked.connect(self.play_sequence)
        self.play_btn.clicked.connect(self.play)
        self.clip_btn.clicked.connect(self.play_clip)
        self.music_chk.toggled.connect(lambda _on: self._load_program(keep=True))
        self.preview.position_changed.connect(self._play_pos)
        self.preview.piece_changed.connect(self._play_piece)
        self.preview.state_changed.connect(self._play_state)
        self.preview.still_needed.connect(self._want_still)
        upper.addWidget(mon)

        side = role(QFrame(), "card")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 12, 14, 12)
        sl.setSpacing(8)
        sl.addWidget(role(QLabel("TOMADA SELECIONADA"), "section"))
        self.info = QLabel("Selecione uma tomada colorida.")
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.RichText)
        sl.addWidget(self.info)
        frames = QHBoxLayout()
        self.in_img, self.out_img = QLabel(), QLabel()
        for lbl, cap in ((self.in_img, "Entrada"), (self.out_img, "Saída")):
            box = QVBoxLayout()
            t = role(QLabel(cap), "muted")
            lbl.setFixedSize(200, 113)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("background:#000; border-radius:6px")
            box.addWidget(t)
            box.addWidget(lbl)
            frames.addLayout(box)
        frames.addStretch()
        sl.addLayout(frames)
        sl.addWidget(role(QLabel("PAISAGENS"), "section"))
        self.legend = QLabel()
        self.legend.setWordWrap(True)
        self.legend.setTextFormat(Qt.RichText)
        sl.addWidget(self.legend)
        sl.addWidget(role(QLabel("REGISTRO"), "section"))
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        sl.addWidget(self.logbox, 1)
        upper.addWidget(side)
        upper.setStretchFactor(0, 3)
        upper.setStretchFactor(1, 2)
        split.addWidget(upper)

        # ---- sequence strip
        seq = QWidget()
        ql = QVBoxLayout(seq)
        ql.setContentsMargins(0, 0, 0, 0)
        ql.setSpacing(4)
        ql.addWidget(role(QLabel("SEQUÊNCIA  ·  arraste para reordenar  ·  Delete remove"), "section"))
        self.strip = SequenceStrip()
        self.strip.moved.connect(self._strip_moved)
        self.strip.currentRowChanged.connect(self._strip_selected)
        self.strip.installEventFilter(self)
        ql.addWidget(self.strip)
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
        self._split = split
        self._sized = False
        lay.addWidget(split, 1)

        # ---- bottom buttons
        bot = QHBoxLayout()
        self.restore_btn = QPushButton("Restaurar automático")
        self.restore_btn.setToolTip("Descarta as edições deste estilo/formato e volta ao corte automático")
        self.snap_btn = QPushButton("Encaixar tudo na batida")
        self.save_btn = QPushButton("Salvar edição")
        self.render_btn = role(QPushButton("RENDERIZAR"), "primary")
        self.render_btn.setMinimumHeight(38)
        self.render_btn.setMinimumWidth(180)
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setEnabled(False)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        for w in (self.restore_btn, self.snap_btn, self.save_btn):
            bot.addWidget(w)
        bot.addSpacing(10)
        bot.addWidget(self.bar, 1)
        bot.addSpacing(10)
        bot.addWidget(self.cancel_btn)
        bot.addWidget(self.render_btn)
        lay.addLayout(bot)
        self.setCentralWidget(root)

        self.restore_btn.clicked.connect(self.restore)
        self.snap_btn.clicked.connect(self.snap_all)
        self.save_btn.clicked.connect(self.save)
        self.render_btn.clicked.connect(self.render)
        self.cancel_btn.clicked.connect(self._cancel_render)
        self.zoom.valueChanged.connect(self._zoom)
        self.magnet.toggled.connect(self._magnet_toggled)

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

    def _initial_sizes(self) -> None:
        h = max(600, self._split.height())
        lanes = len(self.plan.clips) if self.plan is not None else 4
        tl = min(int(h * 0.42), RULER_H + lanes * (LANE_H + LANE_GAP) + 50)
        tl = max(tl, RULER_H + 2 * (LANE_H + LANE_GAP) + 40)
        strip = 180
        self._split.setSizes([max(260, h - strip - tl), strip, tl])
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
                    _gp, auto_plan = self.session.plan(g, st, tag)
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
        self.plan.magnet = self.magnet.isChecked()
        auto = f" (Automático → {gp.style.label})" if gp.auto else ""
        self.log(f"Estilo {STYLES[st].label}{auto}: {len(plan.shots)} tomadas, {plan.total:.1f} s")
        self.sel = 0 if plan.shots else None
        self.rebuild()
        self.preview.t = 0.0
        self._load_program()

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

    def rebuild(self) -> None:
        self.scene.clear()
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
                    scaled = pm.scaledToHeight(int(th), Qt.SmoothTransformation)
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
        for c in self.plan.candidates:
            if c["path"] not in lane_y or (c["path"], round(c["start"], 2)) in used:
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
            if i == self.sel:
                hl = QGraphicsRectItem(item.rect().adjusted(-2, -2, 2, 2))
                hl.setPen(QPen(QColor(255, 255, 255), 2))
                hl.setBrush(Qt.NoBrush)
                hl.setZValue(4)
                self.scene.addItem(hl)
                ex = self.t_to_x(s.start if self.edge == "start" else s.end)
                edge = self.scene.addLine(ex, y, ex, y + LANE_H, QPen(QColor(255, 255, 255), 3))
                edge.setZValue(5)
        self.playhead = self.scene.addLine(0, 0, 0, LANE_H, QPen(QColor(255, 80, 80), 2))
        self.playhead.setZValue(10)
        self.playhead.setVisible(False)
        self._fill_strip()
        self._fill_legend()
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
        self.strip.blockSignals(True)
        self.strip.clear()
        for i, s in enumerate(self.plan.shots):
            clip = self.plan.clips.get(s.path)
            pm = None
            if clip is not None:
                thumbs = self._thumb_list(clip)
                if thumbs:
                    _t, pm = min(thumbs, key=lambda tp: abs(tp[0] - s.start - 0.3))
            icon = _card_icon(pm, _colour(s.category))
            tr = " ⇢" if self.plan.trans_out(i) else ""
            beat = "" if self.plan.on_beat(i) else " ♪✗"
            it = QListWidgetItem(icon, f"#{i + 1}  {s.length:.1f}s{tr}{beat}\n{LABEL_PT.get(s.category, s.category)}")
            it.setData(Qt.UserRole, i)
            it.setToolTip(f"{os.path.basename(s.path)} {fmt_time(s.start)}–{fmt_time(s.end)}")
            self.strip.addItem(it)
        if self.sel is not None and self.sel < self.strip.count():
            self.strip.setCurrentRow(self.sel)
        self.strip.blockSignals(False)

    def _update_info(self) -> None:
        p = self.plan
        target = f" (automático: {p.target:.1f} s)" if p.target else ""
        off = sum(1 for i in range(len(p.shots)) if not p.on_beat(i))
        beat = f" · {off} fora da batida" if off else " · tudo na batida"
        self.total_lbl.setText(f"Duração: {p.total:.2f} s{target} · {len(p.shots)} tomadas{beat}")
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
            f"No vídeo: {p.cut_times()[self.sel]:.2f} s · avança {p.advance(self.sel):.2f} s "
            f"{'🎵 na batida' if p.on_beat(self.sel) else '⚠ fora da batida'} · "
            f"transição: {'corte seco' if not p.trans_out(self.sel) else f'fusão {p.trans_out(self.sel):.1f} s'}<br>"
            f"Borda ativa: <b>{'entrada' if self.edge == 'start' else 'saída'}</b> ([ / ] ou Tab)")
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
                self._pool.start(_FrameJob(key, s.path, t, self._fsig))

    def _want_still(self, path: str, t: float) -> None:
        """Monitor paused: show the exact frame (grabbed with ffmpeg, cached)."""
        key = f"{path}|{t:.4f}"
        self._monitor_key = key
        if key in self._frames:
            self.preview.show_still(QPixmap.fromImage(self._frames[key]))
        else:
            self._pending = getattr(self, "_pending", {})
            self._pending[key] = "monitor"
            job = _FrameJob(key, path, t, self._fsig)
            job.width = 960
            self._pool.start(job)

    def _frame_ready(self, key: str, img) -> None:
        self._frames[key] = img
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
            self.mode = "seq"
            self.monitor_title.setText("SEQUÊNCIA FINAL")
        path, off = self._music()
        self.preview.set_program(self._sequence_pieces(), path, off)
        if not self.preview.playing:
            self.preview.seek(self.preview.t)
        self._play_pos(self.preview.t)

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

    def play(self) -> None:
        """Play only the selected shot, in → out."""
        if not self._can_play or self.sel is None or self.plan is None:
            return
        s = self.plan.shots[self.sel]
        self.mode = "shot"
        self.monitor_title.setText(f"TOMADA #{self.sel + 1}  ·  {os.path.basename(s.path)}")
        self.preview.set_program([Piece(s.path, s.start, s.end, 0.0)])
        self.preview.play(0.0)

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
        self._clip_path = path
        self.monitor_title.setText(f"CLIPE ORIGINAL  ·  {clip.name}")
        self.preview.set_program([Piece(path, 0.0, clip.duration, 0.0)])
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
                    self._clip_path = path
                    self.monitor_title.setText(f"CLIPE ORIGINAL  ·  {clip.name}")
                    self.preview.set_program([Piece(path, 0.0, clip.duration, 0.0)])
                    self.preview.seek(t)
                return

    def _scrubbed(self, v: int) -> None:
        d = self.preview.duration
        if d > 0:
            self.preview.seek(d * v / 1000.0)

    def _play_pos(self, t: float) -> None:
        d = self.preview.duration
        if not self.scrub.isSliderDown():
            self.scrub.blockSignals(True)
            self.scrub.setValue(int(1000 * t / d) if d > 0 else 0)
            self.scrub.blockSignals(False)
        self.time_lbl.setText(f"{_clock(t)} / {_clock(d)}")
        self._move_playhead(t)

    def _play_piece(self, i: int) -> None:
        if self.mode == "seq" and 0 <= i < self.strip.count():
            self.strip.blockSignals(True)
            self.strip.setCurrentRow(i)
            self.strip.blockSignals(False)
            self.strip.scrollToItem(self.strip.item(i))

    def _play_state(self, playing: bool) -> None:
        self.play_seq_btn.setText("⏸" if playing and self.mode == "seq" else "▶")
        if self.playhead is not None:
            self.playhead.setVisible(playing or self.mode != "seq")

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
        self.rebuild()
        self._scroll_to_sel()
        if self.mode == "seq":
            self.preview.seek(self.plan.cut_times()[i])

    # ================================================================ editing
    def select(self, i: int, edge: str | None = None) -> None:
        changed = i != self.sel
        self.sel = i
        if edge in ("start", "end"):
            self.edge = edge
        self.rebuild()
        if changed:
            self._cue_selected()

    def drag_edge(self, i: int, edge: str, t: float) -> None:
        self.plan.trim(i, edge, t, snap=self.magnet.isChecked())
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
        self._update_info()

    def commit_drag(self) -> None:
        self._changed()

    def _changed(self) -> None:
        self.rebuild()
        self._fill_styles()
        self._load_program()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
            return
        if e.key() == Qt.Key_Escape and self.isFullScreen():
            self.showMaximized()
            return
        if e.key() == Qt.Key_Space:
            self.play_sequence()
            return
        super().keyPressEvent(e)

    def on_key(self, e) -> bool:
        k, mods = e.key(), e.modifiers()
        if k == Qt.Key_Space:
            self.play_sequence()
            return True
        if k == Qt.Key_F11:
            self.keyPressEvent(e)
            return True
        if self.plan is None or self.sel is None:
            return False
        if k == Qt.Key_T:
            self.play()
            return True
        if k == Qt.Key_C:
            self.play_clip()
            return True
        if k in (Qt.Key_Left, Qt.Key_Right):
            sign = -1 if k == Qt.Key_Left else 1
            if mods & Qt.AltModifier:
                self.plan.slide(self.sel, sign * self.plan.frame(self.plan.shots[self.sel].path))
            elif mods & Qt.ShiftModifier:
                if self.magnet.isChecked():
                    self.plan.nudge(self.sel, self.edge, beats=sign)
                else:
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
        self.rebuild()
        return True

    def eventFilter(self, obj, e):
        if obj is self.strip and e.type() == QEvent.KeyPress:
            if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.strip.currentRow() >= 0:
                self.remove(self.strip.currentRow())
                return True
            if e.key() == Qt.Key_Space:
                self.play_sequence()
                return True
        return super().eventFilter(obj, e)

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
        snap = m.addAction("Encaixar esta tomada na batida")
        m.addSeparator()
        rem = m.addAction("Remover do vídeo")
        last = i == len(self.plan.shots) - 1
        cut.setEnabled(not last)
        fade.setEnabled(not last)
        chosen = m.exec(pos)
        if chosen is cut:
            self.plan.set_transition(i, "cut")
        elif chosen is fade:
            self.plan.set_transition(i, "crossfade", 0.6)
        elif chosen is snap:
            self.plan.trim(i, "end", self.plan.shots[i].end, snap=True)
        elif chosen is rem:
            self.remove(i)
            return
        else:
            return
        self._changed()

    def _strip_selected(self, row: int) -> None:
        if row >= 0 and row != self.sel:
            self.sel = row
            self.rebuild()
            self._scroll_to_sel()
            self._cue_selected()

    def _cue_selected(self) -> None:
        """Paused on the film: show the selected shot's first frame in the monitor."""
        if self.mode == "seq" and not self.preview.playing and self.sel is not None and self._can_play:
            self.preview.seek(self.plan.cut_times()[self.sel])

    def _scroll_to_sel(self) -> None:
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

    def _magnet_toggled(self, on: bool) -> None:
        if self.plan is not None:
            self.plan.magnet = on

    def snap_all(self) -> None:
        n = self.plan.snap_all()
        self.log(f"🧲 {n} tomada(s) encaixada(s) na batida")
        self._changed()

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
    def render(self) -> None:
        if self.plan is None or not self.plan.shots:
            return
        off = sum(1 for i in range(len(self.plan.shots)) if not self.plan.on_beat(i))
        if off and self.magnet.isChecked():
            ans = QMessageBox.question(
                self, "Ímã", f"{off} tomada(s) estão fora da batida (ajuste fino por quadro).\n"
                             "Encaixar tudo na batida antes de renderizar?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if ans == QMessageBox.Cancel:
                return
            if ans == QMessageBox.Yes:
                self.plan.snap_all()
                self._changed()
        self.save()
        plan = self.plan.to_plan(self.gp.music, self.title_text)
        self.bar.setValue(0)
        self.render_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self._render_thread = QThread()
        self._render_worker = _RenderWorker(self.session, self.gp, plan, self.plan.tag)
        self._render_worker.moveToThread(self._render_thread)
        self._render_thread.started.connect(self._render_worker.run)
        self._render_worker.log.connect(self.log)
        self._render_worker.progress.connect(lambda p, t: self.bar.setValue(int(p * 10)))
        self._render_worker.finished.connect(self._rendered)
        self._render_worker.failed.connect(self._render_failed)
        self._render_worker.finished.connect(self._render_thread.quit)
        self._render_worker.failed.connect(self._render_thread.quit)
        self._render_thread.start()

    def _cancel_render(self) -> None:
        if self._render_worker is not None:
            self._render_worker.cancelled = True

    def _rendered(self, out: dict) -> None:
        self.bar.setValue(1000)
        self.render_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.rendered.emit(out)
        box = QMessageBox(self)
        box.setWindowTitle("Edição manual")
        box.setText(f"Vídeo pronto:\n{out['video']}")
        watch = box.addButton("Assistir", QMessageBox.AcceptRole)
        box.addButton("Fechar", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is watch:
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(out["video"]))

    def _render_failed(self, msg: str) -> None:
        self.render_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if "cancel" not in msg:
            QMessageBox.critical(self, "Edição manual", msg)

    def closeEvent(self, e):
        self._remember()
        if any(p.edited for p in self.plans.values()):
            self.store.save()
        self.preview.stop()
        super().closeEvent(e)


class _EmptyPlan:
    """Stand-in used to build the clip table for a saved edit."""
    shots: list = []
    total = 0.0

