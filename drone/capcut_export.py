# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hand the edit to CapCut for finishing.

CapCut Desktop cannot open another editor's project or timeline (EDL/XML), so
the hand-off is the practical one: a folder with every shot already cut to
the frame, numbered in film order, plus the music (with its fade-out) and a
short ``ORDEM.txt``. In CapCut: Importar → select all files → drag them to the
timeline (they land in the right order because of the numbers) → drop the
music underneath. Transitions, colour and text are then done in CapCut.

Each file is exactly the shot's *time on screen* in the film (from its cut to
the next cut). Laid end to end they add up to the film's length, so every cut
falls on the same beat of the music as in the program; the dissolves become
straight cuts (``ORDEM.txt`` says where to put them back). The program's
dissolve overlaps two shots, which a back-to-back import can't reproduce.

The cuts are re-encoded from the originals at their full resolution (GPU
encoder when there is one), never from the preview proxies.
"""
from __future__ import annotations

import glob
import os
import time

from drone import music_selector
from drone.edl_adapter import cut_cropped
from drone.media_io import _run, ffmpeg_bin


def cut_full(src: str, start: float, end: float, dst: str, mode: str = "gpu") -> str:
    """One shot at the source's own resolution (16:9)."""
    from modules.system.encoder_select import encoder_chain
    last = ""
    for _enc, vargs in encoder_chain(src, mode=mode):
        cmd = [ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", src,
               "-t", f"{end - start:.3f}", "-map", "0:v:0", "-map", "0:a:0?",
               "-vf", "format=yuv420p"] + vargs + [
               "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", dst]
        r = _run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(dst):
            return dst
        last = (r.stderr or "")[-400:]
    raise RuntimeError(f"falha ao cortar {os.path.basename(src)}: {last}")


def export(plan, *, out_dir: str, music=None, src_sizes: dict | None = None, title: str = "",
           mode: str = "gpu", log_fn=print, progress_fn=None, cancel_check=None) -> str:
    """Write the CapCut folder for an EditPlan. Returns the folder."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    folder = os.path.join(out_dir, "capcut", f"{plan.style_key}_{plan.tag}_{stamp}")
    os.makedirs(folder, exist_ok=True)
    vertical = plan.tag == "9x16"
    n = len(plan.shots)
    lines = [f"DroneHighlighter → CapCut  ·  {title or ''}  ·  estilo {plan.style_label or plan.style_key}"
             f"  ·  {'vertical 9:16' if vertical else 'horizontal 16:9'}",
             f"Duração total: {plan.total:.2f} s  ·  {n} tomadas", "",
             "No CapCut: Importar → selecione todos os arquivos → arraste para a linha do tempo",
             "(entram na ordem pelo número) → arraste 00_musica.m4a para baixo, na faixa de áudio,",
             "começando no zero. Cada arquivo tem exatamente o tempo que a tomada fica na tela, então",
             "os cortes caem nas mesmas batidas da música. Onde houver \"fusão\", coloque uma transição",
             "no CapCut (ela já vem como corte seco).", ""]
    cuts = plan.cut_times()
    for i, s in enumerate(plan.shots):
        if cancel_check is not None and cancel_check():
            raise RuntimeError("cancelado")
        if progress_fn:
            progress_fn(i, n + 1)
        base = os.path.splitext(os.path.basename(s.path))[0]
        on_screen = plan.advance(i) if i < n - 1 else s.length
        end = s.start + on_screen
        dst = os.path.join(folder, f"{i + 1:02d}_{base}_{s.start:.1f}s.mp4")
        log_fn(f"✂ CapCut {i + 1}/{n}: {base} {s.start:.2f}–{end:.2f} s")
        if vertical and src_sizes and s.path in src_sizes:
            w, h = src_sizes[s.path]
            out_h = min(1920, h)
            out_w = int(out_h * 9 / 16) // 2 * 2
            cut_cropped(s.path, s.start, end, dst, src_size=(w, h), sal_x=s.crop_x,
                        out_size=(out_w, out_h), mode=mode)
        else:
            cut_full(s.path, s.start, end, dst, mode=mode)
        trans = (f"fusão de {plan.trans_out(i):.1f} s para a próxima" if plan.trans_out(i)
                 else "corte seco")
        lines.append(f"{i + 1:02d}  entra em {cuts[i]:7.2f} s  ·  {on_screen:5.2f} s na tela  ·  "
                     f"{os.path.basename(s.path)} {s.start:.2f}–{end:.2f} s  ·  {trans}")
    if music is not None:
        try:
            music_selector.prepare(music, plan.total, os.path.join(folder, "00_musica.m4a"))
            lines += ["", f"Música: {os.path.basename(music.path)} a partir de {music.start:.2f} s "
                          f"(já cortada no tamanho do vídeo, com fade no final) → 00_musica.m4a"]
        except Exception as exc:  # noqa: BLE001 — the clips are still useful
            log_fn(f"⚠️ Música não exportada: {exc}")
    with open(os.path.join(folder, "ORDEM.txt"), "w", encoding="utf-8-sig") as fh:
        fh.write("\n".join(lines) + "\n")
    if progress_fn:
        progress_fn(n + 1, n + 1)
    log_fn(f"✅ Pasta para o CapCut pronta: {folder}")
    return folder


def find_capcut() -> str:
    """CapCut Desktop's .exe on this PC, or ''."""
    if os.name != "nt":
        return ""
    roots = [os.environ.get("LOCALAPPDATA", ""), os.environ.get("PROGRAMFILES", ""),
             os.environ.get("PROGRAMFILES(X86)", "")]
    for root in filter(None, roots):
        for pattern in (r"CapCut\Apps\CapCut.exe", r"CapCut\CapCut.exe", r"CapCut\Apps\*\CapCut.exe"):
            hits = sorted(glob.glob(os.path.join(root, pattern)))
            if hits:
                return hits[-1]
    return ""
