# SPDX-License-Identifier: AGPL-3.0-or-later
"""From scored candidates to a finished cut list (a VideoHighlighter ``Edl``).

Order of decisions
==================
1. **Rhythm.** Shot lengths come from the music: a shot advances the reel by a
   whole number of beats, chosen so it falls inside the style's range (opener
   and closer get longer). With a transition after a cut, that cut is made
   longer by the transition, so every join *starts* on a beat — the same rule
   VideoHighlighter's ``quantise_to_music`` applies.
2. **Casting.** As many shots as slots are selected by MMR (quality + variety).
3. **Order.** The most striking reveal/push/orbit opens, a pull-out, rise or
   sunset closes; the middle alternates landscape type, move and source clip,
   leaning toward shooting order so a trip still reads as a journey.
4. **Fitting.** Each shot's window is re-centred and resized to its slot,
   inside the clip's usable span.
"""
from __future__ import annotations

import dataclasses
import hashlib
import math
from dataclasses import dataclass, field

import numpy as np

from drone import shot_selection
from drone.config import Style
from drone.duplicate_detection import similarity
from drone.landscape import CAT_KEYS

OPENERS = {"rise_reveal": 1.0, "push_in": 0.85, "flyover": 0.85, "orbit": 0.8, "pull_out": 0.5}
CLOSERS = {"pull_out": 1.0, "rise_reveal": 0.8, "hover": 0.6, "orbit": 0.6, "pan": 0.4}


@dataclass
class Slot:
    advance: float                       # how far this shot moves the reel on
    transition: str = "cut"
    transition_duration: float = 0.0
    role: str = "body"


@dataclass
class PlannedShot:
    candidate: object
    start: float
    end: float
    slot: Slot
    text: str = ""
    crop_x: float = 0.5                  # 0..1 centre of the 9:16 crop


@dataclass
class Plan:
    shots: list = field(default_factory=list)
    unit: float = 0.0
    total: float = 0.0
    music: object = None

    def describe(self) -> str:
        lines = [f"{len(self.shots)} tomadas, {self.total:.1f}s, unidade {self.unit:.3f}s"]
        t = 0.0
        for s in self.shots:
            c = s.candidate
            lines.append(f"  {t:6.1f}s  [{s.slot.role:6s}] {s.end - s.start:4.1f}s  "
                         f"{c.describe()}  → {s.slot.transition}")
            t += s.slot.advance
        return "\n".join(lines)


def _beats_for(unit: float, lo: float, hi: float) -> int:
    if unit <= 0:
        return 1
    best = max(1, round(((lo + hi) / 2) / unit))
    # Prefer an even number of beats (half/whole bars) when it stays in range.
    for k in (best, best + 1, best - 1):
        if k >= 1 and k % 2 == 0 and lo - 1e-6 <= k * unit <= hi + 1e-6:
            return k
    return best


