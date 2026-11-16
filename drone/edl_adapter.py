# SPDX-License-Identifier: AGPL-3.0-or-later
"""Render a plan with VideoHighlighter's engine.

* **16:9** goes straight through :func:`modules.media.edl.render_edl` — the
  same renderer the app's own EDL editor uses, so the saved ``.edl.yaml`` file
  can be opened, tweaked and re-rendered there.
* **9:16** needs a crop that follows the subject, which the engine's
  centre-crop ``fill`` cannot do. Each piece is cut *and* cropped here around
  the saliency centre measured during analysis (SkyGrid's "smart reframe"
  idea, reimplemented without YOLO), then joined by the engine's
  :func:`modules.media.transitions.build_reel` for transitions, title and music.
"""
from __future__ import annotations

import os
import shutil
import tempfile

from drone.cinematic_director import Plan, to_edl
from drone.media_io import _run, ffmpeg_bin  # _run hides console windows on Windows


PROJECT_DIR = "projeto"


def side_path(out_path: str, suffix: str) -> str:
    """``<saída>/projeto/<nome do vídeo><suffix>``: the working files (EDL, music,
    report) live one folder down, so ``_CataVoo`` shows only the videos."""
    folder = os.path.join(os.path.dirname(out_path), PROJECT_DIR)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, os.path.splitext(os.path.basename(out_path))[0] + suffix)



def render_horizontal(plan: Plan, out_path: str, *, title: str, size, fps: int, crf: int,
                      music_path: str, mode: str = "gpu", log_fn=print, progress_fn=None,
                      cancel_check=None) -> tuple[str, str]:
    from modules.media.edl import render_edl, save_edl
    edl = to_edl(plan, title=title, width=size[0], height=size[1], fps=fps, crf=crf,
                 music_path=music_path, fill="pad")
    edl_path = side_path(out_path, ".edl.yaml")
    save_edl(edl, edl_path)
    render_edl(edl, out_path, mode=mode, music_optional=True, log_fn=log_fn,
               progress_fn=progress_fn, cancel_check=cancel_check)
    return out_path, edl_path


def _crop_box(src_w: int, src_h: int, sal_x: float, out_w: int, out_h: int) -> tuple[int, int, int, int]:
    aspect = out_w / out_h
    if src_w / src_h > aspect:              # wider than target: crop the sides
        cw, ch = int(src_h * aspect) // 2 * 2, src_h // 2 * 2
        x = int(round(sal_x * src_w - cw / 2))
        x = max(0, min(src_w - cw, x)) // 2 * 2
        return cw, ch, x, 0
    cw, ch = src_w // 2 * 2, int(src_w / aspect) // 2 * 2
    return cw, ch, 0, max(0, (src_h - ch) // 2) // 2 * 2


def cut_cropped(src: str, start: float, end: float, dst: str, *, src_size, sal_x: float,
                out_size, mode: str = "gpu") -> str:
    from modules.system.encoder_select import encoder_chain
    cw, ch, x, y = _crop_box(src_size[0], src_size[1], sal_x, *out_size)
    vf = f"crop={cw}:{ch}:{x}:{y},scale={out_size[0]}:{out_size[1]}:flags=lanczos,setsar=1,format=yuv420p"
    last = ""
    for enc, vargs in encoder_chain(src, mode=mode):
        cmd = [ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", src,
               "-t", f"{end - start:.3f}", "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf] + vargs + [
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", dst]
        r = _run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(dst):
            return dst
        last = (r.stderr or "")[-400:]
    raise RuntimeError(f"falha ao cortar {os.path.basename(src)}: {last}")


def render_vertical(plan: Plan, out_path: str, *, title: str, size, fps: int, crf: int,
                    music_path: str, src_sizes: dict, mode: str = "gpu", log_fn=print,
                    progress_fn=None, cancel_check=None) -> tuple[str, str]:
    from modules.media.edl import save_edl
    from modules.media.transitions import ReelCancelled, Transition, build_reel
    edl = to_edl(plan, title=title, width=size[0], height=size[1], fps=fps, crf=crf,
                 music_path=music_path, fill="crop")
    edl_path = side_path(out_path, ".edl.yaml")
    save_edl(edl, edl_path)
    tmp = tempfile.mkdtemp(prefix="dh_vert_")
    try:
        pieces = []
        for i, shot in enumerate(plan.shots):
            if cancel_check is not None and cancel_check():
                raise ReelCancelled("cancelado")
            if progress_fn:
                progress_fn(i, len(plan.shots) + 1, "Vertical", f"recorte {i + 1}/{len(plan.shots)}")
            dst = os.path.join(tmp, f"v{i:03d}.mp4")
            log_fn(f"📱 Recorte 9:16 {i + 1}/{len(plan.shots)} (centro {shot.crop_x:.2f})")
            cut_cropped(shot.candidate.path, shot.start, shot.end, dst,
                        src_size=src_sizes[shot.candidate.path], sal_x=shot.crop_x,
                        out_size=size, mode=mode)
            pieces.append(dst)
        transitions = [Transition(index=i, kind=c.transition, duration=c.transition_duration,
                                  easing=c.easing, feather=0.0)
                       for i, c in enumerate(edl.cuts[:-1])]
        texts = {i: c.text for i, c in enumerate(edl.cuts) if c.text}
        music = {"path": music_path, "mode": "replace", "volume": 1.0} if music_path else None
        build_reel(pieces, out_path, transitions=transitions, width=size[0], height=size[1],
                   fps=fps, crf=crf, music=music, music_optional=True, texts=texts,
                   fill="crop", log_fn=log_fn, progress_fn=progress_fn, cancel_check=cancel_check)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out_path, edl_path
