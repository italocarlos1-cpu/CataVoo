# SPDX-License-Identifier: AGPL-3.0-or-later
"""Synthetic aerial footage with known camera moves, for the drone tests.

A large procedural "landscape" (sky, ridges, forest texture, a lake and a
white waterfall streak) is filmed by a virtual camera: a crop window that
pans, zooms, rises or jitters. Ground truth is therefore exact. Synthetic
footage cannot replace real DJI clips for calibration — it only proves the
algorithms respond to the right *kind* of change.
"""
from __future__ import annotations

import os
import subprocess

import cv2
import numpy as np

W, H, FPS = 640, 360, 30


def landscape(seed: int = 0, w: int = 2600, h: int = 1500, hue_shift: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), np.float32)
    y = np.linspace(0, 1, h)[:, None]
    sky = np.dstack([np.full((h, w), 235), np.full((h, w), 180) - 40 * y, np.full((h, w), 120) - 40 * y])
    img[:] = sky
    # ridges
    x = np.arange(w)
    ridge = (0.38 + 0.08 * np.sin(x / 210.0 + seed) + 0.05 * np.sin(x / 73.0)) * h
    noise = cv2.resize(rng.random((h // 8, w // 8)).astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
    fine = rng.random((h, w)).astype(np.float32)
    tex = 0.6 * noise + 0.4 * cv2.GaussianBlur(fine, (0, 0), 1.2)
    mask = (np.arange(h)[:, None] > ridge[None, :])
    green = np.dstack([40 + 60 * tex, 110 + 90 * tex, 50 + 40 * tex])
    img[mask] = green[mask]
    # lake
    cy, cx = int(h * 0.78), int(w * 0.35)
    yy, xx = np.ogrid[:h, :w]
    lake = ((yy - cy) / (h * 0.10)) ** 2 + ((xx - cx) / (w * 0.14)) ** 2 < 1
    img[lake] = np.dstack([170 + 40 * tex, 110 + 30 * tex, 40 + 20 * tex])[lake]
    # waterfall streak
    fx = int(w * 0.62)
    band = (xx > fx) & (xx < fx + 40) & (yy > ridge[fx] - 10) & (yy < h * 0.75)
    img[band] = np.dstack([235 + 15 * tex, 240 + 10 * tex, 240 + 10 * tex])[band]
    # houses with warm roofs (texture/corners for tracking)
    for _ in range(60):
        px, py = int(rng.integers(0, w - 30)), int(rng.integers(int(h * 0.5), h - 20))
        cv2.rectangle(img, (px, py), (px + 22, py + 14), (40, 70, 200), -1)
    img = np.clip(img, 0, 255).astype(np.uint8)
    if hue_shift:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        hsv[..., 0] = (hsv[..., 0].astype(int) + hue_shift) % 180
        img = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    return img


def _writer(path: str, w=W, h=H, fps=FPS, audio=False):
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-pix_fmt", "yuv420p", path]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def render_move(path: str, scene: np.ndarray, *, seconds: float, move: str,
                speed: float = 1.0, jitter_px: float = 0.0, blur: float = 0.0, seed: int = 1,
                start=(0.5, 0.55), view=0.42):
    """Write a clip. ``move``: hover | pan | push_in | pull_out | rise | orbit."""
    rng = np.random.default_rng(seed)
    sh, sw = scene.shape[:2]
    n = int(seconds * FPS)
    proc = _writer(path)
    vw0 = sw * view
    for i in range(n):
        u = i / max(1, n - 1)
        cx, cy = start[0] * sw, start[1] * sh
        vw = vw0
        if move == "pan":
            cx += (u - 0.5) * sw * 0.25 * speed
        elif move == "push_in":
            vw = vw0 * (1.0 - 0.45 * u * speed)
        elif move == "pull_out":
            vw = vw0 * (0.55 + 0.45 * u * speed)
        elif move == "rise":
            cy -= (u - 0.5) * sh * 0.25 * speed      # window moves up → scene moves down
        if jitter_px:
            cx += rng.normal(0, jitter_px) * vw / W
            cy += rng.normal(0, jitter_px) * vw / W
        vh = vw * H / W
        x0, y0 = cx - vw / 2, cy - vh / 2
        M = np.array([[vw / W, 0, x0], [0, vh / H, y0]], np.float32)
        frame = cv2.warpAffine(scene, M, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                               borderMode=cv2.BORDER_REFLECT)
        if move == "orbit":
            # Two depth layers moving at different speeds = parallax.
            bg_shift = (u - 0.5) * 120 * speed
            fg_shift = (u - 0.5) * 420 * speed
            Mb = np.array([[vw / W, 0, x0 + bg_shift], [0, vh / H, y0]], np.float32)
            Mf = np.array([[vw / W, 0, x0 + fg_shift], [0, vh / H, y0]], np.float32)
            bg = cv2.warpAffine(scene, Mb, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)
            fg = cv2.warpAffine(scene, Mf, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)
            frame = bg.copy()
            frame[H // 2:] = fg[H // 2:]
        if blur:
            frame = cv2.GaussianBlur(frame, (0, 0), blur)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    proc.wait()
    return path


def make_music(path: str, seconds: float = 120.0, bpm: float = 96.0):
    """A click track with a bass note on every downbeat — easy beats to find."""
    beat = 60.0 / bpm
    expr = (f"0.6*sin(2*PI*880*t)*exp(-40*mod(t\\,{beat:.6f}))"
            f"+0.5*sin(2*PI*110*t)*exp(-8*mod(t\\,{beat * 4:.6f}))")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"aevalsrc='{expr}':s=44100:d={seconds}", "-ac", "2", path], check=True)
    return path


def make_trip(folder: str) -> dict:
    """A small 'trip' folder with known-good and known-bad shots."""
    os.makedirs(folder, exist_ok=True)
    a, b, c = landscape(1), landscape(2, hue_shift=8), landscape(3, hue_shift=-6)
    spec = {
        "DJI_0001.MP4": dict(scene=a, seconds=10, move="pan"),
        "DJI_0002.MP4": dict(scene=b, seconds=9, move="push_in"),
        "DJI_0003.MP4": dict(scene=c, seconds=9, move="rise"),
        "DJI_0004.MP4": dict(scene=a, seconds=8, move="hover", jitter_px=7.0),
        "DJI_0005.MP4": dict(scene=b, seconds=8, move="hover", blur=6.0, start=(0.3, 0.6)),
        "DJI_0006.MP4": dict(scene=c, seconds=9, move="orbit", start=(0.4, 0.6)),
        "DJI_0007.MP4": dict(scene=a, seconds=10, move="pan", seed=5),   # duplicate of 0001
        "DJI_0008.MOV": dict(scene=b, seconds=9, move="pull_out", start=(0.65, 0.5)),
    }
    for name, kw in spec.items():
        p = os.path.join(folder, name)
        if not os.path.exists(p):
            render_move(p, **kw)
    return {k: os.path.join(folder, k) for k in spec}
