# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turn a slice of a clip's timeline into the five scoring criteria.

Criteria (0..1 each) and the spec's initial weights:

==================  ======  ===================================================
quality             30 %    sharpness (absolute and vs. the clip's own best),
                            exposure, contrast, colour, a clear subject
smoothness          25 %    jitter vs. trend + jerk (NOT speed — see motion)
interest            20 %    landscape category value × (1 − P(boring)),
                            plus the user's priorities
movement            15 %    move type, steadiness, comfortable pace
variety             10 %    applied at selection time (depends on the others)
==================  ======  ===================================================

Windows are judged by their *weak* moments too (20th/80th percentiles), since
one second of blur or a jolt inside a shot is what the viewer remembers.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from drone import motion_analysis
from drone.config import DroneConfig, ScoreWeights
from drone.landscape import CAT_KEYS


@dataclass
class Candidate:
    clip: int                    # index into the analyses list
    path: str
    start: float
    end: float
    i0: int
    i1: int
    parts: dict = field(default_factory=dict)
    base: float = 0.0            # weighted score without the variety term
    movement: str = ""
    consistency: float = 0.0
    category: str = ""
    cat_vec: np.ndarray = None
    struct: np.ndarray = None
    colour: np.ndarray = None
    sal_x: float = 0.5
    sal_focus: float = 0.0
    order: int = 0               # shooting order of the clip
    scene_lo: float = 0.0        # bounds this window may grow within
    scene_hi: float = 0.0
    uid: str = ""                # stable id within one run ("S12"), used by the AI director

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def center(self) -> float:
        return 0.5 * (self.start + self.end)

    def describe(self) -> str:
        from drone.landscape import LABEL_PT
        return (f"{self.path.split('/')[-1].split(chr(92))[-1]} {self.start:6.1f}-{self.end:6.1f}s "
                f"{LABEL_PT.get(self.category, self.category):16s} {self.movement:11s} "
                f"score={self.base:.3f} " +
                " ".join(f"{k[:4]}={v:.2f}" for k, v in self.parts.items()))


def clip_reference(tl: dict) -> dict:
    sharp = np.asarray(tl["sharp"])
    return {"sharp_p90": float(np.quantile(sharp, 0.9)) if len(sharp) else 1.0}


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def score_window(tl: dict, ref: dict, i0: int, i1: int, cfg: DroneConfig,
                 weights: ScoreWeights, priorities=()) -> dict | None:
    """Criteria for samples [i0, i1). None when the window is unusable."""
    sl = slice(i0, i1)
    mt = cfg.motion
    sharp = np.asarray(tl["sharp"][sl])
    p20 = float(np.quantile(sharp, 0.2))
    s_abs = min(1.0, max(0.0, (math.log10(p20 + 1.0) - 1.2) / 1.2))   # var ~16 → 0, ~250 → 1
    s_rel = min(1.0, p20 / max(1e-6, ref["sharp_p90"]))
    # Absolute sharpness decides; the relative term only punishes dips inside
    # the clip. (Averaging them let a clip blurred end to end score itself as
    # "as sharp as its own best moment".)
    sharp_score = s_abs * (0.6 + 0.4 * s_rel)

    clipping = float(np.mean(np.asarray(tl["clip_lo"][sl]) + np.asarray(tl["clip_hi"][sl])))
    mean_l = float(np.mean(tl["mean_l"][sl]))
    exposure = (1.0 - min(1.0, clipping * 4.0)) * (1.0 - min(1.0, max(0.0, abs(mean_l - 0.45) - 0.25) * 3))
    contrast = min(1.0, float(np.mean(tl["contrast"][sl])) / 0.20)
    colorful = min(1.0, float(np.mean(tl["colorful"][sl])) / 55.0)
    focus = float(np.mean(tl["sal_focus"][sl]))
    quality = 0.40 * sharp_score + 0.20 * exposure + 0.15 * contrast + 0.10 * colorful + 0.15 * focus

    jitter = np.asarray(tl["jitter"][sl])
    jerk = np.asarray(tl["jerk"][sl])
    j80 = float(np.quantile(jitter, 0.8))
    if motion_analysis.is_unusable(j80, mt):
        return None
    if mean_l < 0.06 or p20 < 3.0:     # black frames / total blur
        return None
    smooth = motion_analysis.smoothness_score(jitter, jerk, cfg=mt)

    kind, cons = motion_analysis.classify_movement(
        tl["tvx"][sl], tl["tvy"][sl], tl["tzoom"][sl], tl["parallax"][sl],
        tl["speed"][sl], cfg=mt)
    speed = float(np.mean(tl["speed"][sl]))
    movement = motion_analysis.movement_score(kind, cons, speed, cfg=mt)

    cats = np.asarray(tl["cat_probs"][i0:i1]).mean(axis=0)
    interest = float(np.mean(tl["interest"][sl]))
    if priorities:
        idx = [CAT_KEYS.index(p) for p in priorities if p in CAT_KEYS]
        if idx:
            interest = min(1.0, interest + 0.6 * float(cats[idx].sum()))

    parts = {"quality": quality, "smoothness": smooth, "interest": interest, "movement": movement}
    base = (weights.quality * quality + weights.smoothness * smooth
            + weights.interest * interest + weights.movement * movement)
    base /= max(1e-9, 1.0 - weights.variety)
    return {"parts": parts, "base": base, "movement": kind, "consistency": cons,
            "cats": cats, "category": CAT_KEYS[int(np.argmax(cats))]}


def scene_bounds(tl: dict) -> list[tuple[int, int]]:
    n = len(tl["t"])
    edges = [0] + [c for c in tl.get("cuts", []) if 0 < c < n] + [n]
    return [(a, b) for a, b in zip(edges, edges[1:]) if b > a]


def candidates_for_clip(clip_idx: int, analysis: dict, length: float, cfg: DroneConfig,
                        weights: ScoreWeights, *, priorities=(), hop: float = 0.5,
                        head_skip: float = 0.6, tail_skip: float = 0.3,
                        per_clip: int = 6, min_length: float = 1.2) -> list[Candidate]:
    """Best non-overlapping windows of ``length`` seconds in one clip.

    Long continuous takes (the SkyGrid shot-search idea) are thus split into
    several candidates, each scored on its own; short clips that cannot hold
    ``length`` contribute a single shorter window.
    """
    tl = analysis["timeline"]
    hz = float(analysis.get("sample_hz", cfg.analysis.sample_hz))
    t = np.asarray(tl["t"])
    duration = float(analysis["duration"])
    ref = clip_reference(tl)
    struct_all = np.asarray(tl["struct"])
    colour_all = np.asarray(tl["colour"])
    out: list[Candidate] = []
    for a, b in scene_bounds(tl):
        lo = float(t[a]) + (head_skip if a == 0 else 0.25)
        hi = (float(t[b]) if b < len(t) else duration) - (tail_skip if b == len(t) else 0.25)
        span = hi - lo
        if span < min_length:
            continue
        win = min(length, span)
        starts = np.arange(lo, hi - win + 1e-6, hop) if span > win else np.array([lo])
        for s in starts:
            e = s + win
            i0 = int(np.searchsorted(t, s))
            i1 = max(i0 + 2, int(np.searchsorted(t, e)))
            if i1 > len(t):
                continue
            sc = score_window(tl, ref, i0, i1, cfg, weights, priorities)
            if sc is None:
                continue
            c = Candidate(clip=clip_idx, path=analysis["path"], start=round(float(s), 3),
                          end=round(float(e), 3), i0=i0, i1=i1, parts=sc["parts"],
                          base=sc["base"], movement=sc["movement"],
                          consistency=sc["consistency"], category=sc["category"],
                          cat_vec=sc["cats"],
                          struct=_unit(struct_all[i0:i1].mean(axis=0)),
                          colour=_unit(colour_all[i0:i1].mean(axis=0)),
                          sal_x=float(np.median(tl["sal_x"][i0:i1])),
                          sal_focus=float(np.mean(tl["sal_focus"][i0:i1])),
                          order=int(analysis.get("order", clip_idx)),
                          scene_lo=lo, scene_hi=hi)
            out.append(c)
    # Non-maximum suppression: keep the best, drop anything overlapping it.
    out.sort(key=lambda c: -c.base)
    kept: list[Candidate] = []
    for c in out:
        if all(c.end + 0.5 <= k.start or c.start >= k.end + 0.5 for k in kept if k.clip == c.clip):
            kept.append(c)
        if len(kept) >= per_clip:
            break
    _ = hz
    return kept
