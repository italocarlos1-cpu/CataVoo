# SPDX-License-Identifier: AGPL-3.0-or-later
"""DroneHighlighter window: choose a folder, a style, click GERAR VÍDEO.

One screen, no tabs: settings on the left; on the right, ANALISAR MATERIAL
(the footage is analysed once and cached), then what there is, the length
(suggested, or any other) and GERAR PRÉVIA, which opens the preview/editor
(drone.editor_window). Nothing is rendered until the user asks for it there.
"""
from __future__ import annotations

import os
import sys
import traceback

from PySide6.QtCore import QObject, QSettings, Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from drone import __version__
from drone.ai_assist import PROVIDERS, AIClient, AIError
from drone.config import STYLES, DroneConfig
from drone.grouping import MODES
from drone.theme import role
from drone.progress_ui import Heartbeat
from drone import theme
from drone.pipeline import Job, Session, edit_store_path

MUSIC_CHOICES = [("Automática (pelo estilo)", ""), ("Instrumental cinematográfica", "cinematografica"),
                 ("Instrumental", "instrumental"), ("Tranquila / natureza", "tranquila"),
                 ("Eletrônica ambiente", "eletronica"), ("Alternativa / indie", "indie"),
                 ("Animada", "animada")]
FORMATS = [("Horizontal 16:9 + Vertical 9:16", (True, True)),
           ("Somente horizontal 16:9", (True, False)), ("Somente vertical 9:16", (False, True))]
RESOLUTIONS = [("4K (3840×2160)", (3840, 2160)), ("Full HD (1920×1080)", (1920, 1080))]


