# SPDX-License-Identifier: AGPL-3.0-or-later
"""Camera motion of drone footage: what move it is, and whether it is smooth.

The problem with the obvious approach
=====================================
"Stability = little optical flow" (what most auto-editors do) punishes exactly
the shots a drone is for: a slow orbit or a fly-over moves every pixel. The
quantity that separates a *good* move from a bad one is not how much the image
moves, it is whether the motion is **coherent and smooth over time**.

What is measured
================
For each pair of adjacent frames the dominant camera motion is fitted as a
similarity transform (RANSAC over Lucas–Kanade tracked corners):

* ``vx, vy``  — translation of the frame centre, in frame-widths per second;
* ``zoom``    — log scale change per second (+ = push in / forward, − = pull out);
* ``rot``     — roll in degrees per second;
* ``parallax``— how differently the top and bottom of the frame move after the
  fit — near zero for a pure pan (camera rotates in place), clearly non-zero for
  an orbit, a lateral truck or a low fly-over, where depth produces parallax;
* ``inliers`` — fraction of tracks the fit explains (low = water, sky, chaos).

Smoothness then compares each pair's instantaneous motion with the local trend
(a ~1.75 s moving average). A gimbal-stabilised pan has instantaneous motion
equal to its trend → jitter ≈ 0, however fast it is. Prop vibration, wind
buffeting or a jerky stick input shows up as instantaneous motion that disagrees
with the trend → high jitter. ``jerk`` is the change of the trend itself and
catches direction reversals ("searching for the framing").

Thresholds are initial values from synthetic tests (see tests/test_drone_*)
and must be calibrated on real DJI footage; they live in ``drone.config``.
"""
from __future__ import annotations

import math
from typing import Optional

import cv2
import numpy as np

_LK = dict(winSize=(21, 21), maxLevel=3,
           criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
_FEATURES = dict(maxCorners=300, qualityLevel=0.01, minDistance=8, blockSize=7)

MOVEMENTS = ("hover", "pan", "orbit", "push_in", "pull_out",
             "rise_reveal", "descend", "flyover", "fast")

# How much each move is worth as cinematography, 0..1. Initial values, tunable.
MOVEMENT_VALUE = {
    "rise_reveal": 1.00, "orbit": 0.95, "push_in": 0.90, "flyover": 0.90,
    "pull_out": 0.85, "pan": 0.70, "descend": 0.65, "hover": 0.50, "fast": 0.25,
}


def pair_motion(g0: np.ndarray, g1: np.ndarray, fps: float) -> dict:
    """Instantaneous camera motion between two adjacent grayscale frames."""
    h, w = g0.shape
    out = {"vx": 0.0, "vy": 0.0, "zoom": 0.0, "rot": 0.0, "parallax": 0.0,
           "residual": 0.0, "inliers": 0.0, "valid": 0}
    pts = cv2.goodFeaturesToTrack(g0, mask=None, **_FEATURES)
    if pts is None or len(pts) < 12:
        return out
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(g0, g1, pts, None, **_LK)
    if nxt is None:
        return out
    ok = st.ravel() == 1
    p0 = pts[ok].reshape(-1, 2)
    p1 = nxt[ok].reshape(-1, 2)
    if len(p0) < 12:
        return out
    M, inl = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC,
                                         ransacReprojThreshold=1.5,
                                         maxIters=500, confidence=0.98)
    if M is None:
        return out
    a, b = float(M[0, 0]), float(M[1, 0])
    scale = math.hypot(a, b)
    rot = math.degrees(math.atan2(b, a))
    cx, cy = w / 2.0, h / 2.0
    ncx = M[0, 0] * cx + M[0, 1] * cy + M[0, 2]
    ncy = M[1, 0] * cx + M[1, 1] * cy + M[1, 2]
    pred = p0 @ M[:, :2].T + M[:, 2]
    res = p1 - pred
    resid = float(np.median(np.linalg.norm(res, axis=1)))
    # Parallax: residual horizontal/vertical drift of the far (top) vs near
    # (bottom) band. A pan leaves none; depth-dependent motion leaves some.
    ys = p0[:, 1]
    top, bot = ys < h * 0.4, ys > h * 0.6
    par = 0.0
    if top.sum() >= 5 and bot.sum() >= 5:
        d = np.median(res[bot], axis=0) - np.median(res[top], axis=0)
        par = float(np.hypot(d[0], d[1]))
        # A raw flow comparison catches parallax the similarity fit absorbed.
        raw = np.median((p1 - p0)[bot], axis=0) - np.median((p1 - p0)[top], axis=0)
        par = max(par, float(abs(raw[0])) * 0.5)
    out.update({
        "vx": float(ncx - cx) / w * fps,
        "vy": float(ncy - cy) / w * fps,
        "zoom": math.log(max(1e-6, scale)) * fps,
        "rot": rot * fps,
        "parallax": par / w * fps,
        "residual": resid / w * fps,
        "inliers": float(inl.mean()) if inl is not None else 0.0,
        "valid": 1,
    })
    return out


def _moving_average(x: np.ndarray, k: int) -> np.ndarray:
    if len(x) == 0:
        return x
    k = max(1, min(k, len(x)))
    if k % 2 == 0:
        k -= 1
    pad = k // 2
    xp = np.pad(x, (pad, pad), mode="edge")
    return np.convolve(xp, np.ones(k) / k, mode="valid")


