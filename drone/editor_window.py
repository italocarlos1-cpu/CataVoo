# SPDX-License-Identifier: AGPL-3.0-or-later
"""Edição manual — the timeline window.

One lane per raw clip (its full length, with a filmstrip from the analysis
thumbnails). Grey blocks are the candidates the analysis found; coloured,
numbered blocks are the shots in the film. Drag a coloured block's edge to trim
it, drag its middle to slide it inside the clip; double-click a grey block to
add it. The strip at the bottom is the film's order (drag to reorder, Delete to
remove). Switching *Estilo* shows that style's automatic cut instantly — no
rendering — and keeps your edits per style (✎ marks the edited ones).

Keyboard (timeline focused, a shot selected):
    ← / →          active edge ±1 frame (fine adjustment, ignores the ímã)
    Shift + ← / →  ±1 beat with the ímã on, ±1 s with it off
    [  /  ]        choose the in / out edge          Tab  toggle edge
    Alt + ← / →    slide the whole shot ±1 frame     Delete  remove the shot
    ↑ / ↓          previous / next shot              Espaço  play the shot

The window only edits; analysis and rendering go through drone.pipeline.Session.
"""
from __future__ import annotations

import colorsys
import os
import traceback

import numpy as np
from PySide6.QtCore import QEvent, QObject, QRectF, QRunnable, QSize, Qt, QThread, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QGraphicsItem,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem,
                               QGraphicsView, QHBoxLayout, QLabel, QListView, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QSlider, QSplitter, QVBoxLayout, QWidget)

from drone.config import STYLES
from drone.edit_session import EditPlan, EditStore, fmt_time
from drone.landscape import LABEL_PT

LABEL_W = 170          # px: clip names column
LANE_H = 64
LANE_GAP = 8
RULER_H = 26
EDGE_PX = 7            # grab zone of a shot's edge
TAGS = [("16x9", "Horizontal 16:9"), ("9x16", "Vertical 9:16")]


