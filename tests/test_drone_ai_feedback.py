# SPDX-License-Identifier: AGPL-3.0-or-later
"""Optional AI assistant and "refazer com ajustes" — no real model needed.

A tiny local HTTP server stands in for Ollama / LM Studio (OpenAI-compatible)
and for the Claude API, so the whole path — request shape, messy replies,
validation, fallback — is exercised offline.
"""
from __future__ import annotations

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests import test_drone_pipeline as base  # noqa: E402  (real cv2 swap + trip fixture)

trip = base.trip
_real_opencv = base._real_opencv

from drone import ai_assist, cinematic_director  # noqa: E402
from drone.config import STYLES, AISettings, DroneConfig  # noqa: E402
from drone.feedback import Adjustments, parse_feedback  # noqa: E402
from drone.visual_scoring import candidates_for_clip  # noqa: E402


# ------------------------------------------------------------- fake servers
class FakeLLM(BaseHTTPRequestHandler):
    seen: list = []
    mode = "good"

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._send({"data": [{"id": "text-embedding-x"}, {"id": "fake-local-8b"}]})
        else:
            self._send({}, 404)

    def _answer(self, system: str, user: str) -> str:
        if FakeLLM.mode == "garbage":
            return "desculpe, não sei"
        if "configura" in system:
            return ('<think>pensando...</think>Claro! ```json\n{"style": "contemplativo", '
                    '"duration": 40, "prefer": ["waterfall", "vulcão"], "opener": "pull_out", '
                    '"shot_scale": 9, "transitions": "dissolve", "notes": ["mais cachoeira"]}\n```')
        if "montador" in system:
            n = int(re.search(r"Quantidade de trechos: (\d+)", user).group(1))
            ids = re.findall(r"^(S\d+) \|", user, flags=re.M)
            order = list(reversed(ids))[:n] + ["S999", ids[0] if ids else "S1"]
            return json.dumps({"order": order, "reason": "ordem invertida de teste"})
        return "OK"

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeLLM.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, data))
        if self.path.endswith("/messages"):
            if self.headers.get("x-api-key") != "sk-ant-test":
                return self._send({"error": "bad key"}, 401)
            text = self._answer(data.get("system", ""), data["messages"][0]["content"])
            return self._send({"content": [{"type": "text", "text": text}]})
        msgs = data["messages"]
        text = self._answer(msgs[0]["content"], msgs[1]["content"])
        self._send({"choices": [{"message": {"role": "assistant", "content": text}}]})


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), FakeLLM)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def _settings(url, provider="compatible", **kw):
    return AISettings(enabled=True, provider=provider, base_url=url, timeout=10, **kw)


# ---------------------------------------------------------- keyword feedback
def test_feedback_without_ai():
    a = parse_feedback("mais cachoeira, menos cidade, cortes mais rápidos, "
                       "começar com uma revelação e trocar as tomadas")
    assert a.prefer == ["waterfall"] and a.avoid_categories == ["city"]
    assert a.shot_scale < 1 and a.opener == "rise_reveal" and a.new_shots
    b = parse_feedback("vídeo mais curto, terminar com pôr do sol, sem transições, sem tremer")
    assert b.duration_scale < 1 and b.closer == "sunset" and b.transitions == "cut" and b.smooth
    assert parse_feedback("começar com a cachoeira").prefer == []      # placement, not "more"
    assert parse_feedback("").is_empty()


def test_adjusted_style_and_merge():
    st = STYLES["cinematografico"]
    fast = cinematic_director.adjusted_style(st, Adjustments(shot_scale=0.75, transitions="cut"))
    assert fast.shot_max == pytest.approx(st.shot_max * 0.75) and fast.transition == "cut"
    m = Adjustments(prefer=["lake"], shot_scale=0.75).merge(Adjustments(prefer=["waterfall"], opener="orbit"))
    assert m.prefer == ["lake", "waterfall"] and m.shot_scale == 0.75 and m.opener == "orbit"


# --------------------------------------------------------- AI: parsing/safety
def test_reply_parsing_is_tolerant_but_validation_is_strict():
    raw = '<think>x</think>Aqui está:\n```json\n{"style": "nope", "duration": 9999, ' \
          '"prefer": ["lake", "dragões"], "opener": "orbit", "transitions": "wipe", "new_shots": "sim"}\n```'
    adj = ai_assist.adjustments_from_json(ai_assist.extract_json(raw))
    assert adj.style == ""                       # unknown style dropped
    assert adj.duration == 600                   # clamped
    assert adj.prefer == ["lake"]                # unknown category dropped
    assert adj.opener == "orbit" and adj.transitions == "" and adj.new_shots is False
    with pytest.raises(ai_assist.AIError):
        ai_assist.extract_json("sem json aqui")


def test_openai_compatible_local_server(server):
    FakeLLM.mode = "good"
    client = ai_assist.AIClient(_settings(server, model=""))
    assert "respondeu" in client.ping()
    assert client.model == "fake-local-8b"       # picked the loaded chat model, not the embedder
    adj = ai_assist.interpret(client, "vídeo calmo de 40s com cachoeira", current={})
    assert adj.style == "contemplativo" and adj.duration == 40
    assert adj.prefer == ["waterfall"] and adj.opener == "pull_out"
    assert adj.shot_scale == 1.6 and adj.transitions == ""


