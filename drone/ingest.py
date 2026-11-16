# SPDX-License-Identifier: AGPL-3.0-or-later
"""Find the drone clips in a folder and describe them.

Nothing here modifies or moves the user's files: the folder is read-only input.

DJI specifics handled:

* Filenames ``DJI_0001.MP4`` / ``DJI_20240512123456_0001_D.MP4`` sort in
  recording order by name, but files from several cards or cameras don't, so
  clips are ordered by the container's ``creation_time`` when every file has
  one, else by name.
* DJI writes a sidecar ``.SRT`` subtitle with per-frame telemetry (GPS,
  altitude, ISO, shutter). When present its first/mean coordinates are read so
  the selector can spread the reel across places. Absence is normal.
* Proxy files (``*.LRF``) and the low-res ``_D``/``_S`` duplicates are not
  video we want to cut; LRF is skipped by extension, and the analysis treats
  everything else as a candidate.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from drone.media_io import MediaInfo, probe

VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
_MIN_DURATION = 1.5

_SRT_LAT = re.compile(r"\[latitude\s*:\s*(-?\d+\.\d+)\]|latitude[\s:=]+(-?\d+\.\d+)", re.I)
_SRT_LON = re.compile(r"\[longitude\s*:\s*(-?\d+\.\d+)\]|longitude[\s:=]+(-?\d+\.\d+)", re.I)
_SRT_GPS = re.compile(r"GPS\s*\(\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)")  # older: GPS(lon, lat, alt)
_SRT_ALT = re.compile(r"\[(?:rel_alt|altitude)\s*:\s*(-?\d+\.?\d*)", re.I)
_ISO6709 = re.compile(r"([+-]\d+\.\d+)([+-]\d+\.\d+)")


@dataclass
class DroneClip:
    path: str
    info: MediaInfo
    is_dji: bool = False
    srt_path: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    altitude: Optional[float] = None
    order: int = 0
    warnings: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


def parse_dji_srt(path: str) -> dict:
    """Mean lat/lon and max relative altitude from a DJI telemetry SRT."""
    lats, lons, alts = [], [], []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m1, m2 = _SRT_LAT.search(line), _SRT_LON.search(line)
                if m1 and m2:
                    lats.append(float(m1.group(1) or m1.group(2)))
                    lons.append(float(m2.group(1) or m2.group(2)))
                else:
                    g = _SRT_GPS.search(line)
                    if g:  # GPS(lon, lat, alt) on Mavic 2 era firmware
                        lons.append(float(g.group(1)))
                        lats.append(float(g.group(2)))
                a = _SRT_ALT.search(line)
                if a:
                    alts.append(float(a.group(1)))
    except OSError:
        return {}
    pairs = [(a, b) for a, b in zip(lats, lons) if abs(a) > 1e-6 or abs(b) > 1e-6]
    out = {}
    if pairs:
        out["lat"] = sum(p[0] for p in pairs) / len(pairs)
        out["lon"] = sum(p[1] for p in pairs) / len(pairs)
    if alts:
        out["altitude"] = max(alts)
    return out


def _sidecar(path: str) -> str:
    base, _ = os.path.splitext(path)
    for ext in (".SRT", ".srt"):
        if os.path.exists(base + ext):
            return base + ext
    return ""


# Outputs of editors, not footage: VideoHighlighter writes "<video>_highlight.mp4"
# next to the original, and those copies would otherwise be analysed as new
# clips and their scenes used twice.
_DERIVED = re.compile(r"(_highlights?|_highlight_\d+|_edit(ado)?|_catavoo\w*)$", re.IGNORECASE)


def is_derived(path: str) -> bool:
    """True for a video another editor made from the footage (not raw material)."""
    return bool(_DERIVED.search(os.path.splitext(os.path.basename(path))[0]))


def scan_folder(folder: str, *, recursive: bool = False, log_fn=print,
                progress_fn=None) -> list[DroneClip]:
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"pasta não encontrada: {folder}")
    paths: list[str] = []
    if recursive:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.lower().startswith((".", "_catavoo"))]
            paths += [os.path.join(root, f) for f in files]
    else:
        paths = [os.path.join(folder, f) for f in os.listdir(folder)]
    paths = sorted(p for p in paths if os.path.isfile(p)
                   and os.path.splitext(p)[1].lower() in VIDEO_EXTS
                   and not os.path.basename(p).startswith("."))
    derived = [p for p in paths if is_derived(p)]
    if derived:
        paths = [p for p in paths if p not in derived]
        log_fn(f"ℹ️ {len(derived)} vídeo(s) já editado(s) por outro programa ignorado(s) "
               f"(ex.: {os.path.basename(derived[0])}); só os originais entram na análise")

    clips: list[DroneClip] = []
    for i, p in enumerate(paths):
        if progress_fn:
            progress_fn(i, len(paths), f"Lendo metadados: {os.path.basename(p)}")
        info = probe(p)
        if info.duration < _MIN_DURATION or not info.width:
            log_fn(f"⚠️ Ignorado (curto ou ilegível): {os.path.basename(p)}")
            continue
        name = os.path.basename(p).upper()
        is_dji = name.startswith("DJI") or "DJI" in (info.make + info.model).upper() \
            or "dji" in str(info.tags.get("encoder", "")).lower()
        clip = DroneClip(path=p, info=info, is_dji=is_dji)
        srt = _sidecar(p)
        if srt:
            clip.srt_path = srt
            tele = parse_dji_srt(srt)
            clip.lat, clip.lon = tele.get("lat"), tele.get("lon")
            clip.altitude = tele.get("altitude")
        if clip.lat is None and info.location:
            m = _ISO6709.search(info.location)
            if m:
                clip.lat, clip.lon = float(m.group(1)), float(m.group(2))
        if info.bit_depth >= 10:
            clip.warnings.append("10-bit (possível D-Log/HLG): cores podem parecer lavadas")
        clips.append(clip)

    if clips and all(c.info.creation_time for c in clips):
        clips.sort(key=lambda c: (c.info.creation_time, c.name))
    for i, c in enumerate(clips):
        c.order = i
    log_fn(f"📁 {len(clips)} vídeo(s) de drone encontrados em {folder}")
    return clips
