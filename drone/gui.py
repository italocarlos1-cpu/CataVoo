# SPDX-License-Identifier: AGPL-3.0-or-later
"""DroneHighlighter window: choose a folder, a style, click GERAR VÍDEO.

Deliberately one screen with no tabs: settings on the left, run/log/results
on the right. Advanced tuning lives in config.json (see drone.config); the
"Edição manual" button opens the mini editor (drone.editor_window).
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import traceback

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from drone import __version__
from drone.ai_assist import PROVIDERS, AIClient, AIError
from drone.config import STYLES, DroneConfig
from drone.grouping import MODES
from drone.theme import role
from drone import theme
from drone.pipeline import Job, Session, edit_store_path, run

MUSIC_CHOICES = [("Automática (pelo estilo)", ""), ("Instrumental cinematográfica", "cinematografica"),
                 ("Instrumental", "instrumental"), ("Tranquila / natureza", "tranquila"),
                 ("Eletrônica ambiente", "eletronica"), ("Alternativa / indie", "indie"),
                 ("Animada", "animada")]
FORMATS = [("Horizontal 16:9 + Vertical 9:16", (True, True)),
           ("Somente horizontal 16:9", (True, False)), ("Somente vertical 9:16", (False, True))]
RESOLUTIONS = [("4K (3840×2160)", (3840, 2160)), ("Full HD (1920×1080)", (1920, 1080))]


AUTO = "Automática"
SHORTFALL = [("Perguntar", "perguntar"), ("Encurtar o vídeo", "encurtar"),
             ("Esticar as tomadas", "esticar")]


def _seconds(text: str, default: float) -> float:
    """'Automática' → 0 (the material decides); a number → that many seconds."""
    t = (text or "").strip().lower()
    if not t or t.startswith("auto"):
        return 0.0
    try:
        return max(5.0, float(t.replace(",", ".").replace("s", "").strip()))
    except ValueError:
        return default


class Worker(QObject):
    log = Signal(str)
    progress = Signal(float, str)
    finished = Signal(object)
    failed = Signal(str)
    ask = Signal(object)                 # not enough material: the window asks the user

    def __init__(self, job: Job, cfg: DroneConfig):
        super().__init__()
        self.job, self.cfg = job, cfg
        self.cancelled = False
        self._answer = None
        self._answered = threading.Event()

    def decide(self, info: dict):
        """Called on the worker thread; blocks until the window answers."""
        self._answered.clear()
        self.ask.emit(info)
        while not self._answered.wait(0.2):
            if self.cancelled:
                return "cancelar"
        return self._answer

    def answer(self, value) -> None:
        self._answer = value
        self._answered.set()

    def run(self):
        try:
            res = run(self.job, self.cfg, log_fn=self.log.emit,
                      progress_fn=lambda p, t: self.progress.emit(float(p), str(t)),
                      cancel_check=lambda: self.cancelled, decide_fn=self.decide)
            self.finished.emit(res)
        except Exception as exc:  # noqa: BLE001 — shown to the user
            self.log.emit(traceback.format_exc())
            self.failed.emit(str(exc))


class SessionWorker(QObject):
    """Runs the (cached) analysis for the manual editor off the UI thread."""
    log = Signal(str)
    progress = Signal(float, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, job: Job, cfg: DroneConfig):
        super().__init__()
        self.job, self.cfg = job, cfg
        self.cancelled = False

    def run(self):
        try:
            sess = Session(self.job, self.cfg, log_fn=self.log.emit,
                           progress_fn=lambda p, t: self.progress.emit(float(p), str(t)),
                           cancel_check=lambda: self.cancelled)
            self.finished.emit(sess)
        except Exception as exc:  # noqa: BLE001 — shown to the user
            self.log.emit(traceback.format_exc())
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"DroneHighlighter {__version__}")
        self.resize(1240, 820)
        self.settings = QSettings("DroneHighlighter", "DroneHighlighter")
        self.thread = None
        self.worker = None
        self.result = None

        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(16)

        # ---- header
        head = QHBoxLayout()
        title = role(QLabel("DroneHighlighter"), "title")
        sub = role(QLabel(f"v{__version__}  ·  edição automática de vídeos de drone, 100% local"), "muted")
        head.addWidget(title)
        head.addSpacing(12)
        head.addWidget(sub, 0, Qt.AlignBottom)
        head.addStretch()
        outer.addLayout(head)

        cols = QHBoxLayout()
        cols.setSpacing(20)
        outer.addLayout(cols, 1)

        # ---- left: settings (scrolls on small screens)
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(left)
        scroll.setMinimumWidth(520)
        cols.addWidget(scroll, 5)

        def card(name):
            lay.addWidget(role(QLabel(name.upper()), "section"))
            box = role(QFrame(), "card")
            form = QFormLayout(box)
            form.setContentsMargins(16, 14, 16, 14)
            form.setHorizontalSpacing(14)
            form.setVerticalSpacing(10)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lay.addWidget(box)
            return form

        form = card("Vídeos")
        self.folder = QLineEdit(self.settings.value("folder", ""))
        self.folder.setPlaceholderText("Pasta com os vídeos brutos do drone")
        b = QPushButton("Selecionar")
        b.clicked.connect(self._pick_folder)
        row = QHBoxLayout()
        row.addWidget(self.folder)
        row.addWidget(b)
        form.addRow("Pasta", row)
        self.title = QLineEdit(self.settings.value("title", ""))
        self.title.setPlaceholderText("Ex.: Pirenópolis — Goiás")
        form.addRow("Título", self.title)

        form = card("Edição")
        self.style = QComboBox()
        for key, st in STYLES.items():
            self.style.addItem(st.label, key)
            self.style.setItemData(self.style.count() - 1, st.description, Qt.ToolTipRole)
        self.style_hint = role(QLabel(), "muted")
        self.style_hint.setWordWrap(True)
        self.style.currentIndexChanged.connect(
            lambda _i: self.style_hint.setText(STYLES[self.style.currentData()].description))
        self.style.setCurrentIndex(max(0, self.style.findData(self.settings.value("style", "cinematografico"))))
        form.addRow("Estilo", self.style)
        form.addRow("", self.style_hint)
        self.style_hint.setText(STYLES[self.style.currentData()].description)

        self.duration = QComboBox()
        self.duration.setEditable(True)
        for s in (AUTO, "30", "45", "60", "90", "120", "180"):
            self.duration.addItem(s)
        self.duration.setCurrentText(str(self.settings.value("duration", "90")))
        self.duration.setToolTip("Escolha ou digite os segundos. 'Automática': o programa mede "
                                 "quanto material bom existe e decide o tamanho.")
        self.vduration = QComboBox()
        self.vduration.setEditable(True)
        for s in (AUTO, "15", "30", "45", "60", "90"):
            self.vduration.addItem(s)
        self.vduration.setCurrentText(str(self.settings.value("vduration", "45")))
        drow = QHBoxLayout()
        drow.addWidget(role(QLabel("16:9"), "muted"))
        drow.addWidget(self.duration, 1)
        drow.addSpacing(10)
        drow.addWidget(role(QLabel("9:16"), "muted"))
        drow.addWidget(self.vduration, 1)
        form.addRow("Duração (s)", drow)
        self.shortfall = QComboBox()
        for label, key in SHORTFALL:
            self.shortfall.addItem(label, key)
        self.shortfall.setCurrentIndex(max(0, self.shortfall.findData(self.settings.value("shortfall", "perguntar"))))
        self.shortfall.setToolTip("Quando os vídeos não têm material bom suficiente para a duração pedida.")
        form.addRow("Se faltar material", self.shortfall)

        form = card("Música")
        self.music_dir = QLineEdit(self.settings.value("music_dir", ""))
        self.music_dir.setPlaceholderText("Pasta com as suas músicas")
        mb = QPushButton("Selecionar")
        mb.clicked.connect(self._pick_music)
        self.music_kind = QComboBox()
        for label, key in MUSIC_CHOICES:
            self.music_kind.addItem(label, key)
        mrow = QHBoxLayout()
        mrow.addWidget(self.music_dir)
        mrow.addWidget(mb)
        form.addRow("Biblioteca", mrow)
        form.addRow("Tipo", self.music_kind)

        form = card("Saída")
        self.formats = QComboBox()
        for label, v in FORMATS:
            self.formats.addItem(label, v)
        self.resolution = QComboBox()
        for label, v in RESOLUTIONS:
            self.resolution.addItem(label, v)
        form.addRow("Formato", self.formats)
        form.addRow("Resolução", self.resolution)
        self.group_by = QComboBox()
        for key, label in MODES.items():
            self.group_by.addItem(label, key)
        self.group_by.setCurrentIndex(max(0, self.group_by.findData(self.settings.value("group_by", "nada"))))
        self.summary = QCheckBox("Também um vídeo da viagem inteira")
        self.summary.setChecked(self.settings.value("summary", "false") == "true")
        self.group_by.currentIndexChanged.connect(
            lambda _i: self.summary.setEnabled(self.group_by.currentData() != "nada"))
        self.summary.setEnabled(self.group_by.currentData() != "nada")
        form.addRow("Organizar", self.group_by)
        form.addRow("", self.summary)

        form = card("Ajuste fino")
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText(
            "Opcional. Ex.: vídeo cinematográfico de 90 segundos, priorizando "
            "cachoeiras, serras e movimentos suaves, com música instrumental tranquila.")
        self.prompt.setFixedHeight(76)
        form.addRow("Instruções", self.prompt)
        self.use_clip = QCheckBox("Reconhecer paisagens com IA local (CLIP)")
        self.use_clip.setChecked(self.settings.value("use_clip", "true") == "true")
        form.addRow("", self.use_clip)
        lay.addStretch()

        # ---- right: run, progress, log, results
        right = QVBoxLayout()
        right.setSpacing(10)
        cols.addLayout(right, 4)
        self.go = role(QPushButton("GERAR VÍDEO"), "primary")
        self.go.setMinimumHeight(52)
        f = self.go.font()
        f.setPointSize(12)
        self.go.setFont(f)
        self.go.clicked.connect(lambda: self._start())
        self.cancel = QPushButton("Cancelar")
        self.cancel.setEnabled(False)
        self.cancel.setMinimumHeight(52)
        self.cancel.clicked.connect(self._cancel)
        brow = QHBoxLayout()
        brow.addWidget(self.go, 4)
        brow.addWidget(self.cancel, 1)
        right.addLayout(brow)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.status = role(QLabel("Pronto."), "muted")
        right.addWidget(self.bar)
        right.addWidget(self.status)

        right.addWidget(role(QLabel("REGISTRO"), "section"))
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSizeF(9)
        self.logbox.setFont(mono)
        right.addWidget(self.logbox, 1)

        orow = QHBoxLayout()
        self.open_video = QPushButton("▶  Assistir")
        self.open_video.setEnabled(False)
        self.open_video.clicked.connect(self._open_video)
        self.open_folder = QPushButton("Abrir pasta")
        self.open_folder.setEnabled(False)
        self.open_folder.clicked.connect(self._open_folder)
        orow.addWidget(self.open_video)
        orow.addWidget(self.open_folder)
        right.addLayout(orow)

        right.addSpacing(4)
        right.addWidget(self._build_ai_box())
        right.addSpacing(4)
        right.addWidget(role(QLabel("NÃO GOSTOU?"), "section"))
        redo = role(QFrame(), "card")
        rl = QVBoxLayout(redo)
        rl.setContentsMargins(14, 12, 14, 12)
        self.feedback = QLineEdit()
        self.feedback.setPlaceholderText("Ex.: mais cachoeira, menos cidade, cortes mais rápidos")
        self.redo_btn = QPushButton("Refazer")
        self.redo_btn.setToolTip("Gera de novo com os ajustes escritos acima (usa a análise já feita)")
        self.redo_btn.clicked.connect(self._redo)
        self.variant_btn = QPushButton("Outra versão")
        self.variant_btn.setToolTip("Mesma configuração, outras tomadas e outra ordem")
        self.variant_btn.clicked.connect(self._variant)
        for b in (self.redo_btn, self.variant_btn):
            b.setEnabled(False)
        self.manual_btn = QPushButton("Edição manual")
        self.manual_btn.setToolTip("Abre o mini editor: veja e toque os cortes de qualquer estilo sem "
                                   "renderizar, ajuste cada tomada quadro a quadro e renderize quando quiser.")
        self.manual_btn.clicked.connect(self._manual)
        rl.addWidget(self.feedback)
        rrow = QHBoxLayout()
        rrow.addWidget(self.redo_btn)
        rrow.addWidget(self.variant_btn)
        rrow.addWidget(self.manual_btn)
        rl.addLayout(rrow)
        right.addWidget(redo)
        self.editors = []
        self.variation = 0
        self.setCentralWidget(root)

    def _build_ai_box(self) -> QWidget:
        """Optional assistant: one switch; its settings card appears only when on."""
        cfg = DroneConfig.load().ai
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(6)
        self.ai_box = QCheckBox("Assistente de IA (opcional; desligado, tudo roda sem IA)")
        self.ai_box.setChecked(cfg.enabled)
        wl.addWidget(self.ai_box)
        card = role(QFrame(), "card")
        f = QFormLayout(card)
        f.setContentsMargins(14, 12, 14, 12)
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.ai_provider = QComboBox()
        for key, p in PROVIDERS.items():
            self.ai_provider.addItem(p.label, key)
        self.ai_provider.setCurrentIndex(max(0, self.ai_provider.findData(cfg.provider)))
        self.ai_model = QLineEdit(cfg.model)
        self.ai_url = QLineEdit(cfg.base_url)
        self.ai_key = QLineEdit(cfg.api_key)
        self.ai_key.setEchoMode(QLineEdit.Password)
        self.ai_interpret = QCheckBox("Interpretar instruções e ajustes")
        self.ai_interpret.setChecked(cfg.interpret)
        self.ai_direct = QCheckBox("Escolher e ordenar as tomadas")
        self.ai_direct.setChecked(cfg.direct)
        test = QPushButton("Testar conexão")
        test.clicked.connect(self._test_ai)
        self.ai_provider.currentIndexChanged.connect(self._provider_hints)
        f.addRow("Provedor", self.ai_provider)
        f.addRow("Modelo", self.ai_model)
        f.addRow("Endereço", self.ai_url)
        f.addRow("Chave da API", self.ai_key)
        f.addRow("", self.ai_interpret)
        f.addRow("", self.ai_direct)
        row = QHBoxLayout()
        row.addWidget(test)
        row.addStretch()
        f.addRow("", row)
        note = role(QLabel("A IA recebe só texto (instruções e uma tabela dos trechos), nunca imagens "
                           "nem vídeo. A chave fica salva só neste PC."), "muted")
        note.setWordWrap(True)
        f.addRow("", note)
        wl.addWidget(card)
        self._provider_hints()
        self.ai_box.toggled.connect(card.setVisible)
        card.setVisible(self.ai_box.isChecked())
        return wrap

    def _provider_hints(self):
        p = PROVIDERS[self.ai_provider.currentData()]
        self.ai_model.setPlaceholderText(p.default_model or "automático (o modelo carregado)")
        self.ai_url.setPlaceholderText(p.base_url)
        self.ai_key.setEnabled(not p.local or p.key == "compatible")
        self.ai_key.setPlaceholderText(f"ou variável {p.key_env}" if p.key_env else "não precisa")

    def ai_settings(self):
        cfg = DroneConfig.load()
        a = cfg.ai
        a.enabled = self.ai_box.isChecked()
        a.provider = self.ai_provider.currentData()
        a.model = self.ai_model.text().strip()
        a.base_url = self.ai_url.text().strip()
        a.api_key = self.ai_key.text().strip()
        a.interpret = self.ai_interpret.isChecked()
        a.direct = self.ai_direct.isChecked()
        return cfg

    def _test_ai(self):
        cfg = self.ai_settings()
        self.status.setText("Testando a IA…")
        QApplication.processEvents()
        try:
            msg = AIClient(cfg.ai).ping()
            QMessageBox.information(self, "Assistente de IA", msg)
            self.status.setText("IA respondeu.")
        except AIError as exc:
            QMessageBox.warning(self, "Assistente de IA", str(exc))
            self.status.setText("IA indisponível.")

    def _manual(self):
        """Prepare a Session (analysis from the cache when possible) and open the timeline."""
        job = self.build_job()
        if not os.path.isdir(job.folder):
            QMessageBox.warning(self, "DroneHighlighter", "Selecione uma pasta com vídeos.")
            return
        cfg = self.ai_settings()
        cfg.use_clip = self.use_clip.isChecked()
        self._manual_job = job
        self.logbox.clear()
        self.logbox.appendPlainText("✂ Preparando a edição manual (a análise usa o cache quando possível)…")
        self.manual_btn.setEnabled(False)
        self.go.setEnabled(False)
        self.cancel.setEnabled(True)
        self.thread = QThread()
        self.worker = SessionWorker(job, cfg)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.logbox.appendPlainText)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._open_editor)
        self.worker.failed.connect(self._failed)
        self.worker.failed.connect(lambda *_: self.manual_btn.setEnabled(True))
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    def _open_editor(self, session):
        from drone.edit_session import EditStore
        from drone.editor_window import EditorWindow
        self.manual_btn.setEnabled(True)
        self.go.setEnabled(True)
        self.cancel.setEnabled(False)
        self.bar.setValue(1000)
        self.status.setText("Edição manual aberta.")
        job = self._manual_job
        store = EditStore(edit_store_path(session.ctx.out_dir))
        tag = "16x9" if job.horizontal else "9x16"
        ed = EditorWindow(session, style_key=job.style, tag=tag, store=store, title=job.title,
                          main_style=job.style)
        ed.rendered.connect(lambda out: self.logbox.appendPlainText(f"✅ Edição manual: {out['video']}"))
        self.editors.append(ed)
        ed.showMaximized()

    def _redo(self):
        self._start(feedback=self.feedback.text().strip())

    def _variant(self):
        self.variation += 1
        self._start(feedback=self.feedback.text().strip(), variation=self.variation, new_shots=True)

    # -- actions -------------------------------------------------------
    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Pasta com os vídeos brutos", self.folder.text())
        if d:
            self.folder.setText(d)

    def _pick_music(self):
        d = QFileDialog.getExistingDirectory(self, "Biblioteca de músicas", self.music_dir.text())
        if d:
            self.music_dir.setText(d)

    def build_job(self) -> Job:
        dur = _seconds(self.duration.currentText(), STYLES[self.style.currentData()].default_duration)
        vdur = _seconds(self.vduration.currentText(), 45.0)
        h, v = self.formats.currentData()
        tag = self.music_kind.currentData()
        return Job(folder=self.folder.text().strip(), style=self.style.currentData(),
                   duration=dur, vertical_duration=vdur,
                   shortfall=self.shortfall.currentData(), group_by=self.group_by.currentData(),
                   summary=self.summary.isChecked() and self.summary.isEnabled(),
                   horizontal=h, vertical=v, title=self.title.text().strip(),
                   music_dir=self.music_dir.text().strip(), music_tags=[tag] if tag else [],
                   prompt=self.prompt.toPlainText(),
                   horizontal_size=self.resolution.currentData())

    def _start(self, feedback: str = "", variation: int = 0, new_shots: bool = False):
        if feedback is False:          # clicked() passes a bool
            feedback = ""
        job = self.build_job()
        if feedback or variation:
            job.feedback = feedback + (", trocar as tomadas" if new_shots else "")
            job.variation = variation
            job.previous_report = self.result.report if self.result else ""
        else:
            self.variation = 0
        if not os.path.isdir(job.folder):
            QMessageBox.warning(self, "DroneHighlighter", "Selecione uma pasta com vídeos.")
            return
        for k, v in (("folder", job.folder), ("style", job.style), ("duration", self.duration.currentText()),
                     ("vduration", self.vduration.currentText()), ("music_dir", job.music_dir),
                     ("shortfall", job.shortfall), ("group_by", job.group_by),
                     ("summary", "true" if self.summary.isChecked() else "false"),
                     ("title", job.title), ("use_clip", "true" if self.use_clip.isChecked() else "false")):
            self.settings.setValue(k, v)
        cfg = self.ai_settings()
        cfg.save()
        cfg.use_clip = self.use_clip.isChecked()
        self.logbox.clear()
        self.go.setEnabled(False)
        self.cancel.setEnabled(True)
        self.open_video.setEnabled(False)
        self.open_folder.setEnabled(False)
        self.redo_btn.setEnabled(False)
        self.variant_btn.setEnabled(False)
        self.thread = QThread()
        self.worker = Worker(job, cfg)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.logbox.appendPlainText)
        self.worker.progress.connect(self._progress)
        self.worker.ask.connect(self._ask_shortfall)
        self.worker.finished.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    def _ask_shortfall(self, info: dict):
        """Not enough footage for the requested length: let the user choose."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Pouco material")
        where = f"{info['group']} — " if info.get("group") and info["group"] != "Viagem completa" else ""
        box.setText(f"{where}Você pediu {info['asked']:.0f} s ({info['format']}), mas há material bom "
                    f"para cerca de {info['comfortable']:.0f} s no ritmo normal do estilo "
                    f"(até ~{info['maximum']:.0f} s esticando as tomadas).")
        box.setInformativeText("Encurtar: vídeo mais curto, só com as melhores cenas.\n"
                               "Esticar: tomadas mais longas e cenas mais parecidas para chegar "
                               "perto do tempo pedido.")
        shorter = box.addButton(f"Encurtar para {info['shorter']:.0f} s", QMessageBox.AcceptRole)
        stretch = box.addButton("Esticar as tomadas", QMessageBox.ActionRole)
        box.addButton("Cancelar", QMessageBox.RejectRole)
        remember = QCheckBox("Usar esta resposta para os próximos vídeos desta geração")
        box.setCheckBox(remember)
        box.exec()
        clicked = box.clickedButton()
        answer = "encurtar" if clicked is shorter else "esticar" if clicked is stretch else "cancelar"
        if self.worker is not None:
            self.worker.answer((answer, remember.isChecked()))

    def _cancel(self):
        if self.worker:
            self.worker.cancelled = True
            self.status.setText("Cancelando…")

    def _progress(self, pct: float, text: str):
        self.bar.setValue(int(pct * 10))
        self.status.setText(text)

    def _done(self, res):
        self.result = res
        self.go.setEnabled(True)
        self.cancel.setEnabled(False)
        self.bar.setValue(1000)
        self.status.setText(f"Concluído em {res.seconds:.0f}s — {len(res.outputs)} vídeo(s)")
        self.open_video.setEnabled(bool(res.outputs))
        self.open_folder.setEnabled(bool(res.outputs))
        self.redo_btn.setEnabled(bool(res.outputs))
        self.variant_btn.setEnabled(bool(res.outputs))

    def _failed(self, msg: str):
        self.go.setEnabled(True)
        self.cancel.setEnabled(False)
        self.status.setText("Falhou.")
        if "cancel" not in msg:
            QMessageBox.critical(self, "DroneHighlighter", msg)

    def _open_video(self):
        if self.result and self.result.outputs:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.result.outputs[0]))

    def _open_folder(self):
        if self.result and self.result.outputs:
            folder = os.path.dirname(self.result.outputs[0])
            if os.name == "nt":
                subprocess.Popen(["explorer", folder])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))