class SessionWorker(QObject):
    """Analyses the footage (cached) off the UI thread and measures the material."""
    log = Signal(str)
    progress = Signal(float, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, job: Job, cfg: DroneConfig, measure: bool = True):
        super().__init__()
        self.job, self.cfg, self.measure = job, cfg, measure
        self.cancelled = False

    def run(self):
        try:
            sess = Session(self.job, self.cfg, log_fn=self.log.emit,
                           progress_fn=lambda p, t: self.progress.emit(float(p), str(t)),
                           cancel_check=lambda: self.cancelled)
            sess.material = []               # (group label, raw seconds, estimate)
            if self.measure:
                for g in sess.groups:
                    gp = sess.prepare(g.key, self.job.style)
                    sess.material.append((g.label, sess.raw_seconds(g.key), gp.est))
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
        form.addRow("Organizar", self.group_by)
        form.addRow("", self.summary)
        # Only meaningful with groups: hidden for "Um vídeo com tudo".
        def _summary_row(_i=0, _form=form):     # _form: "form" is reused by the next card
            _form.setRowVisible(self.summary, self.group_by.currentData() != "nada")
        self.group_by.currentIndexChanged.connect(_summary_row)
        _summary_row()

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
        self.go = role(QPushButton("ANALISAR MATERIAL"), "primary")
        self.go.setToolTip("Analisa os vídeos da pasta (uma vez só; fica guardado) e mostra quanto material bom existe.\n"
                           "Nenhum vídeo é gerado ainda: você escolhe a duração depois de ver o resultado.")
        self.go.setMinimumHeight(52)
        f = self.go.font()
        f.setPointSize(12)
        self.go.setFont(f)
        self.go.clicked.connect(lambda: self._analyse())
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
        self.bar.setProperty("role", "thick")
        self.status = role(QLabel("Pronto."), "muted")
        self.status.setWordWrap(True)          # a long line must not widen the window
        self.pct = QLabel("")
        f = self.pct.font()
        f.setBold(True)
        f.setPointSize(13)
        self.pct.setFont(f)
        self.pct.setMinimumWidth(56)
        self.pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        prow = QHBoxLayout()
        prow.addWidget(self.bar, 1)
        prow.addWidget(self.pct)
        right.addLayout(prow)
        right.addWidget(self.status)
        self.beat = Heartbeat(self.bar, self.status, self.pct, self)

        right.addWidget(role(QLabel("REGISTRO"), "section"))
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSizeF(9)
        self.logbox.setFont(mono)
        right.addWidget(self.logbox, 1)

        # ---- after the analysis: what there is, how long, and the preview
        self.result_card = role(QFrame(), "card")
        rl = QVBoxLayout(self.result_card)
        rl.setContentsMargins(16, 14, 16, 14)
        rl.setSpacing(10)
        self.material_hint = QLabel("")
        self.material_hint.setWordWrap(True)
        self.material_hint.setTextFormat(Qt.RichText)
        rl.addWidget(self.material_hint)
        drow = QHBoxLayout()
        drow.setSpacing(10)
        drow.addWidget(role(QLabel("Duração"), "muted"))
        self.duration = QComboBox()
        self.duration.setEditable(True)
        self.duration.setInsertPolicy(QComboBox.NoInsert)
        self.duration.setMinimumWidth(170)
        self.duration.setToolTip("A sugestão do programa já vem escolhida. Escolha outro tamanho ou digite o que quiser\n"
                                 "(90, 1:30, 4:00…). Dá para mudar de novo depois, dentro da prévia.")
        drow.addWidget(self.duration)
        self.preview_btn = role(QPushButton("GERAR PRÉVIA"), "primary")
        self.preview_btn.setMinimumHeight(44)
        self.preview_btn.setToolTip("Abre a prévia: o vídeo montado toca na hora, sem renderizar. Lá você muda o estilo e a\n"
                                    "duração, refaz, pede outra versão, ajusta as tomadas e só então renderiza o vídeo final.")
        self.preview_btn.clicked.connect(self._preview)
        drow.addWidget(self.preview_btn, 1)
        rl.addLayout(drow)
        self.result_card.setVisible(False)
        right.addWidget(self.result_card)

        right.addSpacing(4)
        right.addWidget(self._build_ai_box())
        self.editors = []
        self.session = None
        self._session_key = None
        self._open_after = False
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

    def _job_key(self, job: Job) -> tuple:
        """What an analysis depends on (the style and the length are chosen later)."""
        return (job.folder, job.group_by, job.summary, job.horizontal, job.vertical,
                tuple(job.horizontal_size or ()), job.music_dir, tuple(job.music_tags), job.title,
                job.prompt, self.use_clip.isChecked(), self.ai_box.isChecked())

    def _analyse(self, then_open: bool = False):
        """Analyse the folder (cached) and show how much good material there is."""
        job = self.build_job()
        if not os.path.isdir(job.folder):
            QMessageBox.warning(self, "DroneHighlighter", "Selecione uma pasta com vídeos.")
            return
        for k, v in (("folder", job.folder), ("style", job.style), ("music_dir", job.music_dir),
                     ("group_by", job.group_by),
                     ("summary", "true" if self.summary.isChecked() else "false"),
                     ("title", job.title), ("use_clip", "true" if self.use_clip.isChecked() else "false")):
            self.settings.setValue(k, v)
        cfg = self.ai_settings()
        cfg.save()
        cfg.use_clip = self.use_clip.isChecked()
        self._job, self._open_after = job, then_open
        self.logbox.clear()
        self.logbox.appendPlainText(
            "🔎 Analisando o material (fica guardado: os vídeos não são processados de novo depois)…")
        self.go.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.cancel.setEnabled(True)
        self.thread = QThread()
        self.worker = SessionWorker(job, cfg)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.logbox.appendPlainText)
        self.worker.log.connect(self.beat.touch)
        self.worker.progress.connect(self._progress)
        self.beat.start("Analisando o material…")
        self.worker.finished.connect(self._show_material)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    def _show_material(self, session):
        """The analysis is done: what there is, the suggested length, the preview button."""
        from drone import material
        self.session, self._session_key = session, self._job_key(self._job)
        self.go.setEnabled(True)
        self.preview_btn.setEnabled(True)
        self.cancel.setEnabled(False)
        self.beat.finish("Material analisado. Escolha a duração e clique em GERAR PRÉVIA.")
        ck = material.clock
        vertical = self._job.vertical and not self._job.horizontal
        for label, raw, est in session.material:
            top = max(est.maximum, est.comfortable)
            self.logbox.appendPlainText(
                f"📊 {label}: {ck(raw)} brutos → ~{ck(est.comfortable)} de material bom sem repetir "
                f"({est.distinct} tomadas, {est.strong} fortes); até {ck(top)} esticando. "
                f"Sugestão: {ck(est.suggested_vertical if vertical else est.suggested)}.")
        usable = [m for m in session.material if m[2].distinct]
        if not usable:
            self.material_hint.setText("Nenhum trecho aproveitável foi encontrado nesta pasta.")
            self.duration.clear()
            self.preview_btn.setEnabled(False)
            self.result_card.setVisible(True)
            return
        label, raw, est = max(usable, key=lambda m: m[1])
        top = max(est.maximum, est.comfortable)
        sug = est.suggested_vertical if vertical else est.suggested
        more = f" · {label}" if len(session.material) > 1 else ""
        self.material_hint.setText(
            f"<b>{ck(raw)}</b> de filmagem bruta{more}<br>"
            f"<b>~{ck(est.comfortable)}</b> de material bom, sem repetir cenas "
            f"({est.distinct} tomadas, {est.strong} fortes)<br>"
            f"até <b>{ck(top)}</b> esticando as tomadas"
            + ("<br><span style='color:#8c8c8c'>Com vários grupos, cada um usa a própria sugestão; "
               "a duração escolhida aqui vale para o primeiro que abrir.</span>" if len(session.material) > 1 else ""))
        self.duration.clear()
        for sec in sorted({*material.choices(est, vertical), float(round(sug)), float(round(top))}):
            mark = "sugerida" if round(sec) == round(sug) else "máximo" if round(sec) == round(top) else ""
            self.duration.addItem(ck(sec) + (f"  ·  {mark}" if mark else ""), sec)
        self.duration.setCurrentIndex(max(0, self.duration.findData(float(round(sug)))))
        self.result_card.setVisible(True)
        if self._open_after:
            self._open_after = False
            self._preview()

    def _preview(self):
        """Open the preview/editor on the analysed material, at the chosen length."""
        from drone import material
        from drone.edit_session import EditStore
        from drone.editor_window import EditorWindow
        job = self.build_job()
        if self.session is None or self._job_key(job) != self._session_key:
            self._analyse(then_open=True)        # something changed: analyse (from the cache) first
            return
        session = self.session
        self.session = None                      # a session belongs to one editor; the next one is re-read
        self._session_key = None
        target = material.parse_clock(self.duration.currentText())
        store = EditStore(edit_store_path(session.ctx.out_dir))
        tag = "16x9" if job.horizontal else "9x16"
        ed = EditorWindow(session, style_key=job.style, tag=tag, store=store, title=job.title,
                          main_style=job.style, target=target)
        ed.rendered.connect(lambda out: self.logbox.appendPlainText(f"✅ Vídeo renderizado: {out['video']}"))
        self.editors.append(ed)
        self.beat.finish("Prévia aberta.")
        ed.showMaximized()

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
        h, v = self.formats.currentData()
        tag = self.music_kind.currentData()
        # No length here: it is chosen after the analysis (0 = the material decides).
        return Job(folder=self.folder.text().strip(), style=self.style.currentData(),
                   duration=0.0, vertical_duration=0.0,
                   shortfall="esticar", group_by=self.group_by.currentData(),
                   summary=self.summary.isChecked() and self.group_by.currentData() != "nada",
                   horizontal=h, vertical=v, title=self.title.text().strip(),
                   music_dir=self.music_dir.text().strip(), music_tags=[tag] if tag else [],
                   prompt=self.prompt.toPlainText(),
                   horizontal_size=self.resolution.currentData())

    def _cancel(self):
        if self.worker:
            self.worker.cancelled = True
            self.beat.update(self.beat.value, "Cancelando… (termina o passo atual)")

    def _progress(self, pct: float, text: str):
        self.beat.update(pct, text)

    def _failed(self, msg: str):
        self.go.setEnabled(True)
        self.preview_btn.setEnabled(self.session is not None)
        self.cancel.setEnabled(False)
        self.beat.finish("Cancelado." if "cancel" in msg else "Falhou.", ok=False)
        if "cancel" not in msg:
            QMessageBox.critical(self, "DroneHighlighter", msg)


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


def _clip_selftest() -> str:
    """For the build check: is the CLIP pack found and does it classify?"""
    try:
        import numpy as np
        from drone.landscape import ClipScorer
        if not ClipScorer.available():
            return "clip ausente"
        sc = ClipScorer(log_fn=lambda *_: None)
        sc.load()
        img = np.zeros((224, 224, 3), dtype=np.uint8)
        img[:112] = (230, 160, 60)
        probs = sc.probs([img])[0]
        return f"clip ok {getattr(sc._pf, 'device', '?')} {np.asarray(probs).shape}"
    except Exception as exc:  # noqa: BLE001 — reported, not raised
        return f"clip erro {type(exc).__name__}: {exc}"


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
            lines = [f"ok {__version__} {w.windowTitle()}"]
            if os.environ.get("DRONEHIGHLIGHTER_SELFTEST_CLIP"):
                lines.append(_clip_selftest())
            with open(selftest, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
            app.quit()
        QTimer.singleShot(1500, _done)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
