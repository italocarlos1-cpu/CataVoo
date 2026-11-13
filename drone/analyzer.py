# SPDX-License-Identifier: AGPL-3.0-or-later
"""One pass over a clip → a per-sample timeline, cached forever.

The pass is CPU/decoder bound and holds no ML model, so it can run before any
CLIP model is loaded (keeping VRAM free for NVDEC). Thumbnails are written to
the cache so the CLIP pass later never needs to decode the clip again.
"""
from __future__ import annotations

import time

import cv2
import numpy as np

from drone import frame_metrics, motion_analysis
from drone.analysis_cache import AnalysisCache
from drone.config import DroneConfig
from drone.ingest import DroneClip
from drone.media_io import iter_frame_pairs

SCALAR_FIELDS = ("sharp", "clip_lo", "clip_hi", "mean_l", "contrast", "colorful",
                 "green", "water", "warm", "sky", "white", "edges", "sal_x", "sal_focus")
MOTION_FIELDS = ("vx", "vy", "zoom", "rot", "parallax", "residual", "inliers", "valid")


def _round(a, nd=4):
    return [round(float(x), nd) for x in a]


def scene_cuts(colour_sigs: np.ndarray, struct_sigs: np.ndarray, sample_hz: float) -> list[int]:
    """Sample indices where a new shot starts inside one file.

    Drone files are normally one continuous take; this catches the exceptions
    (a recording that spans a hard whip, a merged file). Reuses the engine's
    robust median/MAD threshold rather than a fixed number.
    """
    n = len(colour_sigs)
    if n < 3:
        return []
    c = np.einsum("ij,ij->i", colour_sigs[1:], colour_sigs[:-1])
    s = np.einsum("ij,ij->i", struct_sigs[1:], struct_sigs[:-1])
    diff = 1.0 - np.minimum(c, (s + 1.0) / 2.0)
    try:
        from modules.segments.scene_cuts import adaptive_threshold
        thr = adaptive_threshold(diff, z=6.0)
    except Exception:
        med = float(np.median(diff))
        thr = med + 6.0 * 1.4826 * float(np.median(np.abs(diff - med)))
    thr = max(thr, 0.35)
    cand = np.flatnonzero(diff > thr) + 1
    cuts, last = [], -10 ** 9
    min_gap = int(sample_hz * 1.5)
    for i in cand:
        if i - last >= min_gap:
            cuts.append(int(i))
            last = i
    return cuts


def analyze_clip(clip: DroneClip, cfg: DroneConfig, cache: AnalysisCache, *,
                 log_fn=print, progress_fn=None, cancel_check=None) -> dict:
    params = cfg.analysis.key()
    cached = cache.get(clip.path, params)
    if cached is not None:
        return cached

    info = clip.info
    fps = info.fps if info.fps and info.fps > 1 else 30.0
    sample_hz = cfg.analysis.sample_hz
    scal = {k: [] for k in SCALAR_FIELDS}
    mot = {k: [] for k in MOTION_FIELDS}
    times, structs, colours, thumbs = [], [], [], []
    next_thumb = 0.0
    t0 = time.time()
    expected = max(1, int(info.duration * sample_hz))

    for t, f0, f1 in iter_frame_pairs(info, sample_hz=sample_hz, width=cfg.analysis.width,
                                       hwaccel=cfg.hwaccel, cancel_check=cancel_check):
        m, g0, st, co = frame_metrics.measure(f0)
        g1 = cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY)
        mv = motion_analysis.pair_motion(g0, g1, fps)
        times.append(t)
        for k in SCALAR_FIELDS:
            scal[k].append(m[k])
        for k in MOTION_FIELDS:
            mot[k].append(mv[k])
        structs.append(st)
        colours.append(co)
        if t >= next_thumb:
            th = cv2.resize(f0, (224, max(2, int(224 * f0.shape[0] / f0.shape[1]))),
                            interpolation=cv2.INTER_AREA)
            ok, jpg = cv2.imencode(".jpg", th, [cv2.IMWRITE_JPEG_QUALITY, 88])
            if ok:
                thumbs.append((round(t, 3), jpg.tobytes()))
            next_thumb = t + cfg.analysis.thumb_every
        if progress_fn and len(times) % 20 == 0:
            progress_fn(min(len(times), expected), expected, clip.name)

    if cancel_check is not None and cancel_check():
        raise RuntimeError("cancelado")
    if len(times) < 4:
        raise RuntimeError(f"não foi possível decodificar {clip.name}")

    sm = motion_analysis.smoothness_series(mot["vx"], mot["vy"], mot["zoom"], mot["rot"],
                                           mot["valid"], sample_hz=sample_hz)
    structs_a, colours_a = np.array(structs), np.array(colours)
    timeline = {
        "t": _round(times, 3),
        **{k: _round(v) for k, v in scal.items()},
        **{k: _round(v, 5) for k, v in mot.items()},
        **{k: _round(v, 5) for k, v in sm.items()},
        "struct": np.round(structs_a, 3).tolist(),
        "colour": np.round(colours_a, 3).tolist(),
        "cuts": scene_cuts(colours_a, structs_a, sample_hz),
    }
    meta = {
        "path": clip.path, "name": clip.name, "duration": info.duration,
        "width": info.width, "height": info.height, "fps": fps,
        "codec": info.codec, "bit_depth": info.bit_depth, "is_dji": clip.is_dji,
        "lat": clip.lat, "lon": clip.lon, "creation_time": info.creation_time,
        "sample_hz": sample_hz, "seconds": round(time.time() - t0, 2),
    }
    cache.put(clip.path, params, meta, timeline)
    key = cache.content_key(clip.path)
    cache.put_thumbs(key, thumbs)
    log_fn(f"🔎 {clip.name}: {len(times)} amostras em {meta['seconds']:.1f}s "
           f"({info.duration / max(0.01, meta['seconds']):.1f}x tempo real)")
    meta["timeline"] = timeline
    meta["content_hash"] = key
    return meta
