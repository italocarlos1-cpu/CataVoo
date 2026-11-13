# SPDX-License-Identifier: AGPL-3.0-or-later
"""Optional AI assistant — OFF by default, never required.

The editor works completely without it. When the user switches it on, a
language model can help in two places:

1. **Read instructions/feedback** ("quero começar pela cachoeira e um final
   tranquilo") → the same :class:`drone.feedback.Adjustments` the keyword
   reader produces.
2. **Direct**: choose and order the shots from a *text description* of the
   best candidates (file, time, landscape, camera move, scores). The rhythm
   (shot lengths locked to the music) stays with the rule-based director.

What is sent: only text — the instructions and a table of candidate shots.
Never frames, video, audio or file contents. File names are sent as given.

Backends (one HTTP client, no extra packages):

=============  =====================================  =============================
provider       where                                  protocol
=============  =====================================  =============================
ollama         your GPU (e.g. qwen3:8b on 8 GB VRAM)  OpenAI-compatible /v1
lmstudio       your GPU, LM Studio's server           OpenAI-compatible /v1
compatible     llama.cpp server, Jan, vLLM, …         OpenAI-compatible /v1
openai         OpenAI API (paid, API key)             /v1/chat/completions
anthropic      Claude API (paid, API key)             /v1/messages
=============  =====================================  =============================

Every answer is validated; anything malformed, unknown or out of range is
dropped, and if nothing usable remains the rule-based result is used and the
log says why. A failing or absent AI can never break a render.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from drone.config import STYLES
from drone.feedback import VALID_TARGETS, Adjustments
from drone.landscape import CAT_KEYS, LABEL_PT
from drone.motion_analysis import MOVEMENTS


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    base_url: str
    default_model: str
    key_env: str            # environment variable holding the API key ("" = none)
    kind: str               # "openai" | "anthropic"
    local: bool


PROVIDERS = {
    "ollama": Provider("ollama", "Ollama (local, sua GPU)", "http://localhost:11434/v1",
                       "qwen3:8b", "", "openai", True),
    "lmstudio": Provider("lmstudio", "LM Studio (local, sua GPU)", "http://localhost:1234/v1",
                         "", "", "openai", True),
    "compatible": Provider("compatible", "Outro servidor compatível (OpenAI API)",
                           "http://localhost:8080/v1", "", "", "openai", True),
    "openai": Provider("openai", "OpenAI / ChatGPT (API paga)", "https://api.openai.com/v1",
                       "gpt-5-mini", "OPENAI_API_KEY", "openai", False),
    "anthropic": Provider("anthropic", "Anthropic / Claude (API paga)", "https://api.anthropic.com/v1",
                          "claude-haiku-5-5", "ANTHROPIC_API_KEY", "anthropic", False),
}


class AIError(RuntimeError):
    pass


# --------------------------------------------------------------------- client
class AIClient:
    def __init__(self, settings, log_fn=print):
        self.s = settings
        self.p = PROVIDERS.get(settings.provider, PROVIDERS["ollama"])
        self.base = (settings.base_url or self.p.base_url).rstrip("/")
        self.model = settings.model or self.p.default_model
        self.log_fn = log_fn

    @property
    def api_key(self) -> str:
        if self.s.api_key:
            return self.s.api_key
        return os.environ.get(self.p.key_env, "") if self.p.key_env else ""

    def describe(self) -> str:
        where = "local" if self.p.local else "nuvem"
        return f"{self.p.label} — modelo {self.model or '(automático)'} [{where}]"

    def _post(self, url: str, payload: dict, headers: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=self.s.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code in (401, 403):
                raise AIError(f"chave de API recusada ({e.code}). Confira a chave.") from e
            if e.code == 404 and self.p.local:
                raise AIError(f"modelo '{self.model}' não encontrado no servidor local "
                              f"(no Ollama: ollama pull {self.model})") from e
            if e.code == 429:
                raise AIError("limite de uso/crédito da API atingido (429)") from e
            raise AIError(f"erro {e.code} do servidor: {body}") from e
        except urllib.error.URLError as e:
            if self.p.local:
                raise AIError(f"não consegui conectar em {self.base} — o "
                              f"{self.p.label.split(' (')[0]} está aberto?") from e
            raise AIError(f"sem conexão com {self.base}: {e.reason}") from e
        except TimeoutError as e:
            raise AIError(f"o modelo demorou mais de {self.s.timeout:.0f}s para responder") from e

    def _auto_model(self) -> str:
        """LM Studio / compatible servers: use the model that is loaded."""
        try:
            req = urllib.request.Request(self.base + "/models")
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
            models = [m.get("id") for m in data.get("data", []) if m.get("id")]
            chat = [m for m in models if "embed" not in m.lower()]
            return (chat or models or [""])[0]
        except Exception:
            return ""

    def chat(self, system: str, user: str, max_tokens: int = 2000) -> str:
        if not self.model and self.p.kind == "openai":
            self.model = self._auto_model()
            if not self.model:
                raise AIError("nenhum modelo carregado no servidor local")
        if self.p.kind == "anthropic":
            if not self.api_key:
                raise AIError(f"falta a chave da API (campo 'Chave' ou variável {self.p.key_env})")
            out = self._post(self.base + "/messages",
                             {"model": self.model, "max_tokens": max_tokens, "system": system,
                              "messages": [{"role": "user", "content": user}]},
                             {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
            parts = [b.get("text", "") for b in out.get("content", []) if b.get("type") == "text"]
            return "".join(parts)
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif not self.p.local:
            raise AIError(f"falta a chave da API (campo 'Chave' ou variável {self.p.key_env})")
        payload = {"model": self.model,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}]}
        if self.p.local:
            # Hosted reasoning models reject custom temperatures/limits; local ones like them.
            payload.update({"temperature": 0.2, "max_tokens": max_tokens})
        out = self._post(self.base + "/chat/completions", payload, headers)
        try:
            msg = out["choices"][0]["message"]
        except (KeyError, IndexError) as e:
            raise AIError(f"resposta inesperada: {str(out)[:200]}") from e
        return msg.get("content") or ""

    def ping(self) -> str:
        t = time.time()
        reply = self.chat("Responda apenas com a palavra OK.", "teste", max_tokens=50)
        return f"{self.describe()} respondeu em {time.time() - t:.1f}s: {strip_thinking(reply)[:40]!r}"


# --------------------------------------------------------------- parsing help
def strip_thinking(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def extract_json(text: str) -> dict:
    """The first JSON object in a model reply (tolerates ``` fences, prose, <think>)."""
    t = strip_thinking(text)
    t = re.sub(r"```(?:json)?", "", t)
    start = t.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(t)):
            if t[i] == "{":
                depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(t[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
        start = t.find("{", start + 1)
    raise AIError("a resposta não continha um JSON válido")


def _num(v, lo, hi, default=0.0):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, x))


