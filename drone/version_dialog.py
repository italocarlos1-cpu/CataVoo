"""Nova versão: a small window to ask for another cut of the film.

Write what to change (with ready-made suggestions taken from this material),
optionally pick a Foco, and see right away what the program understood —
then one button, Gerar nova versão. Every new version also varies the shots,
like changing the style does; the current edit stays in Desfazer (Ctrl+Z).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from drone.feedback import parse_feedback
from drone.landscape import LABEL_PT

GENERAL = ["cortes mais rápidos", "cortes mais lentos", "vídeo mais curto", "vídeo mais longo",
           "movimentos suaves", "sem transições"]
CHIP = ("QPushButton{border:1px solid #3a3a3a;border-radius:13px;padding:4px 12px;background:#161616;}"
        "QPushButton:hover{border-color:#9a9a9a;background:#202020;}")
EXAMPLE = "Ex.: mais cachoeira e pôr do sol, menos cidade, cortes mais rápidos, terminar com o pôr do sol"


def understood(text: str) -> str:
    """What the keyword reading takes from ``text``, in plain Portuguese."""
    a = parse_feedback(text)
    out = []
    lab = lambda k: LABEL_PT.get(k, k)          # noqa: E731
    if a.prefer:
        out.append("mais " + ", ".join(lab(k) for k in a.prefer))
    if a.avoid_categories:
        out.append("menos " + ", ".join(lab(k) for k in a.avoid_categories))
    if a.shot_scale < 1:
        out.append("cortes mais rápidos")
    elif a.shot_scale > 1:
        out.append("cortes mais lentos")
    if a.duration_scale < 1:
        out.append("vídeo mais curto")
    elif a.duration_scale > 1:
        out.append("vídeo mais longo")
    if a.opener:
        out.append(f"começar com {lab(a.opener)}")
    if a.closer:
        out.append(f"terminar com {lab(a.closer)}")
    if a.transitions == "cut":
        out.append("só cortes secos")
    elif a.transitions == "crossfade":
        out.append("mais fusões")
    if a.smooth:
        out.append("movimentos mais suaves")
    return " · ".join(out)


class _Flow(QWidget):
    """Suggestion buttons that wrap onto the next line."""

    def __init__(self, labels, on_click, per_row: int = 4):
        super().__init__()
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        rows = []
        for k, label in enumerate(labels):
            if k % per_row == 0:
                rows.append(QHBoxLayout())
                rows[-1].setSpacing(6)
                box.addLayout(rows[-1])
            b = QPushButton("+ " + label)
            b.setStyleSheet(CHIP)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip("Acrescenta ao pedido")
            b.clicked.connect(lambda _c=False, t=label: on_click(t))
            rows[-1].addWidget(b)
        for r in rows:
            r.addStretch()


class VersionDialog(QDialog):
    """``exec()`` → Accepted with ``request`` (text) and ``focus``."""

    def __init__(self, parent, *, request: str, focus: str, focus_options: list,
                 landscapes: list, edited: bool, ai_on: bool):
        super().__init__(parent)
        self.setWindowTitle("Nova versão")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        head = QLabel("<b>O que você quer mudar?</b> Escreva do seu jeito ou clique nas sugestões. "
                      "Pode deixar em branco para só variar as tomadas.")
        head.setWordWrap(True)
        lay.addWidget(head)
        self.edit = QPlainTextEdit(request)
        self.edit.setPlaceholderText(EXAMPLE)
        self.edit.setFixedHeight(84)
        self.edit.textChanged.connect(self._read_back)
        lay.addWidget(self.edit)
        found = [f"mais {lab.lower()}" for lab in landscapes[:4]]
        if found:
            lay.addWidget(self._muted("Paisagens deste material"))
            lay.addWidget(_Flow(found, self._add))
        lay.addWidget(self._muted("Ritmo e duração"))
        lay.addWidget(_Flow(GENERAL, self._add, per_row=3))

        frow = QHBoxLayout()
        frow.addWidget(self._muted("Foco"))
        self.focus = QComboBox()
        self.focus.setEditable(True)
        self.focus.setInsertPolicy(QComboBox.NoInsert)
        self.focus.addItem("Sem foco")
        for label, n in focus_options:
            self.focus.addItem(f"{label} ({n} s)")
        self.focus.setEditText(focus or "Sem foco")
        self.focus.lineEdit().setPlaceholderText("lago, ponte, igreja…")
        self.focus.setToolTip("O assunto que deve aparecer mais. Escolha uma paisagem da lista ou digite qualquer\n"
                              "coisa (ponte, igreja, barco…): a IA de paisagens procura nas miniaturas já guardadas.")
        frow.addWidget(self.focus, 1)
        lay.addLayout(frow)

        self.got = QLabel()
        self.got.setWordWrap(True)
        self.got.setTextFormat(Qt.RichText)
        lay.addWidget(self.got)
        note = ("A edição manual atual é substituída — ela continua no <b>Desfazer</b> (Ctrl+Z)."
                if edited else "Cada clique gera uma versão diferente; o <b>Desfazer</b> (Ctrl+Z) volta à anterior.")
        if not ai_on:
            note += "<br>Pedidos mais livres são entendidos com o Assistente de IA ligado (tela principal)."
        n = QLabel(note)
        n.setWordWrap(True)
        n.setProperty("role", "muted")
        lay.addWidget(n)

        btns = QHBoxLayout()
        clear = QPushButton("Limpar")
        clear.setProperty("role", "ghost")
        clear.clicked.connect(lambda: (self.edit.clear(), self.focus.setEditText("Sem foco")))
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        go = QPushButton("Gerar nova versão")
        go.setProperty("role", "primary")
        go.setDefault(True)
        go.clicked.connect(self.accept)
        btns.addWidget(clear)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(go)
        lay.addLayout(btns)
        self._read_back()

    @staticmethod
    def _muted(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setProperty("role", "muted")
        return lbl

    def _add(self, phrase: str) -> None:
        cur = self.edit.toPlainText().strip().rstrip(",")
        self.edit.setPlainText(f"{cur}, {phrase}" if cur else phrase)

    def _read_back(self) -> None:
        text = self.edit.toPlainText().strip()
        got = understood(text)
        if got:
            self.got.setText(f"<b>Entendi:</b> {got}")
        elif text:
            self.got.setText("<b>Entendi:</b> nenhum pedido reconhecido — a nova versão vai só variar as tomadas. "
                             "Use as sugestões acima como modelo.")
        else:
            self.got.setText("")

    @property
    def request(self) -> str:
        return self.edit.toPlainText().strip()

    @property
    def focus_text(self) -> str:
        t = self.focus.currentText().strip()
        return "" if t.lower() == "sem foco" else t