def _colour(category: str) -> QColor:
    h = (abs(hash(category)) % 360) / 360.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.55, 0.92)
    return QColor(int(r * 255), int(g * 255), int(b * 255))


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
            bgr = grab_frame(self.path, self.t, width=480)
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
        self.setBrush(QBrush(colour))
        self.setPen(QPen(QColor(30, 30, 30), 1))
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
        t.setPos(rect.x() + 4, rect.y() + 3)
        t.setBrush(QBrush(QColor(15, 15, 15)))

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
        self.setBrush(QBrush(QColor(215, 215, 215, 150)))
        self.setPen(QPen(QColor(245, 245, 245), 1.5, Qt.DashLine))
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

    def mousePressEvent(self, e):
        self.setFocus()
        super().mousePressEvent(e)


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

        root = QWidget()
        lay = QVBoxLayout(root)

        # ---- top bar
        top = QHBoxLayout()
        self.group_box = QComboBox()
        for g in session.groups:
            self.group_box.addItem(g.label, g.key)
        top.addWidget(QLabel("Grupo:"))
        top.addWidget(self.group_box)
        self.group_box.setVisible(len(session.groups) > 1)
        top.itemAt(0).widget().setVisible(len(session.groups) > 1)
        top.addWidget(QLabel("Estilo:"))
        self.style_box = QComboBox()
        self.style_box.setMinimumWidth(320)
        top.addWidget(self.style_box)
        top.addWidget(QLabel("Formato:"))
        self.tag_box = QComboBox()
        for k, lbl in TAGS:
            self.tag_box.addItem(lbl, k)
        self.tag_box.setCurrentIndex(max(0, self.tag_box.findData(tag)))
        top.addWidget(self.tag_box)
        self.magnet = QCheckBox("🧲 Ímã na batida")
        self.magnet.setChecked(True)
        self.magnet.setToolTip("Ligado: ao arrastar, o corte pula de batida em batida da música.\n"
                               "As setas ←/→ continuam ajustando quadro a quadro.")
        top.addWidget(self.magnet)
        top.addWidget(QLabel("Zoom:"))
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(3, 120)
        self.zoom.setValue(12)
        self.zoom.setFixedWidth(160)
        top.addWidget(self.zoom)
        fit = QPushButton("Ajustar")
        fit.setToolTip("Zoom para caber o vídeo mais longo na largura")
        fit.clicked.connect(lambda: self.fit_zoom())
        top.addWidget(fit)
        top.addStretch()
        self.total_lbl = QLabel()
        f = QFont()
        f.setBold(True)
        f.setPointSize(11)
        self.total_lbl.setFont(f)
        top.addWidget(self.total_lbl)
        lay.addLayout(top)

        # ---- timeline + preview
        split = QSplitter(Qt.Vertical)
        upper = QSplitter(Qt.Horizontal)
        self.scene = QGraphicsScene()
        self.scene.setBackgroundBrush(QBrush(QColor(24, 24, 28)))
        self.view = TimelineView(self)
        self.view.setScene(self.scene)
        upper.addWidget(self.view)

        side = QWidget()
        sl = QVBoxLayout(side)
        self.info = QLabel("Selecione uma tomada colorida.")
        self.info.setWordWrap(True)
        sl.addWidget(self.info)
        frames = QHBoxLayout()
        self.in_img, self.out_img = QLabel(), QLabel()
        for lbl, cap in ((self.in_img, "Entrada"), (self.out_img, "Saída")):
            box = QVBoxLayout()
            t = QLabel(cap)
            t.setAlignment(Qt.AlignCenter)
            lbl.setFixedSize(240, 135)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("background:#111;color:#888;border:1px solid #333")
            box.addWidget(t)
            box.addWidget(lbl)
            frames.addLayout(box)
        sl.addLayout(frames)
        self.video = None
        self.player = None
        self._play_end = 0.0
        self._can_play = False
        if not os.environ.get("DRONEHIGHLIGHTER_NO_VIDEO_PREVIEW"):
            try:
                from PySide6.QtMultimediaWidgets import QVideoWidget
                self.video = QVideoWidget()
                self.video.setMinimumHeight(200)
                sl.addWidget(self.video, 1)
                self._can_play = True
            except Exception:
                pass
        if not self._can_play:
            sl.addWidget(QLabel("Prévia em vídeo indisponível (QtMultimedia não instalado)."))
        self.play_btn = QPushButton("▶ Tocar tomada (Espaço)")
        self.play_btn.clicked.connect(self.play)
        self.play_btn.setEnabled(self._can_play)
        sl.addWidget(self.play_btn)
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumHeight(120)
        sl.addWidget(self.logbox)
        upper.addWidget(side)
        upper.setStretchFactor(0, 3)
        upper.setStretchFactor(1, 1)
        split.addWidget(upper)

        # ---- sequence strip
        seq = QWidget()
        ql = QVBoxLayout(seq)
        ql.setContentsMargins(0, 0, 0, 0)
        ql.addWidget(QLabel("Sequência do vídeo (arraste para reordenar · Delete remove · clique seleciona):"))
        self.strip = QListWidget()
        self.strip.setViewMode(QListView.IconMode)
        self.strip.setFlow(QListView.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setIconSize(QSize(128, 72))
        self.strip.setFixedHeight(130)
        self.strip.setMovement(QListView.Snap)
        self.strip.setDragDropMode(QAbstractItemView.InternalMove)
        self.strip.setDefaultDropAction(Qt.MoveAction)
        self.strip.setSpacing(4)
        self.strip.model().rowsMoved.connect(lambda *a: QTimer.singleShot(0, self._strip_reordered))
        self.strip.currentRowChanged.connect(self._strip_selected)
        self.strip.installEventFilter(self)
        ql.addWidget(self.strip)
        split.addWidget(seq)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

        # ---- bottom buttons
        bot = QHBoxLayout()
        self.restore_btn = QPushButton("↺ Restaurar automático")
        self.restore_btn.setToolTip("Descarta as edições deste estilo/formato e volta ao corte automático")
        self.snap_btn = QPushButton("🧲 Encaixar tudo na batida")
        self.save_btn = QPushButton("💾 Salvar edição")
        self.render_btn = QPushButton("RENDERIZAR ESTE ESTILO")
        f2 = QFont()
        f2.setBold(True)
        self.render_btn.setFont(f2)
        self.render_btn.setMinimumHeight(36)
        self.cancel_btn = QPushButton("Cancelar render")
        self.cancel_btn.setEnabled(False)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        for w in (self.restore_btn, self.snap_btn, self.save_btn):
            bot.addWidget(w)
        bot.addWidget(self.bar, 1)
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
        lane_y = {}
        for clip, y in lanes:
            lane_y[clip.path] = y
            self.scene.addRect(LABEL_W, y, clip.duration * self.pps, LANE_H,
                               QPen(QColor(70, 70, 70)), QBrush(QColor(35, 35, 40)))
            name = self.scene.addSimpleText(clip.name)
            name.setPos(6, y + 6)
            name.setBrush(QBrush(QColor(230, 230, 230)))
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
                hl.setZValue(4)
                self.scene.addItem(hl)
                ex = self.t_to_x(s.start if self.edge == "start" else s.end)
                edge = self.scene.addLine(ex, y, ex, y + LANE_H, QPen(QColor(255, 210, 0), 3))
                edge.setZValue(5)
        self._fill_strip()
        self._update_info()

    def _fill_strip(self) -> None:
        self.strip.blockSignals(True)
        self.strip.clear()
        for i, s in enumerate(self.plan.shots):
            clip = self.plan.clips.get(s.path)
            icon = QIcon()
            if clip is not None:
                thumbs = self._thumb_list(clip)
                if thumbs:
                    t, pm = min(thumbs, key=lambda tp: abs(tp[0] - s.start - 0.3))
                    icon = QIcon(pm)
            tr = " ⇢" if self.plan.trans_out(i) else ""
            beat = "" if self.plan.on_beat(i) else " ♪✗"
            it = QListWidgetItem(icon, f"#{i + 1} {s.length:.1f}s{tr}{beat}\n{LABEL_PT.get(s.category, s.category)}")
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

    def _frame_ready(self, key: str, img) -> None:
        self._frames[key] = img
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

    def play(self) -> None:
        """Play the selected shot from its in to its out point (muted preview).

        The player is created on first use and without an audio output: the
        preview is silent anyway, and a missing sound device must not stop it."""
        if not self._can_play or self.sel is None:
            return
        if self.player is None:
            try:
                from PySide6.QtMultimedia import QMediaPlayer
                self.player = QMediaPlayer(self)
                self.player.setVideoOutput(self.video)
                self.player.positionChanged.connect(self._play_tick)
            except Exception as exc:  # noqa: BLE001
                self._can_play = False
                self.play_btn.setEnabled(False)
                self.log(f"Prévia indisponível: {exc}")
                return
        s = self.plan.shots[self.sel]
        self.player.setSource(QUrl.fromLocalFile(s.path))
        self._play_end = s.end
        self.player.setPosition(int(s.start * 1000))
        self.player.play()

    def _play_tick(self, pos: int) -> None:
        if self.player is not None and pos >= self._play_end * 1000:
            self.player.pause()

    # ================================================================ editing
    def select(self, i: int, edge: str | None = None) -> None:
        self.sel = i
        if edge in ("start", "end"):
            self.edge = edge
        self.rebuild()

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

    def on_key(self, e) -> bool:
        if self.plan is None or self.sel is None:
            return False
        k, mods = e.key(), e.modifiers()
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
        elif k == Qt.Key_Space:
            self.play()
            return True
        else:
            return False
        self.rebuild()
        return True

    def eventFilter(self, obj, e):
        if obj is self.strip and e.type() == QEvent.KeyPress and e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.strip.currentRow() >= 0:
                self.remove(self.strip.currentRow())
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

    def _scroll_to_sel(self) -> None:
        if self.sel is not None and self.sel < len(getattr(self, "shot_items", [])):
            self.view.ensureVisible(self.shot_items[self.sel], 80, 20)

    def _strip_reordered(self) -> None:
        order = [self.strip.item(r).data(Qt.UserRole) for r in range(self.strip.count())]
        cur = self.strip.currentItem().data(Qt.UserRole) if self.strip.currentItem() else None
        self.plan.reorder(order)
        if cur is not None:
            self.sel = order.index(cur)
        self._changed()

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
        if self.player is not None:
            self.player.stop()
        super().closeEvent(e)


class _EmptyPlan:
    """Stand-in used to build the clip table for a saved edit."""
    shots: list = []
    total = 0.0

