# SPDX-License-Identifier: AGPL-3.0-or-later
"""The look: black background, greys, one white accent. Minimal on purpose.

Everything goes through one Qt style sheet so both windows (main and Edição
manual) match. Widgets opt into variants with ``setProperty``:

    role = "primary"   white button with black text (one per screen)
    role = "ghost"     borderless text button
    role = "muted"     secondary grey text (labels)
    role = "title"     the big header text
    role = "card"      a QFrame panel a shade lighter than the background
"""
from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPixmap, QPolygonF

BG = "#0a0a0a"          # window
PANEL = "#141414"       # cards, lists
TITLE_BG = "#1c1c1c"    # Windows title bar (Windows 11 colours it; 10 uses its own dark grey)
FIELD = "#1b1b1b"       # inputs
LINE = "#262626"        # borders
HOVER = "#2e2e2e"
TEXT = "#e8e8e8"
MUTED = "#8c8c8c"
FAINT = "#5a5a5a"
ACCENT = "#f2f2f2"      # the primary button

QSS = f"""
* {{ outline: 0; }}
QWidget {{ background: {BG}; color: {TEXT}; font-size: 10pt; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {FIELD}; color: {TEXT}; border: 1px solid {LINE}; padding: 6px; }}

QLabel {{ background: transparent; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="title"] {{ font-size: 18pt; font-weight: 600; letter-spacing: 0.5px; }}
QLabel[role="section"] {{ color: {MUTED}; font-size: 8.5pt; font-weight: 600;
                          letter-spacing: 1.5px; padding-top: 6px; }}

QFrame[role="card"] {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 10px; }}
QFrame[role="card"] QLabel, QFrame[role="card"] QCheckBox {{ background: transparent; }}

QGroupBox {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 10px;
            margin-top: 14px; padding: 14px 12px 10px 12px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {MUTED};
                   font-size: 8.5pt; font-weight: 600; letter-spacing: 1px; }}
QGroupBox QLabel, QGroupBox QCheckBox {{ background: transparent; }}
QGroupBox::indicator {{ width: 14px; height: 14px; }}

QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {FIELD}; border: 1px solid {LINE}; border-radius: 6px;
    padding: 6px 8px; selection-background-color: #3a3a3a; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: #4a4a4a; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {FAINT}; }}
QComboBox::drop-down {{ border: 0; width: 22px; }}
QComboBox::down-arrow {{ image: url(@ARROW@); width: 10px; height: 6px; margin-right: 10px; }}
QComboBox QAbstractItemView {{ background: {FIELD}; border: 1px solid {LINE};
    selection-background-color: {HOVER}; padding: 4px; }}

QPushButton {{ background: {FIELD}; border: 1px solid {LINE}; border-radius: 6px;
              padding: 7px 14px; }}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton:pressed {{ background: #383838; }}
QPushButton:disabled {{ color: {FAINT}; background: {PANEL}; }}
QPushButton:checked {{ background: #3a3a3a; border-color: #5a5a5a; }}
QPushButton[role="primary"] {{ background: {ACCENT}; color: #0a0a0a; border: 0;
    font-weight: 700; letter-spacing: 1px; }}
QPushButton[role="primary"]:hover {{ background: #ffffff; }}
QPushButton[role="primary"]:disabled {{ background: #3a3a3a; color: #7a7a7a; }}
QPushButton[role="ghost"] {{ background: transparent; border: 0; color: {MUTED}; padding: 4px 8px; }}
QPushButton[role="ghost"]:hover {{ color: {TEXT}; }}
QPushButton[role="icon"] {{ background: transparent; border: 1px solid {LINE}; border-radius: 16px;
    min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px; padding: 0; font-size: 11pt; }}
QPushButton[role="icon"]:hover {{ background: {HOVER}; }}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid #4a4a4a;
    border-radius: 4px; background: {FIELD}; }}
QCheckBox::indicator:checked, QGroupBox::indicator:checked {{ background: {FIELD};
    border: 1px solid {ACCENT}; image: url(@DOT@); }}
QGroupBox::indicator:unchecked {{ border: 1px solid #4a4a4a; border-radius: 4px; background: {FIELD}; }}
QGroupBox::indicator:checked {{ border-radius: 4px; }}
QCheckBox::indicator:hover {{ border-color: #8a8a8a; }}

QProgressBar {{ background: {FIELD}; border: 0; border-radius: 2px; height: 4px;
               max-height: 4px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}
QProgressBar[role="thick"] {{ height: 10px; max-height: 10px; border-radius: 5px; }}
QProgressBar[role="thick"]::chunk {{ border-radius: 5px; }}

QSlider::groove:horizontal {{ height: 4px; background: {LINE}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: #9a9a9a; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 12px; height: 12px;
    margin: -5px 0; border-radius: 6px; }}

QScrollBar:vertical, QScrollBar:horizontal {{ background: transparent; border: 0; margin: 0; }}
QScrollBar:vertical {{ width: 10px; }}
QScrollBar:horizontal {{ height: 10px; }}
QScrollBar::handle {{ background: #333333; border-radius: 4px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: #4a4a4a; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QListWidget, QListView {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 8px; }}
QListWidget::item {{ border-radius: 6px; padding: 4px; color: {TEXT}; }}
QListWidget::item:selected {{ background: #2f2f2f; border: 1px solid #6a6a6a; }}
QListWidget::item:hover {{ background: #222222; }}

QSplitter::handle {{ background: {BG}; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical {{ height: 6px; }}
QSplitter::handle:hover {{ background: {LINE}; }}

QMenu {{ background: {FIELD}; border: 1px solid {LINE}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {HOVER}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 6px; }}

QMessageBox QLabel {{ color: {TEXT}; }}
QGraphicsView {{ border: 1px solid {LINE}; border-radius: 8px; background: {BG}; }}
QStatusBar {{ color: {MUTED}; }}
"""


