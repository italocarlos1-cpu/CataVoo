"""Text on a shot: one caption per shot, shown for the whole time the shot is
on screen.

The same drawing is used by the preview (scaled to the monitor) and by the
render (a transparent PNG at the output size, laid over the finished video by
ffmpeg), so what the editor shows is what the file gets. Sizes and margins are
fractions of the frame, never pixels, for that reason.
"""
from __future__ import annotations

import os

FONTS = ["Arial", "Verdana", "Trebuchet MS", "Georgia", "Impact", "Courier New"]
SIZES = {"pequeno": 0.045, "medio": 0.065, "grande": 0.090}      # of the frame's short side
SIZE_LABELS = [("Pequeno", "pequeno"), ("Médio", "medio"), ("Grande", "grande")]
POSITIONS = [("Embaixo", "baixo"), ("No meio", "meio"), ("No alto", "alto")]
ALIGNS = [("Esquerda", "left"), ("Centro", "center")]

DEFAULT = {"text": "", "align": "left", "font": "Arial", "size": "medio", "pos": "baixo",
           "bold": True, "upper": True, "color": "#ffffff", "fill": True, "bg_color": "#0b1f4d"}


def normalize(spec) -> dict | None:
    """A complete caption dict, or None when there is no text."""
    if not spec or not str(spec.get("text", "")).strip():
        return None
    out = dict(DEFAULT)
    out.update({k: v for k, v in spec.items() if k in DEFAULT})
    if out["size"] not in SIZES:
        out["size"] = "medio"
    return out


def _lines(text: str, fm, max_w: float) -> list:
    """The text's own line breaks, then word wrap to the frame's width."""
    out = []
    for para in text.splitlines() or [""]:
        words, cur = para.split(), ""
        if not words:
            out.append("")
            continue
        for w in words:
            cand = f"{cur} {w}".strip()
            if cur and fm.horizontalAdvance(cand) > max_w:
                out.append(cur)
                cur = w
            else:
                cur = cand
        out.append(cur)
    while out and not out[-1]:
        out.pop()
    return out


def draw(spec, width: int, height: int):
    """Transparent ``QImage`` of the frame size with the caption drawn on it."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen

    width, height = max(2, int(width)), max(2, int(height))
    img = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    spec = normalize(spec)
    if spec is None:
        return img
    short = min(width, height)
    px = max(8.0, short * SIZES[spec["size"]])
    font = QFont(spec["font"])
    font.setPixelSize(int(round(px)))
    font.setBold(bool(spec["bold"]))
    fm = QFontMetricsF(font)
    text = spec["text"].upper() if spec["upper"] else spec["text"]
    margin = width * 0.06
    pad_x, pad_y, gap = px * 0.35, px * 0.18, px * 0.18
    lines = _lines(text, fm, width - 2 * margin - 2 * pad_x)
    if not lines:
        return img
    line_h = fm.height() + 2 * pad_y
    block_h = len(lines) * line_h + (len(lines) - 1) * gap
    if spec["pos"] == "alto":
        y = height * 0.08
    elif spec["pos"] == "meio":
        y = (height - block_h) / 2
    else:
        y = height * 0.92 - block_h
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setFont(font)
    color = QColor(spec["color"])
    bg = QColor(spec["bg_color"])
    for line in lines:
        tw = fm.horizontalAdvance(line)
        box_w = tw + 2 * pad_x
        x = margin if spec["align"] == "left" else (width - box_w) / 2
        if spec["fill"] and line:
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(x, y, box_w, line_h), px * 0.08, px * 0.08)
        base = y + pad_y + fm.ascent()
        if line and not spec["fill"]:
            # no box: a dark outline keeps the letters readable on bright sky
            path = QPainterPath()
            path.addText(x + pad_x, base, font, line)
            p.setPen(QPen(QColor(0, 0, 0, 200), max(1.5, px * 0.09), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        p.setPen(color)
        p.drawText(QRectF(x + pad_x, y + pad_y, tw + 2, fm.height()), Qt.AlignLeft | Qt.AlignTop, line)
        y += line_h + gap
    p.end()
    return img


def write_pngs(plan, size, folder: str) -> list:
    """PNG per shot with text, at the output size. Returns ``[(png, t0, t1)]``
    with each shot's on-screen time in the film."""
    os.makedirs(folder, exist_ok=True)
    out = []
    times = plan.cut_times()
    for i, s in enumerate(plan.shots):
        spec = normalize(getattr(s, "caption", None))
        if spec is None:
            continue
        png = os.path.join(folder, f"texto_{i + 1:02d}.png")
        draw(spec, size[0], size[1]).save(png)
        t0 = times[i]
        out.append((png, round(t0, 3), round(t0 + plan.advance(i), 3)))
    return out


def burn(video: str, overlays: list, *, mode: str = "gpu", log_fn=print) -> str:
    """Lay the caption PNGs over ``video`` (in place), each only during its shot."""
    if not overlays:
        return video
    from drone.media_io import _run, ffmpeg_bin
    from modules.system.encoder_select import encoder_chain
    tmp = os.path.splitext(video)[0] + "_texto.mp4"
    inputs, chain, last = [], [], "0:v"
    for k, (png, t0, t1) in enumerate(overlays, start=1):
        inputs += ["-i", png]
        tag = f"v{k}"
        chain.append(f"[{last}][{k}:v]overlay=0:0:enable='between(t,{t0:.3f},{t1:.3f})'[{tag}]")
        last = tag
    err = ""
    for _enc, vargs in encoder_chain(video, mode=mode):
        cmd = [ffmpeg_bin(), "-y", "-v", "error", "-i", video] + inputs + [
            "-filter_complex", ";".join(chain), "-map", f"[{last}]", "-map", "0:a?",
            "-c:a", "copy"] + vargs + ["-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp]
        r = _run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(tmp):
            os.replace(tmp, video)
            log_fn(f"🔤 Textos aplicados em {len(overlays)} tomada(s)")
            return video
        err = (r.stderr or "")[-400:]
    raise RuntimeError(f"falha ao aplicar os textos: {err}")
