# SPDX-License-Identifier: AGPL-3.0-or-later
"""The hand-editable cut list behind the "Edição manual" timeline.

The automatic director proposes a :class:`cinematic_director.Plan`; this module
turns it into an :class:`EditPlan` the user can change — trim each shot's in and
out point to the frame, slide it inside its clip, reorder, remove, add or swap
in another candidate, change a join between a dissolve and a hard cut — and
turns it back into a ``Plan`` the normal renderer accepts.

The **ímã** (magnet) keeps cuts on the music: a shot's *advance* (how far it
moves the film on: its length minus the transition into the next shot) is
snapped to whole beats. Arrow keys still move one frame for fine work; "Encaixar
tudo na batida" re-snaps every shot at the end.

The total length is allowed to change with the edits (user decision); it is
always recomputed and shown.

Pure logic, no Qt: everything here is unit-tested.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from types import SimpleNamespace

MIN_SHOT = 0.5          # seconds; shorter than this is a flash, not a shot


@dataclass
class ClipInfo:
    path: str
    name: str
    duration: float
    fps: float
    content_hash: str = ""
    order: int = 0


@dataclass
class EditShot:
    uid: str
    path: str
    start: float
    end: float
    transition: str = "cut"            # into the next shot
    transition_duration: float = 0.0
    crop_x: float = 0.5                # 9:16 crop centre (0..1)
    category: str = ""
    movement: str = ""
    score: float = 0.0
    parts: dict = field(default_factory=dict)

    @property
    def length(self) -> float:
        return self.end - self.start


def candidate_dict(c) -> dict:
    return {"uid": c.uid, "path": c.path, "start": c.start, "end": c.end,
            "scene_lo": c.scene_lo, "scene_hi": c.scene_hi, "category": c.category,
            "movement": c.movement, "score": round(c.base, 4), "crop_x": c.sal_x,
            "parts": {k: round(v, 3) for k, v in c.parts.items()}}


class EditPlan:
    def __init__(self, *, style_key: str, tag: str, shots: list, clips: dict, unit: float,
                 magnet: bool = True, target: float = 0.0, edited: bool = False,
                 candidates: list | None = None, style_label: str = ""):
        self.style_key = style_key
        self.style_label = style_label
        self.tag = tag
        self.shots: list[EditShot] = shots
        self.clips: dict[str, ClipInfo] = clips
        self.unit = unit if unit and unit > 0 else 0.5
        self.magnet = magnet
        self.target = target
        self.edited = edited
        self.candidates: list[dict] = candidates or []

    # ---------------------------------------------------------- creation
    @classmethod
    def from_plan(cls, plan, gp, *, style_key: str, tag: str, target: float = 0.0) -> "EditPlan":
        clips = {}
        for a in gp.analyses:
            clips[a["path"]] = ClipInfo(path=a["path"], name=a.get("name") or os.path.basename(a["path"]),
                                        duration=float(a["duration"]), fps=float(a.get("fps") or 30.0),
                                        content_hash=a.get("content_hash", ""), order=int(a.get("order", 0)))
        shots = []
        for p in plan.shots:
            c = p.candidate
            shots.append(EditShot(uid=c.uid, path=c.path, start=p.start, end=p.end,
                                  transition=p.slot.transition,
                                  transition_duration=p.slot.transition_duration if p.slot.transition != "cut" else 0.0,
                                  crop_x=p.crop_x, category=c.category, movement=c.movement,
                                  score=round(c.base, 4), parts={k: round(v, 3) for k, v in c.parts.items()}))
        if shots:
            shots[-1].transition, shots[-1].transition_duration = "cut", 0.0
        return cls(style_key=style_key, tag=tag, shots=shots, clips=clips, unit=gp.unit,
                   target=target, candidates=[candidate_dict(c) for c in gp.cands],
                   style_label=getattr(gp.style, "label", style_key))

    # ------------------------------------------------------------ queries
    def trans_out(self, i: int) -> float:
        """Transition overlap into the next shot (0 for the last shot)."""
        s = self.shots[i]
        return s.transition_duration if (i < len(self.shots) - 1 and s.transition != "cut") else 0.0

    def advance(self, i: int) -> float:
        return self.shots[i].length - self.trans_out(i)

    @property
    def total(self) -> float:
        return sum(self.advance(i) for i in range(len(self.shots)))

    def frame(self, path: str) -> float:
        fps = self.clips[path].fps if path in self.clips else 30.0
        return 1.0 / (fps if fps > 1 else 30.0)

    def on_beat(self, i: int, tol: float | None = None) -> bool:
        """Within half a frame of a whole number of beats (a cut cannot be finer)."""
        if tol is None:
            tol = 0.5 * self.frame(self.shots[i].path)
        k = self.advance(i) / self.unit
        return abs(k - round(k)) * self.unit <= tol + 1e-9

    def cut_times(self) -> list[float]:
        """Where each cut lands in the finished film."""
        t, out = 0.0, []
        for i in range(len(self.shots)):
            out.append(round(t, 3))
            t += self.advance(i)
        return out

    # -------------------------------------------------------------- edits
    def _q(self, t: float, path: str) -> float:
        f = self.frame(path)
        return round(round(t / f) * f, 4)

    def _bounds(self, s: EditShot) -> tuple[float, float]:
        dur = self.clips[s.path].duration if s.path in self.clips else s.end
        return 0.0, dur

    def _min_len(self, i: int) -> float:
        return max(MIN_SHOT, self.trans_out(i) + 0.2)

    def trim(self, i: int, edge: str, t: float, snap: bool | None = None) -> None:
        """Move the in ('start') or out ('end') point of shot ``i`` to ``t``."""
        s = self.shots[i]
        lo, hi = self._bounds(s)
        snap = self.magnet if snap is None else snap
        tr = self.trans_out(i)
        mn = self._min_len(i)
        if edge == "end":
            t = min(hi, max(s.start + mn, t))
            if snap:
                k = max(1, round((t - s.start - tr) / self.unit))
                while k > 1 and s.start + tr + k * self.unit > hi + 1e-6:
                    k -= 1
                t = s.start + tr + k * self.unit
            s.end = round(min(hi, t), 4) if snap else self._q(t, s.path)
        else:
            t = max(lo, min(s.end - mn, t))
            if snap:
                k = max(1, round((s.end - t - tr) / self.unit))
                while k > 1 and s.end - tr - k * self.unit < lo - 1e-6:
                    k -= 1
                t = s.end - tr - k * self.unit
            s.start = round(max(lo, t), 4) if snap else self._q(t, s.path)
        self.edited = True

    def nudge(self, i: int, edge: str, frames: int = 0, seconds: float = 0.0, beats: int = 0) -> None:
        """Arrow keys: ±1 frame (never snapped), ±1 s, or ±1 beat."""
        s = self.shots[i]
        cur = s.end if edge == "end" else s.start
        if frames:
            # Step to real frame boundaries: from a time between two frames the
            # first press lands on the next frame in that direction.
            f = self.frame(s.path)
            k = cur / f
            target = (math.floor(k + 0.01) + frames) if frames > 0 else (math.ceil(k - 0.01) + frames)
            cur = target * f
        cur += seconds + beats * self.unit
        self.trim(i, edge, cur, snap=False)

    def slide(self, i: int, dt: float) -> None:
        """Move the whole shot inside its clip, keeping its length."""
        s = self.shots[i]
        lo, hi = self._bounds(s)
        length = s.length
        new_start = min(max(lo, s.start + dt), hi - length)
        s.start = self._q(new_start, s.path)
        s.end = round(s.start + length, 4)
        self.edited = True

    def move(self, i: int, j: int) -> None:
        if i == j or not (0 <= i < len(self.shots)) or not (0 <= j < len(self.shots)):
            return
        self.shots.insert(j, self.shots.pop(i))
        self._fix_last()
        self.edited = True

    def reorder(self, order: list[int]) -> None:
        """New sequence from a permutation of the current indices (drag in the strip)."""
        if sorted(order) != list(range(len(self.shots))) or order == list(range(len(self.shots))):
            return
        self.shots = [self.shots[k] for k in order]
        self._fix_last()
        self.edited = True

    def remove(self, i: int) -> None:
        if 0 <= i < len(self.shots) and len(self.shots) > 1:
            self.shots.pop(i)
            self._fix_last()
            self.edited = True

    def _shot_from(self, cand: dict, like: EditShot | None = None, length: float | None = None) -> EditShot:
        if length is None:
            length = (like.length if like else min(cand["end"] - cand["start"], 5.0))
        lo, hi = cand.get("scene_lo", cand["start"]), cand.get("scene_hi", cand["end"])
        length = min(length, hi - lo)
        centre = 0.5 * (cand["start"] + cand["end"])
        start = min(max(lo, centre - length / 2), hi - length)
        return EditShot(uid=cand["uid"], path=cand["path"], start=round(start, 4),
                        end=round(start + length, 4),
                        transition=like.transition if like else "cut",
                        transition_duration=like.transition_duration if like else 0.0,
                        crop_x=cand.get("crop_x", 0.5), category=cand.get("category", ""),
                        movement=cand.get("movement", ""), score=cand.get("score", 0.0),
                        parts=dict(cand.get("parts", {})))

    def insert(self, cand: dict, at: int | None = None) -> int:
        """Add a candidate (a grey block) to the sequence; returns its index."""
        at = len(self.shots) if at is None else max(0, min(at, len(self.shots)))
        ref = self.shots[min(at, len(self.shots) - 1)] if self.shots else None
        shot = self._shot_from(cand, like=ref)
        self.shots.insert(at, shot)
        self._fix_last()
        if self.magnet:
            self.trim(at, "end", shot.end, snap=True)
        self.edited = True
        return at

    def replace(self, i: int, cand: dict) -> None:
        old = self.shots[i]
        self.shots[i] = self._shot_from(cand, like=old)
        self._fix_last()
        self.edited = True

    def set_transition(self, i: int, kind: str, duration: float = 0.6) -> None:
        s = self.shots[i]
        if kind == "cut":
            s.transition, s.transition_duration = "cut", 0.0
        else:
            s.transition, s.transition_duration = kind, min(duration, max(0.1, s.length * 0.4))
        self._fix_last()
        self.edited = True

    def snap_all(self) -> int:
        """Put every cut back on the beat; returns how many shots changed."""
        changed = 0
        for i in range(len(self.shots)):
            if not self.on_beat(i):
                self.trim(i, "end", self.shots[i].end, snap=True)
                changed += 1
        return changed

    def _fix_last(self) -> None:
        if self.shots:
            self.shots[-1].transition, self.shots[-1].transition_duration = "cut", 0.0

    # --------------------------------------------------------- rendering
    def to_plan(self, music=None, title: str = ""):
        """A ``cinematic_director.Plan`` for the normal renderer."""
        from drone.cinematic_director import Plan, PlannedShot, Slot
        plan = Plan(unit=self.unit, music=music)
        n = len(self.shots)
        for i, s in enumerate(self.shots):
            cand = SimpleNamespace(uid=s.uid, path=s.path, category=s.category, movement=s.movement,
                                   base=s.score, parts=dict(s.parts) or {"quality": 0, "smoothness": 0,
                                                                         "interest": 0, "movement": 0},
                                   sal_x=s.crop_x)
            role = "abre" if i == 0 else "fecha" if i == n - 1 else "body"
            tr = self.trans_out(i)
            slot = Slot(advance=self.advance(i), transition=s.transition if tr else "cut",
                        transition_duration=tr, role=role)
            plan.shots.append(PlannedShot(candidate=cand, start=s.start, end=s.end, slot=slot,
                                          text=title if i == 0 and title else "", crop_x=s.crop_x))
        plan.total = self.total
        return plan

    # ------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        return {"style_key": self.style_key, "style_label": self.style_label, "tag": self.tag,
                "unit": self.unit, "magnet": self.magnet, "target": self.target,
                "edited": self.edited, "shots": [asdict(s) for s in self.shots]}

    @classmethod
    def from_dict(cls, d: dict, *, clips: dict, candidates: list | None = None) -> "EditPlan":
        shots = [EditShot(**s) for s in d.get("shots", []) if s.get("path") in clips]
        return cls(style_key=d["style_key"], style_label=d.get("style_label", ""), tag=d["tag"],
                   shots=shots, clips=clips, unit=d.get("unit", 0.5), magnet=d.get("magnet", True),
                   target=d.get("target", 0.0), edited=d.get("edited", False), candidates=candidates)


class EditStore:
    """Manual edits of a folder, kept in ``<saída>/edicao_manual.json`` so they
    survive closing the program and can be reused for later films."""

    def __init__(self, path: str):
        self.path = path
        self.data = {"version": 1, "plans": {}}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict) and isinstance(loaded.get("plans"), dict):
                    self.data = loaded
            except (OSError, json.JSONDecodeError):
                pass

    @staticmethod
    def key(group: str, style: str, tag: str) -> str:
        return f"{group}|{style}|{tag}"

    def get(self, group: str, style: str, tag: str) -> dict | None:
        return self.data["plans"].get(self.key(group, style, tag))

    def put(self, group: str, plan: EditPlan) -> None:
        if plan.edited:
            self.data["plans"][self.key(group, plan.style_key, plan.tag)] = plan.to_dict()

    def drop(self, group: str, style: str, tag: str) -> None:
        self.data["plans"].pop(self.key(group, style, tag), None)

    def edited_styles(self, group: str, tag: str) -> set:
        out = set()
        for k in self.data["plans"]:
            g, st, t = k.split("|")
            if g == group and t == tag:
                out.add(st)
        return out

    def save(self) -> str:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)
        return self.path


def fmt_time(t: float, fps: float = 30.0) -> str:
    """M:SS.ff (frames) — what the timeline shows next to each edge."""
    t = max(0.0, t)
    m = int(t // 60)
    sec = t - 60 * m
    whole = int(math.floor(sec + 1e-9))
    frames = int(round((sec - whole) * fps))
    if frames >= round(fps):
        whole, frames = whole + 1, 0
    return f"{m}:{whole:02d}.{frames:02d}"