def apply(app) -> None:
    """Fusion + a dark palette (for anything the sheet misses) + the sheet."""
    if app.property("dh_theme"):            # once per application: restyling live windows can crash Qt
        return
    app.setProperty("dh_theme", True)
    app.setStyle("Fusion")
    pal = QPalette()
    for role, colour in ((QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, FIELD),
                         (QPalette.AlternateBase, PANEL), (QPalette.Text, TEXT),
                         (QPalette.Button, FIELD), (QPalette.ButtonText, TEXT),
                         (QPalette.ToolTipBase, FIELD), (QPalette.ToolTipText, TEXT),
                         (QPalette.Highlight, "#3a3a3a"), (QPalette.HighlightedText, "#ffffff"),
                         (QPalette.PlaceholderText, FAINT), (QPalette.Link, ACCENT)):
        pal.setColor(role, QColor(colour))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(FAINT))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(FAINT))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(FAINT))
    app.setPalette(pal)
    f = QFont("Segoe UI")
    f.setStyleHint(QFont.SansSerif)
    f.setPointSizeF(10)
    app.setFont(f)
    app.setStyleSheet(QSS.replace("@ARROW@", _arrow_png()).replace("@DOT@", _dot_png()))
    app._cv_no_wheel = _NoWheelOnChoices(app)
    app.installEventFilter(app._cv_no_wheel)
    try:                                        # Qt 6.8+: dark window frames everywhere
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except Exception:  # noqa: BLE001 — older Qt: the filter below does it
        pass


def _dot_png() -> str:
    """Checked boxes show a white dot: a fully white box read as empty."""
    path = os.path.join(tempfile.gettempdir(), "catavoo_dot.png")
    try:
        pm = QPixmap(32, 32)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ACCENT))
        p.drawEllipse(QPointF(16, 16), 8, 8)
        p.end()
        pm.save(path, "PNG")
    except Exception:  # noqa: BLE001 — cosmetic only
        pass
    return path.replace("\\", "/")


def _arrow_png() -> str:
    """A small grey chevron for combo boxes (style sheets need a file)."""
    path = os.path.join(tempfile.gettempdir(), "catavoo_arrow.png")
    try:
        pm = QPixmap(20, 12)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(MUTED))
        p.drawPolygon(QPolygonF([QPointF(2, 2), QPointF(18, 2), QPointF(10, 11)]))
        p.end()
        pm.save(path, "PNG")
    except Exception:  # noqa: BLE001 — cosmetic only
        pass
    return path.replace("\\", "/")


class _NoWheelOnChoices(QObject):
    """The mouse wheel over a closed list (or a number box) changed its value
    without a click — easy to do by accident while scrolling the window.
    Those values now change only by clicking; the wheel is ignored there.

    It also gives every window (main, prévia, dialogs) the dark Windows title
    bar the first time it is shown, instead of the white default."""

    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QWidget
        t = e.type()
        if t == QEvent.Wheel and isinstance(obj, (QComboBox, QAbstractSpinBox)):
            return True
        if t == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow() \
                and not obj.property("cv_dark_frame"):
            obj.setProperty("cv_dark_frame", True)
            dark_title_bar(obj)
        return False


def _colorref(hex_colour: str) -> int:
    c = QColor(hex_colour)
    return c.red() | (c.green() << 8) | (c.blue() << 16)


def dark_title_bar(widget) -> bool:
    """Windows 10/11: dark title bar (and, on 11, the app's own grey with white
    text), like the other dark apps. Nothing happens on other systems."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = wintypes.HWND(int(widget.winId()))
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        ok = False
        for attr in (20, 19):                    # DWMWA_USE_IMMERSIVE_DARK_MODE (new id, then Win10 1809–1909)
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                ok = True
                break
        for attr, colour in ((35, TITLE_BG), (36, TEXT), (34, TITLE_BG)):   # caption, text, border (Win11)
            v = ctypes.c_int(_colorref(colour))
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
        # Windows 10 repaints the frame only when it changes: nudge it
        ctypes.windll.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
        return ok
    except Exception:  # noqa: BLE001 — cosmetic only
        return False


def role(widget, name: str):
    """Tag a widget with a style variant (see the module docstring)."""
    widget.setProperty("role", name)
    return widget


# Landscape colours on the timeline: fixed, so the same landscape is always the
# same colour, and muted enough to read on black. Unknown categories get grey.
CATEGORY_COLOURS = {
    "waterfall": "#5b9bd5",     # azul
    "river": "#4fb3bf",         # ciano
    "lake": "#3f7fbf",          # azul escuro
    "coast": "#6cc3d9",         # azul claro
    "mountains": "#8f9f6b",     # verde oliva
    "forest": "#5fa36a",        # verde
    "countryside": "#b5b05a",   # amarelo palha
    "canyon": "#c0805a",        # terracota claro
    "historic": "#c8925e",      # terracota
    "sunset": "#e39a4c",        # laranja
    "city": "#9a8fc7",          # lilás
    "road": "#9a9a9a",          # cinza
}
UNKNOWN_COLOUR = "#7d7d7d"


def category_colour(category: str) -> str:
    return CATEGORY_COLOURS.get(category or "", UNKNOWN_COLOUR)
