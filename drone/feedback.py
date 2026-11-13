# SPDX-License-Identifier: AGPL-3.0-or-later
""""Refazer com ajustes" without any AI: feedback text → concrete adjustments.

The user watches a result and writes what to change, e.g.

    "mais cachoeira, menos cidade, cortes mais rápidos, começar com uma
     revelação e trocar as tomadas"

and this turns it into an :class:`Adjustments` that the director applies on
the next run (which reuses the analysis cache, so it costs only the render).

The optional AI assistant (:mod:`drone.ai_assist`) produces the *same*
``Adjustments`` object, so the rest of the system never needs to know which of
the two read the text — and the keyword reader is always the fallback.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field

from drone.landscape import CAT_KEYS, KEYWORDS
from drone.motion_analysis import MOVEMENTS

MOVE_WORDS = {
    "rise_reveal": ("revelacao", "revela", "subindo", "subida", "reveal"),
    "orbit": ("orbita", "girando", "em volta", "ao redor", "orbit"),
    "push_in": ("aproximacao", "aproximando", "chegando perto", "push in"),
    "pull_out": ("afastamento", "afastando", "se afastando", "pull out"),
    "flyover": ("sobrevoo", "sobrevoando", "voando por cima", "flyover"),
    "pan": ("panoramica", "pan "),
    "hover": ("parado", "estatic", "pairando"),
}


@dataclass
class Adjustments:
    style: str = ""
    duration: float = 0.0               # absolute horizontal duration (s)
    vertical_duration: float = 0.0
    duration_scale: float = 1.0         # "vídeo mais curto/longo"
    shot_scale: float = 1.0             # "cortes mais rápidos/lentos"
    prefer: list = field(default_factory=list)          # landscape categories
    avoid_categories: list = field(default_factory=list)
    opener: str = ""                    # category or movement wanted first
    closer: str = ""
    transitions: str = ""               # "" (style) | "cut" | "crossfade"
    new_shots: bool = False             # avoid the shots of the previous version
    variation: int = 0                  # >0: deterministic alternative selection
    smooth: bool = False
    title: str = ""
    music_tags: list = field(default_factory=list)
    order: list = field(default_factory=list)   # candidate ids (AI director only)
    notes: list = field(default_factory=list)   # human-readable "what I understood"

    def is_empty(self) -> bool:
        return self == Adjustments()

    def merge(self, other: "Adjustments") -> "Adjustments":
        """``other`` wins where it says something; lists are united."""
        base, new = asdict(self), asdict(other)
        default = asdict(Adjustments())
        for k, v in new.items():
            if isinstance(v, list):
                base[k] = list(dict.fromkeys(base[k] + v)) if k != "order" else (v or base[k])
            elif v != default[k]:
                base[k] = v
        return Adjustments(**base)


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower())
                   if unicodedata.category(c) != "Mn")


def _target_after(f: str, pos: int) -> str:
    """Category or movement mentioned within ~40 characters after ``pos``."""
    window = f[pos:pos + 45]
    best, best_at = "", 10 ** 9
    for cat, words in KEYWORDS.items():
        for w in words:
            i = window.find(_fold(w).strip())
            if 0 <= i < best_at:
                best, best_at = cat, i
    for mv, words in MOVE_WORDS.items():
        for w in words:
            i = window.find(w)
            if 0 <= i < best_at:
                best, best_at = mv, i
    return best


def parse_feedback(text: str) -> Adjustments:
    adj = Adjustments()
    if not text or not text.strip():
        return adj
    f = " " + _fold(text) + " "

    # Pace of the cuts (checked before "mais curto/longo" about the video).
    if re.search(r"(cortes?|tomadas?|cenas?|trechos?)\s+(mais\s+)?(rapid|curt|dinamic|agil)", f) \
            or re.search(r"mais (rapido|dinamico|agil|acelerado)|ritmo mais", f):
        adj.shot_scale = 0.75
        adj.notes.append("cortes mais rápidos")
    elif re.search(r"(cortes?|tomadas?|cenas?|trechos?)\s+(mais\s+)?(lent|long|demorad|calm)", f) \
            or re.search(r"mais (lento|calmo|contemplativo|devagar)", f):
        adj.shot_scale = 1.3
        adj.notes.append("cortes mais longos")

    if re.search(r"(video|filme|montagem)\s+(mais\s+)?(curt|menor)|encurt|reduz", f):
        adj.duration_scale = 0.75
        adj.notes.append("vídeo mais curto")
    elif re.search(r"(video|filme|montagem)\s+(mais\s+)?(long|maior)|aument", f):
        adj.duration_scale = 1.25
        adj.notes.append("vídeo mais longo")

    # Opening / closing shot. A landscape named there is about *where it goes*,
    # not a request for more of it, so those spans are skipped below.
    spans = []
    m = re.search(r"\b(comec\w*|abr[ia]\w*|abertura|inicio|primeira)", f)
    if m:
        adj.opener = _target_after(f, m.end())
        spans.append((m.end(), m.end() + 45))
    m = re.search(r"\b(termin\w*|fech\w*|final|encerr\w*|ultima)", f)
    if m:
        adj.closer = _target_after(f, m.end())
        spans.append((m.end(), m.end() + 45))

    # More / less of a landscape type.
    for cat, words in KEYWORDS.items():
        for w in words:
            for m in re.finditer(re.escape(_fold(w).strip()), f):
                if any(a <= m.start() < b for a, b in spans):
                    continue
                before = f[max(0, m.start() - 22):m.start()]
                if re.search(r"(menos|sem|tirar?|tira|evitar?|nada de)\s+(\w+\s+)?$", before):
                    if cat not in adj.avoid_categories:
                        adj.avoid_categories.append(cat)
                elif cat not in adj.prefer:
                    adj.prefer.append(cat)
    adj.prefer = [c for c in adj.prefer if c not in adj.avoid_categories]


    if re.search(r"outras tomadas|trocar|troque|diferente|variar|varie|outra versao|"
                 r"nova versao|repetitiv|mesmas tomadas", f):
        adj.new_shots = True
        adj.variation = 1
        adj.notes.append("trocar as tomadas")
    if re.search(r"sem transic|corte seco|cortes secos|sem fade", f):
        adj.transitions = "cut"
    elif re.search(r"transic\w* suave|dissolv|fade|mais transic", f):
        adj.transitions = "crossfade"
    if re.search(r"sem trem|estavel|estaveis|mais suave|movimentos suaves", f):
        adj.smooth = True

    for label, x in (("abrir com", adj.opener), ("fechar com", adj.closer)):
        if x:
            adj.notes.append(f"{label} {x}")
    if adj.prefer:
        adj.notes.append("mais " + ", ".join(adj.prefer))
    if adj.avoid_categories:
        adj.notes.append("menos " + ", ".join(adj.avoid_categories))
    return adj


VALID_TARGETS = set(CAT_KEYS) | set(MOVEMENTS)
