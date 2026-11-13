# SPDX-License-Identifier: AGPL-3.0-or-later
"""Split one folder into several films: one per flight, day, place or subfolder.

Modes (``Job.group_by``):

=========  ==================================================================
nada       one film with everything (default)
voo        a new flight starts when more than ``flight_gap`` minutes pass
           between the end of one recording and the start of the next
           (battery swap / landing). Uses the container's creation time,
           else the file's modification time.
dia        one film per calendar day of recording
local      clusters of recordings within ``radius_km`` of each other, from
           DJI ``.SRT`` telemetry or the container's GPS tag; clips without
           GPS join the place they were filmed closest in time to. No GPS at
           all → falls back to flights.
subpasta   one film per subfolder of the chosen folder (scanned recursively)
=========  ==================================================================

``summary=True`` adds a film of the whole trip next to the per-group films.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import datetime

MODES = {
    "nada": "Um vídeo com tudo",
    "voo": "Um vídeo por voo",
    "dia": "Um vídeo por dia",
    "local": "Um vídeo por local (GPS)",
    "subpasta": "Um vídeo por subpasta",
}


@dataclass
class Group:
    key: str
    label: str
    slug: str
    indices: list = field(default_factory=list)   # into the analyses list


def _start_time(a: dict) -> float:
    text = a.get("creation_time") or ""
    if text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    try:
        # mtime is usually when recording stopped
        return os.path.getmtime(a["path"]) - float(a.get("duration", 0))
    except OSError:
        return 0.0


def _km(a, b) -> float:
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def _fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def by_flight(analyses: list, gap_minutes: float = 10.0) -> list[Group]:
    order = sorted(range(len(analyses)), key=lambda i: _start_time(analyses[i]))
    groups: list[Group] = []
    prev_end = None
    days = {datetime.fromtimestamp(_start_time(analyses[i])).date() for i in order}
    for i in order:
        st = _start_time(analyses[i])
        if prev_end is None or st - prev_end > gap_minutes * 60:
            n = len(groups) + 1
            when = datetime.fromtimestamp(st)
            label = f"Voo {n} — {when.strftime('%d/%m %H:%M') if len(days) > 1 else _fmt_time(st)}"
            groups.append(Group(f"voo{n}", label, f"voo{n:02d}"))
        groups[-1].indices.append(i)
        prev_end = max(prev_end or 0, st + float(analyses[i].get("duration", 0)))
    return groups


def by_day(analyses: list) -> list[Group]:
    days: dict = {}
    for i, a in enumerate(analyses):
        d = datetime.fromtimestamp(_start_time(a)).date()
        days.setdefault(d, []).append(i)
    out = []
    for n, d in enumerate(sorted(days), 1):
        out.append(Group(f"dia{n}", f"Dia {d.strftime('%d/%m/%Y')}", f"dia_{d.isoformat()}",
                         sorted(days[d], key=lambda i: _start_time(analyses[i]))))
    return out


def by_place(analyses: list, radius_km: float = 1.5, log_fn=print) -> list[Group]:
    with_gps = [i for i, a in enumerate(analyses) if a.get("lat") is not None and a.get("lon") is not None]
    if not with_gps:
        log_fn("ℹ️ Nenhum vídeo tem GPS (SRT ou metadados): separando por voo")
        return by_flight(analyses)
    clusters: list[dict] = []
    for i in sorted(with_gps, key=lambda i: _start_time(analyses[i])):
        pt = (analyses[i]["lat"], analyses[i]["lon"])
        best = None
        for c in clusters:
            d = _km(pt, c["center"])
            if d <= radius_km and (best is None or d < best[0]):
                best = (d, c)
        if best is None:
            clusters.append({"center": pt, "members": [i]})
        else:
            c = best[1]
            c["members"].append(i)
            n = len(c["members"])
            c["center"] = ((c["center"][0] * (n - 1) + pt[0]) / n, (c["center"][1] * (n - 1) + pt[1]) / n)
    for i, a in enumerate(analyses):
        if i in with_gps:
            continue
        st = _start_time(a)
        nearest = min(clusters, key=lambda c: min(abs(_start_time(analyses[j]) - st) for j in c["members"]))
        nearest["members"].append(i)
    out = []
    for n, c in enumerate(clusters, 1):
        lat, lon = c["center"]
        out.append(Group(f"local{n}", f"Local {n} ({lat:.4f}, {lon:.4f})", f"local{n:02d}",
                         sorted(c["members"], key=lambda i: _start_time(analyses[i]))))
    return out


def by_subfolder(analyses: list, root: str) -> list[Group]:
    dirs: dict = {}
    for i, a in enumerate(analyses):
        rel = os.path.relpath(os.path.dirname(a["path"]), root)
        dirs.setdefault(rel, []).append(i)
    out = []
    for n, rel in enumerate(sorted(dirs), 1):
        name = "pasta principal" if rel in (".", "") else rel.replace(os.sep, " / ")
        slug = "principal" if rel in (".", "") else rel.replace(os.sep, "_")
        out.append(Group(f"sub{n}", name, slug, dirs[rel]))
    return out


def make_groups(analyses: list, mode: str, *, root: str = "", summary: bool = False,
                flight_gap: float = 10.0, radius_km: float = 1.5, log_fn=print) -> list[Group]:
    everything = Group("tudo", "Viagem completa", "", list(range(len(analyses))))
    if mode in ("", "nada") or not analyses:
        return [everything]
    if mode == "voo":
        groups = by_flight(analyses, flight_gap)
    elif mode == "dia":
        groups = by_day(analyses)
    elif mode == "local":
        groups = by_place(analyses, radius_km, log_fn)
    elif mode == "subpasta":
        groups = by_subfolder(analyses, root)
    else:
        raise ValueError(f"modo de separação desconhecido: {mode}")
    log_fn(f"🗂️ {len(groups)} grupo(s): " + "; ".join(f"{g.label} ({len(g.indices)} vídeo(s))" for g in groups))
    if len(groups) == 1:
        g = groups[0]
        return [Group("tudo", g.label, "", g.indices)]
    if summary:
        everything.slug = "viagem_completa"
        groups.append(everything)
    return groups