def test_claude_api_shape_and_bad_key(server):
    FakeLLM.mode = "good"
    ok = ai_assist.AIClient(_settings(server, provider="anthropic", api_key="sk-ant-test"))
    assert ok.chat("Responda OK", "teste") == "OK"
    path, headers, body = FakeLLM.seen[-1]
    assert path.endswith("/messages") and body["model"] == "claude-haiku-5-5"
    assert headers.get("anthropic-version") == "2023-06-01" and "system" in body
    bad = ai_assist.AIClient(_settings(server, provider="anthropic", api_key="errada"))
    with pytest.raises(ai_assist.AIError, match="chave"):
        bad.chat("x", "y")
    nokey = ai_assist.AIClient(_settings(server, provider="openai", api_key=""))
    os.environ.pop("OPENAI_API_KEY", None)
    with pytest.raises(ai_assist.AIError, match="chave"):
        nokey.chat("x", "y")


def test_unreachable_ai_never_breaks_the_edit(trip):
    from drone.pipeline import Job, ai_direct
    cfg = DroneConfig()
    cfg.ai = _settings("http://127.0.0.1:9/v1")
    client = ai_assist.AIClient(cfg.ai)
    w = cfg.weights_for(STYLES["cinematografico"])
    cands = []
    for i, a in enumerate(trip["a"].values()):
        cands += candidates_for_clip(i, a, 4.0, trip["cfg"], w)
    for i, c in enumerate(cands):
        c.uid = f"S{i + 1}"
    logs = []
    adj = Adjustments(prefer=["lake"])
    out = ai_direct(client, cands, adj, Job(folder="."), cfg, STYLES["cinematografico"], w,
                    0.625, 20, False, [], {"events": []}, "16x9", logs.append)
    assert out is adj and any("diretor automático" in m for m in logs)


# ------------------------------------------------- director: redo/variations
def _cands(trip):
    cfg = trip["cfg"]
    w = cfg.weights_for(STYLES["cinematografico"])
    out = []
    for i, a in enumerate(trip["a"].values()):
        out += candidates_for_clip(i, a, 3.0, cfg, w, per_clip=3)
    for i, c in enumerate(out):
        c.uid = f"S{i + 1}"
    return cfg, w, out


def test_opener_and_closer_requests_are_honoured(trip):
    cfg, w, cands = _cands(trip)
    plan = cinematic_director.direct(cands, target=15, style=STYLES["cinematografico"], weights=w,
                                     cfg=cfg, adj=Adjustments(opener="pull_out", closer="orbit"),
                                     log_fn=lambda *_: None)
    assert plan.shots[0].candidate.movement == "pull_out"
    assert plan.shots[-1].candidate.movement == "orbit"


def test_new_shots_avoids_previous_version(trip):
    cfg, w, cands = _cands(trip)
    st = STYLES["viagem_dinamica"]
    first = cinematic_director.direct(cands, target=8, style=st, weights=w, cfg=cfg, log_fn=lambda *_: None)
    prev = [(s.candidate.path, s.start, s.end) for s in first.shots]
    again = cinematic_director.direct(cands, target=8, style=st, weights=w, cfg=cfg,
                                      adj=Adjustments(new_shots=True, variation=1), previous=prev,
                                      log_fn=lambda *_: None)
    reused = sum(cinematic_director._overlaps(s.candidate, prev) for s in again.shots)
    assert reused < len(first.shots)


def test_ai_order_is_followed(trip):
    cfg, w, cands = _cands(trip)
    order = [cands[-1].uid, cands[0].uid, cands[len(cands) // 2].uid]
    plan = cinematic_director.direct(cands, target=12, style=STYLES["viagem_dinamica"], weights=w,
                                     cfg=cfg, adj=Adjustments(order=order), log_fn=lambda *_: None)
    assert [s.candidate.uid for s in plan.shots][:3] == order


# ------------------------------------------------------------ end to end
def test_end_to_end_with_local_ai_and_redo(trip, server, tmp_path):
    from drone.pipeline import Job, run
    FakeLLM.mode = "good"
    folder = os.path.dirname(trip["files"]["DJI_0001.MP4"])
    cfg = DroneConfig()
    cfg.use_clip = False
    cfg.ai = _settings(server)
    job = Job(folder=folder, duration=14, horizontal=True, vertical=False,
              prompt="um vídeo calmo com cachoeira", output_dir=str(tmp_path),
              horizontal_size=(640, 360), crf=28)
    logs = []
    res = run(job, cfg, cache=trip["cache"], log_fn=logs.append)
    rep = json.load(open(res.report, encoding="utf-8"))
    assert rep["ai"]["enabled"] and rep["ai"]["events"][0]["ok"]
    ai_order = rep["ai"]["events"][0]["order"]
    assert "S999" not in ai_order                                  # hallucinated id dropped
    shots = [s["uid"] for s in rep["formats"]["16x9"]["shots"]]
    # The AI's picks keep its order; the director may only add filler shots
    # (before the closer) when the picked clips are too short for their slots.
    assert shots[0] == ai_order[0]
    assert [u for u in shots if u in ai_order] == [u for u in ai_order if u in shots]
    assert len([u for u in shots if u in ai_order]) >= len(shots) // 2
    assert any("sem imagens nem vídeo" in m for m in logs)

    # The AI answers nonsense on the redo: the keyword feedback still applies.
    FakeLLM.mode = "garbage"
    job2 = Job(folder=folder, duration=14, horizontal=True, vertical=False,
               feedback="cortes mais rápidos, trocar as tomadas", previous_report=res.report,
               output_dir=str(tmp_path), horizontal_size=(640, 360), crf=28)
    logs2 = []
    res2 = run(job2, cfg, cache=trip["cache"], log_fn=logs2.append)
    rep2 = json.load(open(res2.report, encoding="utf-8"))
    assert rep2["adjustments"]["new_shots"] and rep2["adjustments"]["shot_scale"] == 0.75
    assert not rep2["ai"]["events"][0]["ok"]
    assert os.path.exists(res2.outputs[0])
