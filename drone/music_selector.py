# SPDX-License-Identifier: AGPL-3.0-or-later
"""Choose a track from the user's local music library and prepare it.

Beat detection is VideoHighlighter's own (:func:`modules.audio.music_analysis.analyze_music`,
numpy backend, librosa when installed); the result is cached so a library is
analysed once. Tracks are matched to the style by words in their file or folder
names — organise the library as e.g. ``Musicas/instrumental/...`` or
``Musicas/eletronica ambiente/...`` — then by tempo and length.

Only files the user put in the library are ever used; nothing is downloaded.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

from drone.media_io import _run, ffmpeg_bin  # _run hides console windows on Windows

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}

# Words a user may type → words to look for in file/folder names
MUSIC_WORDS = {
    "instrumental": ("instrument", "cinemat", "piano", "orquest", "score", "trilha"),
    "cinematografica": ("cinemat", "epic", "épic", "orquest", "trilha", "score"),
    "tranquila": ("tranquil", "calm", "relax", "ambient", "chill", "piano", "lofi", "lo-fi"),
    "eletronica": ("eletr", "electr", "ambient", "synth", "chill"),
    "indie": ("indie", "alternat", "folk", "acoust", "acúst"),
    "animada": ("upbeat", "energ", "pop", "rock", "dance", "anim"),
}


@dataclass
class MusicChoice:
    path: str
    duration: float
    bpm: float
    beat_interval: float
    start: float                         # offset into the file the reel starts at
    beats: list = field(default_factory=list)       # relative to ``start``
    downbeats: list = field(default_factory=list)
    reason: str = ""

    def unit(self) -> float:
        return self.beat_interval if self.beat_interval > 0 else 0.0


def scan_library(folder: str) -> list[str]:
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    for root, _, files in os.walk(folder):
        for f in files:
            if os.path.splitext(f)[1].lower() in AUDIO_EXTS:
                out.append(os.path.join(root, f))
    return sorted(out)


def analyse(path: str, cache, log_fn=print) -> dict:
    cached = cache.get_music(path) if cache is not None else None
    if cached:
        return cached
    from modules.audio.music_analysis import analyze_music
    a = analyze_music(path, backend="auto", log_fn=lambda *_: None)
    data = {"duration": a.duration, "bpm": a.bpm, "beat_interval": a.beat_interval,
            "beats": [round(b, 4) for b in a.beats],
            "downbeats": [round(b, 4) for b in a.downbeats],
            "sections": [[s.start, s.end, s.energy, s.label] for s in a.sections],
            "backend": a.backend}
    if cache is not None:
        cache.put_music(path, data)
    log_fn(f"🎵 {os.path.basename(path)}: {a.bpm:.0f} BPM, {a.duration:.0f}s")
    return data


def _tag_score(path: str, words: tuple) -> int:
    low = path.lower()
    return sum(1 for w in words if w in low)


def choose(library: str | list, *, target: float, bpm_range=(60, 140), tags=(),
           cache=None, explicit: str = "", seed: str = "", log_fn=print) -> MusicChoice | None:
    """Best track for the style, or None when the library is empty."""
    paths = [explicit] if explicit else (library if isinstance(library, list) else scan_library(library))
    paths = [p for p in paths if p and os.path.exists(p)]
    if not paths:
        return None
    words: tuple = ()
    for t in tags:
        words += MUSIC_WORDS.get(t, (t,))
    best, best_key = None, None
    for p in paths:
        try:
            data = analyse(p, cache, log_fn)
        except Exception as exc:
            log_fn(f"⚠️ Música ignorada ({os.path.basename(p)}): {exc}")
            continue
        bpm = data["bpm"]
        lo, hi = bpm_range
        fits = any(lo <= b <= hi for b in (bpm, bpm * 2, bpm / 2)) if bpm else False
        long_enough = data["duration"] >= target + 1.0
        tie = hashlib.sha1((seed + p).encode()).hexdigest()
        key = (_tag_score(p, words), long_enough, fits, bool(data["beats"]), tie)
        if best_key is None or key > best_key:
            best, best_key = (p, data), key
    if best is None:
        return None
    p, data = best
    start = 0.0
    downs = [d for d in data["downbeats"] if d >= 0.05]
    # Start on a downbeat, but only if the track still covers the reel from there.
    for d in downs[:8]:
        if data["duration"] - d >= target + 0.5:
            start = d
            break
    beats = [round(b - start, 4) for b in data["beats"] if b >= start - 1e-6]
    downbeats = [round(b - start, 4) for b in data["downbeats"] if b >= start - 1e-6]
    reason = (f"palavras-chave={best_key[0]}, duração {'ok' if best_key[1] else 'curta'}, "
              f"andamento {'compatível' if best_key[2] else 'fora da faixa'}")
    return MusicChoice(path=p, duration=data["duration"], bpm=data["bpm"],
                       beat_interval=data["beat_interval"], start=start,
                       beats=beats, downbeats=downbeats, reason=reason)


def prepare(choice: MusicChoice, length: float, out_path: str, *, fade_in: float = 0.15,
            fade_out: float = 4.0) -> str:
    """Trim the track to the film and fade it out over its last seconds, so the
    music *ends* with the film. A track shorter than the film is looped."""
    fade_out = min(fade_out, max(0.5, length * 0.15))
    loop = (choice.duration - choice.start) < length + 0.1
    af = (f"afade=t=in:st=0:d={fade_in:.2f},"
          f"afade=t=out:st={max(0.0, length - fade_out):.3f}:d={fade_out:.2f}")
    cmd = [ffmpeg_bin(), "-y", "-v", "error"] + (["-stream_loop", "-1"] if loop else []) + [
           "-ss", f"{choice.start:.3f}", "-i", choice.path,
           "-t", f"{length:.3f}", "-af", af, "-ac", "2", "-ar", "48000",
           "-c:a", "aac", "-b:a", "256k", out_path]
    r = _run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"falha ao preparar música: {r.stderr[-300:]}")
    return out_path
