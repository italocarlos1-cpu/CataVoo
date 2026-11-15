"""Dialog to write the text of one shot: box left or centred, basic fonts,
filled or transparent background, text and background colours — with a live
preview on the shot's own frame."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QFormLayout, QHBoxLayout,
                               QLabel, QPlainTextEdit, QPushButton, QVBoxLayout)

from drone import text_overlay as T

FILLS = [("Preenchido", True), ("Transparente", False)]


class TextDialog(QDialog):
    """``exec()`` → Accepted with ``result_spec`` ({} removes the text)."""

    def __init__(self, parent, spec: dict, frame: QPixmap | None, shot_no: int, vertical: bool):
        super().__init__(parent)
        self.setWindowTitle(f"Texto da tomada #{shot_no}")
        self.frame, self.vertical = frame, vertical
        self.spec = dict(T.DEFAULT)
        self.spec.update(spec or {})
        self.result_spec: dict = {}

        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        left.addWidget(QLabel("Texto (Enter quebra a linha; cada linha ganha a sua caixa)"))
        self.edit = QPlainTextEdit(self.spec["text"])
        self.edit.setPlaceholderText("DICAS DE DRONE ESSENCIAIS!")
        self.edit.setMinimumSize(320, 90)
        self.edit.textChanged.connect(self._changed)
        left.addWidget(self.edit)
        form = QFormLayout()
        self.align = self._combo(T.ALIGNS, self.spec["align"])
        self.pos = self._combo(T.POSITIONS, self.spec["pos"])
        self.font = self._combo([(f, f) for f in T.FONTS], self.spec["font"])
        self.size = self._combo(T.SIZE_LABELS, self.spec["size"])
        self.fill = self._combo(FILLS, bool(self.spec["fill"]))
        form.addRow("Alinhamento", self.align)
        form.addRow("Posição", self.pos)
        form.addRow("Fonte", self.font)
        form.addRow("Tamanho", self.size)
        st = QHBoxLayout()
        self.bold = QCheckBox("Negrito")
        self.bold.setChecked(bool(self.spec["bold"]))
        self.upper = QCheckBox("MAIÚSCULAS")
        self.upper.setChecked(bool(self.spec["upper"]))
        for c in (self.bold, self.upper):
            c.toggled.connect(self._changed)
            st.addWidget(c)
        st.addStretch()
        form.addRow("Estilo", st)
        self.color_btn = QPushButton()
        self.color_btn.clicked.connect(lambda: self._pick("color"))
        form.addRow("Cor do texto", self.color_btn)
        form.addRow("Fundo", self.fill)
        self.bg_btn = QPushButton()
        self.bg_btn.clicked.connect(lambda: self._pick("bg_color"))
        form.addRow("Cor do fundo", self.bg_btn)
        left.addLayout(form)
        btns = QHBoxLayout()
        self.remove_btn = QPushButton("Remover texto")
        self.remove_btn.clicked.connect(self._remove)
        self.remove_btn.setVisible(bool(T.normalize(spec)))
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Aplicar")
        ok.setProperty("role", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self._apply)
        btns.addWidget(self.remove_btn)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(ok)
        left.addLayout(btns)
        lay.addLayout(left)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background:#000; border-radius:6px")
        self.preview.setFixedSize(*(220, 390) if vertical else (400, 225))
        lay.addWidget(self.preview, 0, Qt.AlignTop)
        self._changed()

    def _combo(self, items, current) -> QComboBox:
        box = QComboBox()
        for label, data in items:
            box.addItem(label, data)
        box.setCurrentIndex(max(0, box.findData(current)))
        box.currentIndexChanged.connect(self._changed)
        return box

    def _pick(self, key: str) -> None:
        c = QColorDialog.getColor(QColor(self.spec[key]), self, "Escolha a cor")
        if c.isValid():
            self.spec[key] = c.name()
            self._changed()

    def _read(self) -> dict:
        self.spec.update(text=self.edit.toPlainText(), align=self.align.currentData(),
                         pos=self.pos.currentData(), font=self.font.currentData(),
                         size=self.size.currentData(), fill=self.fill.currentData(),
                         bold=self.bold.isChecked(), upper=self.upper.isChecked())
        return self.spec

    def _changed(self, *_a) -> None:
        spec = self._read()
        for btn, key in ((self.color_btn, "color"), (self.bg_btn, "bg_color")):
            btn.setText(spec[key])
            fg = "#000" if QColor(spec[key]).lightness() > 140 else "#fff"
            btn.setStyleSheet(f"background:{spec[key]}; color:{fg}")
        self.bg_btn.setEnabled(bool(spec["fill"]))
        w, h = self.preview.width(), self.preview.height()
        base = QPixmap(w, h)
        base.fill(Qt.black)
        p = QPainter(base)
        if self.frame is not None and not self.frame.isNull():
            pm = self.frame.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap((w - pm.width()) // 2, (h - pm.height()) // 2, pm)
        p.drawImage(0, 0, T.draw(spec, w, h))
        p.end()
        self.preview.setPixmap(base)

    def _apply(self) -> None:
        self.result_spec = T.normalize(self._read()) or {}
        self.accept()

    def _remove(self) -> None:
        self.result_spec = {}
        self.accept()