def smoothness_series(vx, vy, zoom, rot, valid, *, sample_hz: float,
                      trend_seconds: float = 1.75) -> dict:
    """Per-sample jitter and jerk (frame-widths/s), plus the smoothed trend."""
    vx, vy, zoom, rot = (np.asarray(a, dtype=np.float64) for a in (vx, vy, zoom, rot))
    valid = np.asarray(valid, dtype=bool)
    k = max(3, int(round(trend_seconds * sample_hz)))
    # Fill invalid samples from neighbours so they do not fake a spike.
    def fill(a):
        a = a.copy()
        if valid.any() and not valid.all():
            idx = np.arange(len(a))
            a[~valid] = np.interp(idx[~valid], idx[valid], a[valid])
        return a
    vx, vy, zoom, rot = fill(vx), fill(vy), fill(zoom), fill(rot)
    # Roll and zoom are put on the same footing as translation: 1 deg/s of roll
    # reads about like 1% of frame width per second of slide.
    comps = [vx, vy, zoom * 0.5, rot * 0.01]
    trends = [_moving_average(c, k) for c in comps]
    jitter = np.sqrt(sum((c - t) ** 2 for c, t in zip(comps, trends)))
    jerk = np.zeros_like(jitter)
    if len(jitter) > 1:
        dt = 1.0 / sample_hz
        jerk[1:] = np.sqrt(sum(np.diff(t) ** 2 for t in trends)) / dt
    speed = np.sqrt(trends[0] ** 2 + trends[1] ** 2)
    return {"jitter": jitter, "jerk": jerk, "speed": speed,
            "tvx": trends[0], "tvy": trends[1], "tzoom": trends[2] * 2.0,
            "trot": trends[3] * 100.0}


def classify_movement(tvx, tvy, tzoom, parallax, speed, *, cfg) -> tuple[str, float]:
    """Dominant move over a window and how consistent it was (0..1).

    Signs (image coordinates, y down): a drone rising with a level gimbal, or a
    gimbal tilting up, moves the scene *down* the frame → ``vy > 0`` = reveal.
    """
    tvx, tvy, tzoom = (np.asarray(a, dtype=np.float64) for a in (tvx, tvy, tzoom))
    mvx, mvy, mz = float(np.mean(tvx)), float(np.mean(tvy)), float(np.mean(tzoom))
    mspeed = float(np.mean(speed)) if len(speed) else 0.0
    mpar = float(np.median(parallax)) if len(parallax) else 0.0

    # Consistency: how much of the motion keeps one direction (1 = steady).
    def consistency(a):
        a = np.asarray(a)
        mag = np.abs(a).mean()
        return float(abs(a.mean()) / mag) if mag > 1e-9 else 1.0

    if mspeed > cfg.fast_speed:
        return "fast", consistency(tvx if abs(mvx) >= abs(mvy) else tvy)
    zoom_dom = abs(mz) * 0.5
    if mspeed < cfg.hover_speed and abs(mz) < cfg.zoom_rate:
        return "hover", 1.0
    if abs(mz) >= cfg.zoom_rate and zoom_dom >= max(abs(mvx), abs(mvy)) * 0.6:
        kind = "push_in" if mz > 0 else "pull_out"
        if kind == "push_in" and mpar > cfg.parallax_orbit and mspeed > cfg.hover_speed:
            kind = "flyover"
        return kind, consistency(tzoom)
    if abs(mvy) > abs(mvx) * 1.2:
        return ("rise_reveal" if mvy > 0 else "descend"), consistency(tvy)
    if abs(mvx) >= abs(mvy) * 1.2:
        kind = "orbit" if mpar > cfg.parallax_orbit else "pan"
        return kind, consistency(tvx)
    # Diagonal / mixed motion over depth: treat as a fly-over.
    return ("flyover" if mpar > cfg.parallax_orbit else "pan"), \
        0.5 * (consistency(tvx) + consistency(tvy))


def smoothness_score(jitter: np.ndarray, jerk: np.ndarray, *, cfg) -> float:
    """0..1 — high when the window's motion is smooth, regardless of speed."""
    if len(jitter) == 0:
        return 0.5
    j = float(np.quantile(jitter, 0.8))
    s_j = 1.0 - min(1.0, max(0.0, (j - cfg.jitter_ok) / max(1e-6, cfg.jitter_bad - cfg.jitter_ok)))
    k = float(np.quantile(jerk, 0.8)) if len(jerk) else 0.0
    s_k = 1.0 - min(1.0, max(0.0, (k - cfg.jerk_ok) / max(1e-6, cfg.jerk_bad - cfg.jerk_ok)))
    return float(0.75 * s_j + 0.25 * s_k)


def movement_score(kind: str, consistency: float, speed: float, *, cfg) -> float:
    """0..1 — how cinematic the move is: its type, steadiness and pace."""
    base = MOVEMENT_VALUE.get(kind, 0.5)
    if speed <= cfg.comfort_speed:
        pace = 1.0
    else:
        pace = max(0.0, 1.0 - (speed - cfg.comfort_speed) / max(1e-6, cfg.fast_speed - cfg.comfort_speed))
    return float(base * (0.55 + 0.45 * consistency) * (0.4 + 0.6 * pace))


def is_unusable(jitter_p80: Optional[float], cfg) -> bool:
    return jitter_p80 is not None and jitter_p80 > cfg.jitter_reject
