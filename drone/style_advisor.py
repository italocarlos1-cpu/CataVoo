# SPDX-License-Identifier: AGPL-3.0-or-later
"""The "Automático" style: read the footage, then pick the edit style.

No AI involved. The analysis already knows, for every quarter second of every
clip, what landscape it shows, how the camera moves, how steady it is and how
warm the light is. This module summarises that into a *profile* of the trip
and scores each style against it with transparent rules, so the log can say
*why* — e.g. "Pôr do sol: 34% das cenas com luz de pôr do sol".

The chosen style is then run with adaptive rhythm (each shot's length follows
its own character), which is what makes the edit feel "decided by the scene".

All weights below are first guesses meant to be tuned on real trips.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from drone import motion_analysis
from drone.config import STYLES
from drone.landscape import CAT_KEYS, LABEL_PT

NATURE = ("waterfall", "mountains", "lake", "river", "forest", "canyon", "coast")
URBAN = ("historic", "city")
REVEALS = ("rise_reveal", "push_in", "orbit", "pull_out")


@dataclass
class TripProfile:
    clips: int = 0
    seconds: float = 0.0
    categories: dict = field(default_factory=dict)    # share of footage per landscape
    movements: dict = field(default_factory=dict)     # share of 3 s chunks per move
    smooth_share: float = 0.0
    interest: float = 0.0
    variety: float = 0.0                              # 0..1 landscape entropy
    span_hours: float = 0.0                           # first → last recording
    spread_km: float = 0.0                            # GPS extent

    def share(self, keys) -> float:
        return float(sum(self.categories.get(k, 0.0) for k in keys))

    def moves(self, keys) -> float:
        return float(sum(self.movements.get(k, 0.0) for k in keys))


def _parse_time(text: str):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def profile(analyses: list, cfg) -> TripProfile:
    p = TripProfile(clips=len(analyses))
    cat_sum = np.zeros(len(CAT_KEYS))
    moves: dict = {}
    n_chunks = 0
    smooth = total = 0
    interest = []
    for a in analyses:
        tl = a["timeline"]
        n = len(tl["t"])
        p.seconds += float(a.get("duration", 0))
        if "cat_probs" in tl:
            cat_sum += np.asarray(tl["cat_probs"]).sum(axis=0)
        if "interest" in tl:
            interest.extend(np.asarray(tl["interest"]).tolist())
        jit = np.asarray(tl["jitter"])
        smooth += int((jit < cfg.motion.jitter_ok * 2).sum())
        total += n
        hz = float(a.get("sample_hz", cfg.analysis.sample_hz))
        step = max(4, int(3 * hz))                       # 3-second chunks
        for i0 in range(0, max(1, n - step + 1), step):
            sl = slice(i0, i0 + step)
            kind, _ = motion_analysis.classify_movement(
                tl["tvx"][sl], tl["tvy"][sl], tl["tzoom"][sl], tl["parallax"][sl],
                tl["speed"][sl], cfg=cfg.motion)
            moves[kind] = moves.get(kind, 0) + 1
            n_chunks += 1
    if cat_sum.sum() > 0:
        shares = cat_sum / cat_sum.sum()
        p.categories = {k: float(v) for k, v in zip(CAT_KEYS, shares)}
        nz = shares[shares > 1e-9]
        p.variety = float(-(nz * np.log(nz)).sum() / math.log(len(CAT_KEYS)))
    p.movements = {k: v / max(1, n_chunks) for k, v in moves.items()}
    p.smooth_share = smooth / max(1, total)
    p.interest = float(np.mean(interest)) if interest else 0.5
    times = [t for t in (_parse_time(a.get("creation_time", "")) for a in analyses) if t]
    if len(times) >= 2:
        p.span_hours = (max(times) - min(times)).total_seconds() / 3600.0
    pts = [(a["lat"], a["lon"]) for a in analyses if a.get("lat") is not None and a.get("lon") is not None]
    if len(pts) >= 2:
        lats, lons = [x[0] for x in pts], [x[1] for x in pts]
        p.spread_km = _km(min(lats), min(lons), max(lats), max(lons))
    return p


def score_styles(p: TripProfile, *, vertical_only: bool = False, target: float = 90.0) -> dict:
    """{style key: (score 0..1, reason)}. The best score wins."""
    sunset = p.share(("sunset",))
    nature = p.share(NATURE)
    urban = p.share(URBAN)
    fast = p.moves(("fast", "flyover"))
    calm = p.moves(("hover", "pan"))
    reveals = p.moves(REVEALS)
    pct = lambda x: f"{100 * x:.0f}%"  # noqa: E731
    clamp = lambda x: max(0.0, min(1.0, x))  # noqa: E731
    out = {
        "cinematografico": (0.50, "equilíbrio — boa escolha quando nada se destaca"),
        "por_do_sol": (clamp(0.20 + 2.2 * sunset), f"{pct(sunset)} das cenas com luz de pôr do sol"),
        "cidade_historica": (clamp(0.15 + 1.3 * urban), f"{pct(urban)} das cenas com cidade/arquitetura"),
        "contemplativo": (clamp(0.15 + 0.55 * nature + 0.25 * p.smooth_share - 0.8 * fast),
                          f"{pct(nature)} natureza, {pct(p.smooth_share)} de imagens estáveis"),
        "minimalista": (clamp(0.10 + (0.25 if p.clips <= 6 else 0.0) + 0.45 * calm
                              + 0.15 * p.smooth_share - 0.35 * p.variety),
                        f"poucos clipes ({p.clips}) e {pct(calm)} de tomadas paradas/panorâmicas"),
        "aventura_fpv": (clamp(0.05 + 1.8 * fast), f"{pct(fast)} de sobrevoos/movimentos rápidos"),
        "viagem_dinamica": (clamp(0.15 + 0.40 * p.variety + (0.15 if p.clips >= 15 else 0.0) + 0.5 * fast),
                            f"muita variedade de lugares ({p.clips} clipes)"),
        "trailer_epico": (clamp(0.10 + 0.55 * reveals + 0.35 * p.interest
                                - (0.25 if target < 30 or target > 150 else 0.0)),
                          f"{pct(reveals)} de revelações/órbitas/aproximações e paisagens fortes"),
        "diario_viagem": (clamp(0.10 + (0.30 if p.span_hours >= 6 else 0.0)
                                + (0.25 if p.spread_km >= 5 else 0.0) + 0.1 * p.variety),
                          f"filmagens espalhadas por {p.span_hours:.0f} h e {p.spread_km:.0f} km"),
        "redes_sociais": ((0.95, "somente vertical e curto") if vertical_only and target <= 35
                          else (0.05, "")),
    }
    return out


def choose(analyses: list, cfg, *, vertical_only: bool = False, target: float = 90.0,
           log_fn=print) -> tuple[str, dict, TripProfile]:
    p = profile(analyses, cfg)
    scores = score_styles(p, vertical_only=vertical_only, target=target)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1][0])
    best, (sc, why) = ranked[0]
    top_cats = sorted(p.categories.items(), key=lambda kv: -kv[1])[:3]
    log_fn("🧭 Perfil da viagem: " + ", ".join(f"{LABEL_PT.get(k, k)} {100 * v:.0f}%" for k, v in top_cats)
           + f"; {p.clips} clipes, {p.seconds / 60:.1f} min")
    log_fn(f"🧭 Estilo automático: {STYLES[best].label} ({sc:.2f}) — {why}. Alternativas: "
           + ", ".join(f"{STYLES[k].label} {v[0]:.2f}" for k, v in ranked[1:3]))
    return best, {k: round(v[0], 3) for k, v in scores.items()}, p