# ------------------------------------------------------------- task: interpret
_INTERPRET_SYSTEM = """Você configura um editor automático de vídeos de drone.
Leia o pedido do usuário e responda SOMENTE com um objeto JSON, sem texto fora dele.
Campos (omita os que o pedido não menciona):
  "style": um de %(styles)s
  "duration": duração do vídeo horizontal em segundos (10 a 600)
  "vertical_duration": duração do vídeo vertical em segundos (10 a 180)
  "duration_scale": 0.5 a 1.5 se pedir vídeo mais curto/longo sem número
  "shot_scale": 0.5 a 1.6 (menor = cortes mais rápidos, maior = tomadas mais longas)
  "prefer": lista de paisagens desejadas, valores de %(cats)s
  "avoid_categories": lista de paisagens a evitar, mesmos valores
  "opener": paisagem ou movimento para abrir o vídeo, valores de %(cats)s ou %(moves)s
  "closer": idem para encerrar
  "transitions": "cut" (cortes secos) ou "crossfade" (transições suaves)
  "new_shots": true se pedir outras tomadas / menos repetição
  "smooth": true se pedir movimentos suaves / sem tremer
  "title": título a escrever na tela, se o usuário der um
  "music_tags": lista entre ["instrumental","cinematografica","tranquila","eletronica","indie","animada"]
  "notes": lista curta, em português, do que você entendeu
Movimentos: rise_reveal=subida/revelação, orbit=órbita, push_in=aproximação, pull_out=afastamento,
flyover=sobrevoo, pan=panorâmica, hover=parado."""


def interpret(client: AIClient, text: str, *, current: dict) -> Adjustments:
    system = _INTERPRET_SYSTEM % {"styles": json.dumps(sorted(STYLES)),
                                  "cats": json.dumps(CAT_KEYS), "moves": json.dumps(list(MOVEMENTS))}
    user = (f"Configuração atual: {json.dumps(current, ensure_ascii=False)}\n"
            f"Pedido do usuário: {text.strip()}")
    data = extract_json(client.chat(system, user, max_tokens=1500))
    return adjustments_from_json(data)


