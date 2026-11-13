# SPDX-License-Identifier: AGPL-3.0-or-later
"""How alike two candidate shots are, 0..1.

Following the lesson VideoHighlighter's :mod:`modules.segments.shot_look`
documents from real shoots — any single descriptor fails on one-colour
landscapes — similarity is the *smaller* of layout agreement and palette
agreement, so it only reports "same shot" when both agree. Here it is computed
per window (not per file), because one long drone take holds several views.

Two windows of the same take close in time are also near-duplicates even when
the camera moved, and a near-identical landscape category mix nudges it up.
"""
from __future__ import annotations

import numpy as np


def similarity(a, b) -> float:
    s = float(np.dot(a.struct, b.struct))
    c = float(np.dot(a.colour, b.colour))
    visual = min(max(0.0, s), max(0.0, c))
    if a.cat_vec is not None and b.cat_vec is not None:
        na, nb = np.linalg.norm(a.cat_vec), np.linalg.norm(b.cat_vec)
        cat = float(np.dot(a.cat_vec, b.cat_vec) / (na * nb)) if na and nb else 0.0
        visual = visual * (0.85 + 0.15 * cat)
    if a.path == b.path:
        gap = max(0.0, max(a.start, b.start) - min(a.end, b.end))
        visual = max(visual, 0.95 - 0.04 * gap)      # adjacent seconds of one take
    return float(min(1.0, visual))


def max_similarity(c, chosen) -> float:
    return max((similarity(c, k) for k in chosen), default=0.0)
