# SPDX-License-Identifier: AGPL-3.0-or-later
"""How much good, non-repetitive footage is there — and how long should the film be?

Used for two features:

* **Duração automática** — the film is as long as the material deserves: a
  short flight gives 20–40 s, a big trip several minutes.
* **Aviso de falta de material** — when the requested length cannot be filled
  at the style's normal pace, the user decides: shorten the film, or stretch
  the shots (and accept more similar shots).

Three numbers per set of candidates, all without repeating a view:

* ``comfortable`` — every distinct usable shot at the style's normal length;
* ``maximum``     — every distinct shot stretched up to 1.6× the style's
  longest shot (what "esticar" can reach);
* ``suggested``   — only the *strong* shots at normal length, rounded down to
  5 s. Weak-but-usable shots are left out: they are what makes a film drag.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from drone import shot_selection

MIN_SUGGESTED = 15.0
MAX_SUGGESTED = 3600.0           # only a sanity bound: the material itself limits the suggestion
MAX_SUGGESTED_VERTICAL = 60.0


@dataclass
class MaterialEstimate:
    distinct: int = 0
    strong: int = 0
    comfortable: float = 0.0
    maximum: float = 0.0
    suggested: float = 0.0
    suggested_vertical: float = 0.0

    def describe(self) -> str:
        return (f"{self.distinct} tomadas distintas ({self.strong} fortes): "
                f"~{self.comfortable:.0f} s em ritmo normal, até ~{self.maximum:.0f} s esticando; "
                f"sugestão {self.suggested:.0f} s (vertical {self.suggested_vertical:.0f} s)")


def _floor5(x: float) -> float:
    return float(math.floor(x / 5.0) * 5)


def estimate(cands: list, style, cfg, weights) -> MaterialEstimate:
    est = MaterialEstimate()
    if not cands:
        return est
    pool = shot_selection.select(cands, len(cands), w_variety=weights.variety,
                                 duplicate_threshold=cfg.duplicate_threshold,
                                 max_per_clip=getattr(style, "max_per_clip", 0) or cfg.max_shots_per_clip,
                                 relax=False,
                                 log_fn=lambda *_: None)
    if not pool:
        return est
    normal = 0.5 * (style.shot_min + style.shot_max)
    cap = style.shot_max * 1.6
    est.distinct = len(pool)
    est.comfortable = sum(min(c.scene_hi - c.scene_lo, normal) for c in pool)
    # Stretching is bounded by each clip's usable footage, shared by its shots.
    per_clip = defaultdict(list)
    for c in pool:
        per_clip[c.clip].append(c)
    for shots in per_clip.values():
        spans = {(round(c.scene_lo, 2), round(c.scene_hi, 2)) for c in shots}
        usable = sum(hi - lo for lo, hi in spans)
        est.maximum += min(usable, sum(min(c.scene_hi - c.scene_lo, cap) for c in shots))
    est.maximum = max(est.maximum, est.comfortable)
    top = sorted((c.base for c in pool), reverse=True)[:5]
    bar = 0.8 * sum(top) / len(top)
    strong = [c for c in pool if c.base >= bar]
    est.strong = len(strong)
    raw = sum(min(c.scene_hi - c.scene_lo, normal) for c in strong)
    est.suggested = min(MAX_SUGGESTED, max(MIN_SUGGESTED, _floor5(raw)))
    est.suggested = min(est.suggested, max(MIN_SUGGESTED, _floor5(est.comfortable)))
    est.suggested_vertical = min(MAX_SUGGESTED_VERTICAL,
                                 max(MIN_SUGGESTED, _floor5(est.suggested * 0.5)))
    return est


def clock(sec: float) -> str:
    sec = int(round(max(0.0, sec)))
    return f"{sec // 60}:{sec % 60:02d}"


def choices(est: MaterialEstimate, vertical: bool = False) -> list:
    """Round lengths worth offering for this material, in seconds, up to what
    stretching can reach."""
    top = max(est.maximum, est.comfortable, MIN_SUGGESTED)
    steps = [15, 30, 45, 60, 90, 120, 150, 180, 240, 300, 360, 420, 480, 540, 600]
    steps += list(range(720, 3601, 120))
    return [float(x) for x in steps if x <= top + 1e-6] or [MIN_SUGGESTED]


def parse_clock(text: str) -> float:
    """'90', '90 s', '1:30', '1m30' → seconds (0 when it is not a length)."""
    t = (text or "").strip().lower().split("·")[0].strip().replace(",", ".")
    try:
        if ":" in t:
            m, sec = t.split(":", 1)
            return max(0.0, float(m) * 60 + float(sec or 0))
        if "m" in t:
            m, sec = t.split("m", 1)
            return max(0.0, float(m) * 60 + float(sec.replace("s", "").strip() or 0))
        return max(0.0, float(t.replace("s", "").strip()))
    except ValueError:
        return 0.0


def shortfall(target: float, est: MaterialEstimate, unit: float) -> bool:
    """True when ``target`` cannot be filled at the style's normal pace."""
    return est.distinct > 0 and target > est.comfortable + max(unit, 1.0)