def adjustments_from_json(d: dict) -> Adjustments:
    """Validate a model's JSON field by field; anything invalid is ignored."""
    a = Adjustments()
    if d.get("style") in STYLES:
        a.style = d["style"]
    if "duration" in d:
        a.duration = _num(d["duration"], 10, 600)
    if "vertical_duration" in d:
        a.vertical_duration = _num(d["vertical_duration"], 10, 180)
    if "duration_scale" in d:
        a.duration_scale = _num(d["duration_scale"], 0.5, 1.5, 1.0)
    if "shot_scale" in d:
        a.shot_scale = _num(d["shot_scale"], 0.5, 1.6, 1.0)
    for k in ("prefer", "avoid_categories"):
        v = d.get(k)
        if isinstance(v, list):
            setattr(a, k, [x for x in v if x in CAT_KEYS])
    for k in ("opener", "closer"):
        if d.get(k) in VALID_TARGETS:
            setattr(a, k, d[k])
    if d.get("transitions") in ("cut", "crossfade"):
        a.transitions = d["transitions"]
    a.new_shots = d.get("new_shots") is True
    if a.new_shots:
        a.variation = 1
    a.smooth = d.get("smooth") is True
    if isinstance(d.get("title"), str) and 0 < len(d["title"].strip()) <= 80:
        a.title = d["title"].strip()
    if isinstance(d.get("music_tags"), list):
        a.music_tags = [x for x in d["music_tags"] if isinstance(x, str)][:4]
    if isinstance(d.get("notes"), list):
        a.notes = [str(x)[:120] for x in d["notes"][:6]]
    return a


# --------------------------------------------------------------- task: direct
_DIRECT_SYSTEM = """Você é o montador de um vídeo de drone. Recebe uma tabela de trechos
candidatos (já analisados por algoritmos) e escolhe e ordena os trechos do vídeo final.
Boas práticas: abrir com o trecho mais impactante (revelação, aproximação, órbita), alternar
tipos de paisagem e de movimento, evitar dois trechos seguidos do mesmo arquivo, evitar
trechos parecidos, encerrar com algo calmo (afastamento, subida, pôr do sol) e respeitar o
pedido do usuário. Notas vão de 0 a 1 (maior é melhor).
Responda SOMENTE com JSON: {"order": ["S3","S7",...], "reason": "uma frase em português"}
"order" deve ter exatamente %(n)d ids diferentes, todos da tabela."""


def candidate_table(cands: list) -> str:
    rows = ["id | arquivo | trecho (s) | paisagem | movimento | nota | qualidade | suavidade | interesse | movim."]
    for c in cands:
        name = os.path.basename(c.path)
        p = c.parts
        rows.append(f"{c.uid} | {name} | {c.start:.1f}-{c.end:.1f} | {LABEL_PT.get(c.category, c.category)} | "
                    f"{c.movement} | {c.base:.2f} | {p['quality']:.2f} | {p['smoothness']:.2f} | "
                    f"{p['interest']:.2f} | {p['movement']:.2f}")
    return "\n".join(rows)


def direct(client: AIClient, cands: list, n: int, *, style_label: str, request: str) -> tuple[list, str]:
    """Ordered list of candidate ids chosen by the model, plus its reason."""
    system = _DIRECT_SYSTEM % {"n": n}
    user = (f"Estilo: {style_label}. Quantidade de trechos: {n}.\n"
            f"Pedido do usuário: {request.strip() or '(nenhum)'}\n\n{candidate_table(cands)}")
    data = extract_json(client.chat(system, user, max_tokens=3000))
    return validate_order(data.get("order"), cands, n), str(data.get("reason", ""))[:300]


def validate_order(order, cands: list, n: int) -> list:
    if not isinstance(order, list):
        raise AIError("a resposta não trouxe a lista 'order'")
    known = {c.uid for c in cands}
    out = []
    for x in order:
        x = str(x).strip()
        if x in known and x not in out:
            out.append(x)
    if len(out) < max(2, (n + 1) // 2):
        raise AIError(f"só {len(out)} id(s) válidos de {n} pedidos")
    return out[:n]