def _crash_log() -> str:
    """If the program dies, leave a trace the user can send:
    ``%LOCALAPPDATA%\\DroneHighlighter\\erros.log`` — native crashes (faulthandler)
    and Python errors that reach the top (logged instead of lost)."""
    import faulthandler
    import time
    from drone.analysis_cache import default_cache_dir
    path = os.path.join(default_cache_dir(), "erros.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fh = open(path, "a", encoding="utf-8", buffering=1)
        fh.write(f"\n===== DroneHighlighter {__version__} aberto em {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        faulthandler.enable(fh, all_threads=True)
        _crash_log.fh = fh                     # keep it open for the whole run

        def hook(kind, value, tb):
            fh.write("".join(traceback.format_exception(kind, value, tb)))
            fh.flush()
        sys.excepthook = hook
    except OSError:
        pass
    return path


def main() -> int:
    _crash_log()
    from drone.win_console import hide_child_consoles
    hide_child_consoles()                  # no ffmpeg console windows flashing
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("DroneHighlighter")
    theme.apply(app)
    try:
        from PySide6.QtGui import QIcon
        base = getattr(sys, "_MEIPASS", os.path.join(os.path.dirname(__file__), ".."))
        icon = os.path.join(base, "packaging", "drone", "DroneHighlighter.ico")
        if os.path.exists(icon):
            app.setWindowIcon(QIcon(icon))
    except Exception:
        pass
    w = MainWindow()
    w.show()
    selftest = os.environ.get("DRONEHIGHLIGHTER_SELFTEST")
    if selftest:
        # Used by the .exe build: open the window, write a marker, close.
        from PySide6.QtCore import QTimer

        def _done():
            with open(selftest, "w", encoding="utf-8") as fh:
                fh.write(f"ok {__version__} {w.windowTitle()}")
            app.quit()
        QTimer.singleShot(1500, _done)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
