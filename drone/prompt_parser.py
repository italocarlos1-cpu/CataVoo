# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read the optional instruction text with keywords — no LLM, no network.

Example: "Crie um vídeo cinematográfico de Pirenópolis com 90 segundos,
priorizando cachoeiras, serras e movimentos suaves, com música instrumental
tranquila." → style=cinematografico, duration=90, title="Pirenópolis",
priorities=[waterfall, mountains], smooth=True, music_tags=[instrumental, tranquila].

Anything the text does not mention stays as chosen in the interface.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from drone.landscape import KEYWORDS


@dataclass
class ParsedPrompt:
    style: str = ""
    duration: float = 0.0
    vertical_duration: float = 0.0
    title: str = ""
    priorities: list = field(default_factory=list)
    music_tags: list = field(default_factory=list)
    horizontal: bool | None = None
    vertical: bool | None = None
    smooth: bool = False
    dynamic: bool = False
    notes: list = field(default_factory=list)


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower())
                   if unicodedata.category(c) != "Mn")


_STYLE_WORDS = [
    ("automatico", ("estilo automatic", "automatico", "decida o estilo", "escolha o estilo", "voce escolhe")),
    ("redes_sociais", ("redes sociais", "reels", "tiktok", "shorts", "story", "stories")),
    ("trailer_epico", ("trailer", "epico", "grandioso")),
    ("aventura_fpv", ("fpv", "aventura", "radical", "adrenalina")),
    ("minimalista", ("minimalista", "minimal")),
    ("diario_viagem", ("diario", "cronologic", "ordem em que filmei", "ordem das filmagens")),
    ("por_do_sol", ("estilo por do sol", "golden hour", "hora dourada")),
    ("cidade_historica", ("arquitetura", "estilo cidade historica", "centro historico")),
    ("viagem_dinamica", ("dinamic", "rapido", "agitad", "energetic", "ritmo acelerado")),
    ("contemplativo", ("contemplativ", "relaxant", "calmo", "lento", "tranquilo", "sereno")),
    ("cinematografico", ("cinematograf", "cinematic", "filme")),
]
_MUSIC_WORDS = [
    ("instrumental", ("instrumental",)),
    ("cinematografica", ("musica cinematograf", "trilha", "orquestr")),
    ("tranquila", ("tranquila", "calma", "suave", "relaxante", "ambiente")),
    ("eletronica", ("eletronic", "electronic")),
    ("indie", ("indie", "alternativ")),
    ("animada", ("animada", "agitada", "energetica")),
]
_NUM_WORDS = {"trinta": 30, "quarenta e cinco": 45, "sessenta": 60, "noventa": 90,
              "um minuto": 60, "dois minutos": 120, "meio minuto": 30}


def _duration(f: str) -> float:
    m = re.search(r"(\d+)\s*:\s*(\d{2})", f)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(min|minuto|minutos)\b(?:\s*e\s*(\d+)\s*(s|seg|segundos))?", f)
    if m:
        v = float(m.group(1).replace(",", ".")) * 60
        return v + (float(m.group(3)) if m.group(3) else 0)
    m = re.search(r"(\d+)\s*(s|seg|segs|segundo|segundos)\b", f)
    if m:
        return float(m.group(1))
    for w, v in _NUM_WORDS.items():
        if w in f:
            return float(v)
    return 0.0


def parse(text: str) -> ParsedPrompt:
    p = ParsedPrompt()
    if not text or not text.strip():
        return p
    f = " " + _fold(text) + " "

    for key, words in _STYLE_WORDS:
        if any(w in f for w in words):
            p.style = key
            break
    p.duration = _duration(f)

    music_part = f[f.find("music"):] if "music" in f else ""
    for key, words in _MUSIC_WORDS:
        if any(w in music_part for w in words):
            p.music_tags.append(key)

    for cat, words in KEYWORDS.items():
        if any(_fold(w) in f for w in words):
            p.priorities.append(cat)

    if re.search(r"moviment\w*\s+(suave|lent|fluid|cinematograf)", f) or "suavidade" in f:
        p.smooth = True
    if re.search(r"(cortes?|moviment\w*)\s+(rapid|dinamic)", f):
        p.dynamic = True

    vert = any(w in f for w in ("vertical", "9:16", "reels", "instagram", "tiktok", "shorts", "stories"))
    hori = any(w in f for w in ("horizontal", "16:9", "youtube", " 4k", "paisagem 16"))
    if "ambos" in f or (vert and hori):
        p.horizontal = p.vertical = True
    elif vert:
        p.vertical, p.horizontal = True, False
    elif hori:
        p.horizontal, p.vertical = True, False

    # Title: a capitalised place name after "de"/"em" in the original text.
    m = re.search(r"\b(?:de|em|sobre)\s+((?:[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ'-]+)"
                  r"(?:\s+(?:d[aeo]s?\s+)?[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ'-]+)*)", text)
    if m:
        p.title = m.group(1).strip()
    m = re.search(r"t[ií]tulo\s*[:=]?\s*[\"“']([^\"”']+)[\"”']", text, re.I)
    if m:
        p.title = m.group(1).strip()
    return p
