# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-frame visual measurements, on a downscaled BGR frame.

Everything here is classical, cheap image statistics (~2 ms per 480p frame).
None of it knows what the scene *is* — that is :mod:`drone.landscape`'s job.

Sharpness note: the Laplacian variance depends on what is in the frame as much
as on focus (a waterfall is textured, a lake is not), so the scorer judges it
twice — against an absolute floor, and against the clip's own upper range —
rather than trusting one fixed threshold across a whole trip.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

SIG_W, SIG_H = 16, 9          # structure signature grid
HIST_H, HIST_S = 8, 4         # hue x saturation colour signature


def colourfulness(bgr: np.ndarray) -> float:
    """Hasler & Süsstrunk (2003) colourfulness, ~0 (grey) to ~150 (vivid)."""
    b, g, r = [c.astype(np.float32) for c in cv2.split(bgr)]
    rg = r - g
    yb = 0.5 * (r + g) - b
    return float(math.sqrt(rg.std() ** 2 + yb.std() ** 2)
                 + 0.3 * math.sqrt(rg.mean() ** 2 + yb.mean() ** 2))


def spectral_saliency_x(gray: np.ndarray) -> tuple[float, float]:
    """Horizontal centre of visual interest (0..1) via spectral residual
    saliency (Hou & Zhang 2007), and how concentrated it is (0..1).

    Used to place the 9:16 crop window on the subject instead of the middle.
    """
    small = cv2.resize(gray, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)
    f = np.fft.fft2(small)
    log_amp = np.log(np.abs(f) + 1e-6)
    phase = np.angle(f)
    residual = log_amp - cv2.blur(log_amp, (3, 3))
    sal = np.abs(np.fft.ifft2(np.exp(residual + 1j * phase))) ** 2
    sal = cv2.GaussianBlur(sal.astype(np.float32), (5, 5), 2.5)
    thr = np.quantile(sal, 0.90)
    mask = sal >= thr
    weights = (sal * mask).sum(axis=0)
    total = float(weights.sum())
    if total <= 0:
        return 0.5, 0.0
    xs = (np.arange(sal.shape[1]) + 0.5) / sal.shape[1]
    cx = float((weights * xs).sum() / total)
    spread = float(np.sqrt((weights * (xs - cx) ** 2).sum() / total))
    return cx, float(max(0.0, 1.0 - spread / 0.29))


def signatures(bgr: np.ndarray, gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(structure, colour) unit vectors used for near-duplicate detection."""
    s = cv2.resize(gray, (SIG_W, SIG_H), interpolation=cv2.INTER_AREA).astype(np.float32).ravel()
    s -= s.mean()
    s /= (np.linalg.norm(s) + 1e-6)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [HIST_H, HIST_S], [0, 180, 0, 256]).ravel()
    hist = np.sqrt(hist)                       # Hellinger: tames dominant colours
    hist /= (np.linalg.norm(hist) + 1e-6)
    return s, hist.astype(np.float32)


def measure(bgr: np.ndarray) -> tuple[dict, np.ndarray, np.ndarray, np.ndarray]:
    """Return (scalar metrics, gray, structure sig, colour sig)."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    lap = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    sharp = float(lap.var())
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel() / gray.size
    clip_lo = float(hist[:6].sum())
    clip_hi = float(hist[250:].sum())
    mean_l = float(gray.mean()) / 255.0
    contrast = float(gray.std()) / 255.0

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hch, sch, vch = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    vivid = (sch > 45) & (vch > 40)
    green = float((vivid & (hch >= 33) & (hch <= 85)).mean())
    blue = (vivid & (hch >= 86) & (hch <= 130))
    warm = float((vivid & ((hch <= 18) | (hch >= 170)) & (sch > 80)).mean())
    top = slice(0, h // 3)
    rest = slice(h // 3, h)
    # Sky: blue, or bright and unsaturated, in the top third.
    sky_top = float((blue[top] | ((vch[top] > 170) & (sch[top] < 45))).mean())
    water = float(blue[rest].mean())
    white_rest = float(((vch[rest] > 200) & (sch[rest] < 35)).mean())  # foam, clouds
    edges = cv2.Canny(gray, 80, 160)
    edge_density = float((edges > 0).mean())
    sal_x, sal_focus = spectral_saliency_x(gray)
    struct, colour = signatures(bgr, gray)
    m = {
        "sharp": sharp, "clip_lo": clip_lo, "clip_hi": clip_hi, "mean_l": mean_l,
        "contrast": contrast, "colorful": colourfulness(bgr), "green": green,
        "water": water, "warm": warm, "sky": sky_top, "white": white_rest,
        "edges": edge_density, "sal_x": sal_x, "sal_focus": sal_focus,
    }
    return m, gray, struct, colour


def hist_difference(colour_a: np.ndarray, colour_b: np.ndarray) -> float:
    """0 (same palette) .. 1 (disjoint) between two colour signatures."""
    return float(max(0.0, 1.0 - float(np.dot(colour_a, colour_b))))