def plan_slots(target: float, style: Style, unit: float) -> list[Slot]:
    """Slot lengths summing to ``target`` (rounded to the musical grid)."""
    if unit <= 0:
        unit = 0.5
    body = _beats_for(unit, style.shot_min, style.shot_max)
    long_k = max(body, _beats_for(unit, style.shot_max, style.shot_max * 1.5))
    total_units = max(body, int(round(target / unit)))
    if total_units < 2 * long_k + body:            # short reel: shorter bookends
        long_k = max(body, (total_units - body) // 2) if total_units >= 3 * body else body
    if total_units <= long_k:
        ks = [total_units]
    else:
        n_body = max(0, round((total_units - 2 * long_k) / body))
        pattern = [body, max(1, body - 1), body + 1] if style.lively and body > 2 else [body]
        ks = [long_k] + [pattern[i % len(pattern)] for i in range(n_body)] + [long_k]
        if style.pace_curve and n_body:
            # Shape the rhythm along the film (e.g. a trailer that accelerates).
            curve = np.asarray(style.pace_curve, dtype=float)
            xs = np.linspace(0.0, 1.0, len(curve))
            for i in range(n_body):
                m = float(np.interp((i + 0.5) / n_body, xs, curve))
                ks[1 + i] = max(1, round(ks[1 + i] * m))
        # Spread the remainder a beat at a time over the body (then the ends),
        # so no single shot absorbs it.
        diff = total_units - sum(ks)
        idx = list(range(1, len(ks) - 1)) or list(range(len(ks)))
        lo_k = max(1, math.floor(style.shot_min * min(style.pace_curve or (1.0,)) / unit * 0.75))
        i = 0
        guard = 0
        while diff and guard < 10000:
            j = idx[i % len(idx)]
            if diff > 0:
                ks[j] += 1
                diff -= 1
            elif ks[j] > lo_k:
                ks[j] -= 1
                diff += 1
            i += 1
            guard += 1
    slots = [Slot(advance=k * unit) for k in ks]
    slots[0].role = "abre"
    slots[-1].role = "fecha"
    # Transitions on a share of the joins (every join for share 1.0).
    if style.transition != "cut" and style.transition_share > 0:
        every = max(1, round(1 / style.transition_share))
        for j, s in enumerate(slots[:-1]):
            if j % every == every - 1 or every == 1 or j == len(slots) - 2:
                s.transition = style.transition
                s.transition_duration = min(style.transition_duration, s.advance * 0.3)
    return slots


def adjusted_style(style: Style, adj=None) -> Style:
    """The style with the user's pace / transition adjustments applied."""
    if adj is None:
        return style
    kw = {}
    if adj.shot_scale and abs(adj.shot_scale - 1.0) > 1e-6:
        kw.update(shot_min=style.shot_min * adj.shot_scale, shot_max=style.shot_max * adj.shot_scale)
    if adj.transitions == "cut":
        kw.update(transition="cut", transition_share=0.0, transition_duration=0.0)
    elif adj.transitions == "crossfade":
        kw.update(transition="crossfade", transition_share=1.0,
                  transition_duration=style.transition_duration or 0.6)
    return dataclasses.replace(style, **kw) if kw else style


def with_style_defaults(style: Style, adj=None):
    """The user's adjustments, filled in with the style's own preferences.
    What the user asked for always wins."""
    from drone.feedback import Adjustments
    a = dataclasses.replace(adj) if adj is not None else Adjustments()
    if not a.opener and style.opener:
        a.opener = style.opener
    if not a.closer and style.closer:
        a.closer = style.closer
    extra = [c for c in style.prefer if c not in a.prefer and c not in a.avoid_categories]
    if extra:
        a.prefer = list(a.prefer) + extra
    return a


# How long a shot "wants" to stay on screen, by its camera move.
SHOT_TEMPO = {"hover": 1.2, "pan": 1.15, "orbit": 1.1, "rise_reveal": 1.1, "pull_out": 1.1,
              "push_in": 1.0, "descend": 1.0, "flyover": 0.85, "fast": 0.7}


def shot_tempo(c) -> float:
    """Adaptive rhythm: a slow, beautiful, steady view earns more time; a quick
    fly-by or a merely average shot earns less."""
    f = SHOT_TEMPO.get(c.movement, 1.0)
    if c.parts.get("interest", 0) > 0.7:
        f *= 1.1
    if c.parts.get("smoothness", 1) < 0.6:
        f *= 0.85
    if c.parts.get("quality", 1) < 0.5:
        f *= 0.9
    return f


def adapt_slots(slots: list, shots: list, unit: float, style: Style) -> None:
    """Redistribute the body slots' beats according to each shot's tempo,
    keeping the total (and therefore the sync with the music) unchanged."""
    body = [i for i, s in enumerate(slots) if s.role == "body"]
    if len(body) < 2:
        return
    ks = {i: max(1, round(slots[i].advance / unit)) for i in body}
    total = sum(ks.values())
    w = {i: ks[i] * shot_tempo(shots[i]) for i in body}
    scale = total / sum(w.values())
    lo = max(1, math.floor(style.shot_min * 0.6 / unit))
    hi = max(lo + 1, math.ceil(style.shot_max * 1.5 / unit))
    new = {i: int(min(hi, max(lo, round(w[i] * scale)))) for i in body}
    diff = total - sum(new.values())
    order = sorted(body, key=lambda i: -shot_tempo(shots[i]))
    guard = 0
    while diff and guard < 1000:
        for i in (order if diff > 0 else reversed(order)):
            if diff > 0 and new[i] < hi:
                new[i] += 1
                diff -= 1
            elif diff < 0 and new[i] > lo:
                new[i] -= 1
                diff += 1
            if not diff:
                break
        guard += 1
    for i in body:
        slots[i].advance = new[i] * unit
        if slots[i].transition_duration:
            slots[i].transition_duration = min(style.transition_duration, slots[i].advance * 0.3)


def _wants(c, target: str) -> bool:
    return bool(target) and target in (c.category, c.movement)


def _order(shots: list, adj=None, chronological: float = 0.0) -> list:
    opener = adj.opener if adj is not None else ""
    closer_t = adj.closer if adj is not None else ""
    if len(shots) <= 2:
        return sorted(shots, key=lambda c: (-_wants(c, opener), -c.base))

    def hook(c):
        return (0.45 * c.parts["interest"] + 0.30 * OPENERS.get(c.movement, 0.3)
                + 0.25 * c.parts["quality"] + (0.6 if _wants(c, opener) else 0.0))

    def close(c):
        cat = 0.3 if c.category == "sunset" else 0.0
        return (0.40 * CLOSERS.get(c.movement, 0.3) + 0.35 * c.parts["interest"]
                + 0.25 * c.parts["smoothness"] + cat + (0.6 if _wants(c, closer_t) else 0.0))

    rest = list(shots)
    opener = max(rest, key=hook)
    rest.remove(opener)
    closer = max(rest, key=close)
    rest.remove(closer)
    seq = [opener]
    while rest:
        prev = seq[-1]

        def fit(c):
            v = c.base
            v -= 0.25 if c.category == prev.category else 0.0
            v -= 0.15 if c.movement == prev.movement else 0.0
            v -= 0.30 if c.clip == prev.clip else 0.0
            v -= 0.30 * similarity(c, prev)
            k = 1.0 + 12.0 * chronological
            v -= k * (0.02 * (c.order - prev.order) if c.order >= prev.order else 0.06 * (prev.order - c.order))
            return v
        nxt = max(rest, key=fit)
        seq.append(nxt)
        rest.remove(nxt)
    seq.append(closer)
    return seq


def strict_order(style) -> bool:
    """Diário de viagem: recording order, no exceptions (no hook-first opener,
    no closing shot pulled from earlier, no going back to a clip already left)."""
    return style.chronological >= 1.0


def _rec_key(c):
    return (c.order, c.start)


def _fit_window(c, length: float) -> tuple[float, float]:
    lo, hi = c.scene_lo, c.scene_hi
    length = min(length, hi - lo)
    s = c.center - length / 2
    s = min(max(s, lo), hi - length)
    return round(s, 3), round(s + length, 3)


def _overlaps(c, previous) -> bool:
    return any(c.path == p and c.start < e and s < c.end for p, s, e in previous)


def make_bonus(*, vertical: bool = False, adj=None, previous=(), style=None):
    """Extra selection score from the format, the user's adjustments and the
    previous version (so "trocar as tomadas" really brings new material)."""
    prefer = [CAT_KEYS.index(x) for x in (adj.prefer if adj else []) if x in CAT_KEYS]
    avoid = [CAT_KEYS.index(x) for x in (adj.avoid_categories if adj else []) if x in CAT_KEYS]
    seed = adj.variation if adj else 0
    new_shots = bool(adj and adj.new_shots)
    bias = dict(style.movement_bias) if style is not None else {}

    def bonus(c):
        v = 0.08 * c.sal_focus if vertical else 0.0     # 9:16 keeps a third of the width
        if c.cat_vec is not None:
            vec = np.asarray(c.cat_vec)
            if prefer:
                v += 0.25 * float(vec[prefer].sum())
            if avoid:
                v -= 0.35 * float(vec[avoid].sum())
        if c.focus:                                   # "Foco": strong enough to lead the cut
            v += 0.45 * c.focus
        if new_shots and previous and _overlaps(c, previous):
            v -= 0.35
        if bias:
            v += 0.12 * (bias.get(c.movement, 1.0) - 1.0)
        if seed:
            h = hashlib.sha1(f"{seed}|{c.path}|{c.start:.1f}".encode()).digest()
            v += 0.10 * h[0] / 255.0
        return v
    return bonus


def _cast_requested(chosen: list, candidates: list, targets, bonus, log_fn) -> None:
    """Make sure a shot matching each requested opener/closer is cast, swapping
    out the weakest pick that is not itself requested."""
    for target in targets:
        if not target or any(_wants(c, target) for c in chosen):
            continue
        options = [c for c in candidates if _wants(c, target) and c not in chosen]
        if not options:
            log_fn(f"ℹ️ Nenhum trecho do tipo '{target}' utilizável para abrir/fechar")
            continue
        best = max(options, key=lambda c: c.base + bonus(c))
        keep = [c for c in chosen if not any(_wants(c, t) for t in targets if t)]
        if not keep:
            continue
        weakest = min(keep, key=lambda c: c.base + bonus(c))
        chosen[chosen.index(weakest)] = best


def direct(candidates: list, *, target: float, style: Style, weights, cfg, music=None,
           title: str = "", vertical: bool = False, adj=None, previous=(),
           log_fn=print) -> Plan:
    """``adj``: user/AI adjustments (drone.feedback.Adjustments); ``adj.order``
    (candidate uids) casts and orders the shots directly — the AI director.
    ``previous``: (path, start, end) of the last version's shots."""
    style = adjusted_style(style, adj)
    adj = with_style_defaults(style, adj)
    strict = strict_order(style)
    per_clip = style.max_per_clip or cfg.max_shots_per_clip
    unit = music.unit() if music is not None else 0.0
    slots = plan_slots(target, style, unit)
    vertical_bonus = make_bonus(vertical=vertical, adj=adj, previous=previous, style=style)

    forced = []
    if adj is not None and adj.order:
        by_id = {c.uid: c for c in candidates if c.uid}
        forced = [by_id[u] for u in adj.order if u in by_id]
    if forced:
        chosen = forced[:len(slots)]
    else:
        chosen = shot_selection.select(candidates, len(slots), w_variety=weights.variety,
                                       duplicate_threshold=cfg.duplicate_threshold,
                                       max_per_clip=per_clip,
                                       extra_bonus=vertical_bonus, log_fn=log_fn)
    if not chosen:
        raise RuntimeError("nenhum trecho utilizável encontrado (tudo tremido, escuro ou desfocado?)")
    if not forced and adj is not None:
        _cast_requested(chosen, candidates, (adj.opener, adj.closer), vertical_bonus, log_fn)
    if len(chosen) < len(slots):
        # Not enough material: stretch the slots we can fill.
        scale = len(slots) / len(chosen)
        slots = plan_slots(target / scale, style, unit) if len(chosen) > 1 else slots[:1]
        slots = slots[:len(chosen)]
    if forced:
        ordered = chosen[:len(slots)]
    elif strict:
        ordered = sorted(chosen, key=_rec_key)[:len(slots)]
    else:
        ordered = _order(chosen, adj, style.chronological)[:len(slots)]
    if style.adaptive and unit:
        adapt_slots(slots[:len(ordered)], ordered, unit, style)

    plan = Plan(unit=unit, music=music)
    for i, (c, slot) in enumerate(zip(ordered, slots)):
        want = slot.advance + slot.transition_duration
        s, e = _fit_window(c, want)
        got = e - s
        if got + 1e-6 < want:                       # clip shorter than the slot
            if slot.transition_duration and got > slot.transition_duration * 2:
                slot.advance = got - slot.transition_duration
            else:
                slot.transition, slot.transition_duration = "cut", 0.0
                slot.advance = got
            if unit:
                k = max(1, math.floor(slot.advance / unit + 1e-6))
                slot.advance = k * unit
                e = s + slot.advance + slot.transition_duration
        plan.shots.append(PlannedShot(candidate=c, start=s, end=round(e, 3), slot=slot,
                                      text=title if i == 0 and title else "",
                                      crop_x=c.sal_x))
    _add_shots(plan, candidates, target, unit or 0.5, style, weights, cfg, vertical_bonus, log_fn,
               per_clip=per_clip)
    _fill_deficit(plan, target, unit or 0.5, style)
    if strict and not forced:
        _recording_order(plan, title)
    plan.total = sum(p.slot.advance for p in plan.shots)
    return plan


def _recording_order(plan: Plan, title: str) -> None:
    """Put every shot (extras included) back in recording order. Each shot keeps
    its own length; the roles and the title follow the positions."""
    if len(plan.shots) < 2:
        return
    roles = [p.slot.role for p in plan.shots]
    plan.shots.sort(key=lambda p: (p.candidate.order, p.start))
    for p, role in zip(plan.shots, roles):
        p.slot.role = role
        p.text = ""
    if title:
        plan.shots[0].text = title


def _add_shots(plan: Plan, candidates: list, target: float, unit: float, style: Style,
               weights, cfg, bonus, log_fn, per_clip: int = 0) -> None:
    """When the chosen clips were too short for their slots, cast extra shots
    (inserted before the closing shot) rather than deliver a short film."""
    body_k = _beats_for(unit, style.shot_min, style.shot_max)
    min_adv = max(unit, math.floor(style.shot_min * 0.75 / unit) * unit)
    while True:
        deficit = target - sum(p.slot.advance for p in plan.shots)
        if deficit < min_adv - 1e-6:
            return
        used = [p.candidate for p in plan.shots]
        pick = shot_selection.select(candidates, 1, w_variety=weights.variety,
                                     duplicate_threshold=cfg.duplicate_threshold,
                                     max_per_clip=per_clip or cfg.max_shots_per_clip,
                                     extra_bonus=bonus, already=used, log_fn=lambda *_: None)
        if not pick:
            return
        c = pick[0]
        k = max(1, math.floor(min(deficit, body_k * unit, c.scene_hi - c.scene_lo) / unit + 1e-6))
        slot = Slot(advance=k * unit, role="body")
        s, e = _fit_window(c, slot.advance)
        if e - s < slot.advance - 1e-6:
            k = max(1, math.floor((e - s) / unit + 1e-6))
            slot.advance = k * unit
            e = s + slot.advance
        if slot.advance < unit - 1e-6:
            return
        at = max(1, len(plan.shots) - 1)
        plan.shots.insert(at, PlannedShot(candidate=c, start=s, end=round(e, 3), slot=slot,
                                          crop_x=c.sal_x))
        log_fn(f"ℹ️ Tomada extra para completar a duração: {c.describe()}")


def _fill_deficit(plan: Plan, target: float, unit: float, style: Style) -> None:
    """Lengthen shots (in whole beats) until the reel reaches its target.

    Happens when there is less distinct material than slots — short clips, or
    many rejected as shaky. Body shots grow first, then opener and closer, each
    within its clip's usable span and never past 1.6× the style's longest shot.
    """
    deficit = target - sum(p.slot.advance for p in plan.shots)
    if deficit < unit - 1e-6 or not plan.shots:
        return
    cap = style.shot_max * 1.6
    order = [p for p in plan.shots if p.slot.role == "body"] + \
            [p for p in plan.shots if p.slot.role != "body"]
    grew = True
    while deficit >= unit - 1e-6 and grew:
        grew = False
        for p in order:
            if deficit < unit - 1e-6:
                break
            c = p.candidate
            if p.slot.advance + unit > cap:
                continue
            if p.end + unit <= c.scene_hi + 1e-6:
                p.end = round(p.end + unit, 3)
            elif p.start - unit >= c.scene_lo - 1e-6:
                p.start = round(p.start - unit, 3)
            else:
                continue
            p.slot.advance += unit
            deficit -= unit
            grew = True


def to_edl(plan: Plan, *, title: str, width: int, height: int, fps: int, crf: int,
           music_path: str = "", fill: str = "pad"):
    from modules.media.edl import Cut, Edl
    cuts = []
    for p in plan.shots:
        cuts.append(Cut(source=p.candidate.path, start=p.start, end=p.end,
                        transition=p.slot.transition,
                        transition_duration=p.slot.transition_duration or 0.5,
                        easing="ease_in_out" if p.slot.transition != "cut" else "linear",
                        label=f"{p.candidate.category}/{p.candidate.movement}",
                        text=p.text))
    if cuts:
        cuts[-1].transition = "cut"
    return Edl(title=title or "Drone", cuts=cuts, music=music_path, music_mode="replace",
               music_volume=1.0, width=width, height=height, fps=fps, crf=crf, fill=fill)
