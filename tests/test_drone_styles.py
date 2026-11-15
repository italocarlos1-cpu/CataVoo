# SPDX-License-Identifier: AGPL-3.0-or-later
"""Edit styles, pace curves, adaptive rhythm and the automatic style advisor."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from tests import test_drone_pipeline as base  # noqa: E402  (real cv2 swap + trip fixture)

trip = base.trip
_real_opencv = base._real_opencv

from drone import cinematic_director as cd  # noqa: E402
from drone import style_advisor  # noqa: E402
from drone.config import STYLES, DroneConfig  # noqa: E402
from drone.visual_scoring import candidates_for_clip  # noqa: E402

UNIT = 60 / 96


def _p(**kw):
    p = style_advisor.TripProfile(clips=kw.pop("clips", 12), seconds=600)
    p.categories = kw.pop("cats", {"forest": 0.4, "countryside": 0.3, "river": 0.3})
    p.movements = kw.pop("moves", {"pan": 0.3, "orbit": 0.2, "push_in": 0.2, "hover": 0.3})
    for k, v in kw.items():
        setattr(p, k, v)
    p.smooth_share = kw.get("smooth_share", 0.6)
    p.variety = kw.get("variety", 0.5)
    p.interest = kw.get("interest", 0.5)
    return p


def _best(p, **kw):
    sc = style_advisor.score_styles(p, **kw)
    return max(sc, key=lambda k: sc[k][0])


@pytest.mark.parametrize("profile,expected", [
    (dict(cats={"sunset": 0.45, "lake": 0.3, "mountains": 0.25}), "por_do_sol"),
    (dict(cats={"historic": 0.5, "city": 0.2, "countryside": 0.3}), "cidade_historica"),
    (dict(moves={"flyover": 0.35, "fast": 0.25, "pan": 0.4}), "aventura_fpv"),
    (dict(clips=4, moves={"hover": 0.6, "pan": 0.3, "orbit": 0.1}, variety=0.15, smooth_share=0.9),
     "minimalista"),
    (dict(cats={"waterfall": 0.4, "mountains": 0.3, "forest": 0.3}, smooth_share=0.95,
          moves={"pan": 0.2, "orbit": 0.2, "push_in": 0.2, "hover": 0.2, "pull_out": 0.2}), "contemplativo"),
    (dict(span_hours=50, spread_km=40, variety=0.7), "diario_viagem"),
    (dict(cats={"countryside": 0.5, "road": 0.5}, smooth_share=0.4, interest=0.3, variety=0.2), "cinematografico"),
])
def test_advisor_reads_the_trip(profile, expected):
    assert _best(_p(**profile)) == expected


def test_advisor_picks_social_for_short_vertical():
    assert _best(_p(), vertical_only=True, target=30) == "redes_sociais"


def test_every_style_is_complete_and_on_the_beat():
    assert len(STYLES) >= 10
    for st in STYLES.values():
        assert st.label and st.description
        for target in (30, 60, 120):
            slots = cd.plan_slots(target, st, UNIT)
            assert abs(sum(s.advance for s in slots) - target) <= UNIT + 1e-6
            assert all(abs(s.advance / UNIT - round(s.advance / UNIT)) < 1e-6 for s in slots)


def test_trailer_accelerates():
    slots = cd.plan_slots(60, STYLES["trailer_epico"], UNIT)
    body = [s.advance for s in slots if s.role == "body"]
    assert body[0] > 2 * body[-1]


def _shot(movement, interest=0.6, smooth=0.9, order=0):
    return SimpleNamespace(movement=movement, order=order,
                           parts={"interest": interest, "smoothness": smooth, "quality": 0.8})


def test_adaptive_rhythm_gives_calm_shots_more_time_and_keeps_sync():
    st = STYLES["cinematografico"]
    slots = cd.plan_slots(40, st, UNIT)
    shots = [_shot("orbit")] + [_shot("hover" if i % 2 else "flyover") for i in range(len(slots) - 2)] \
        + [_shot("pull_out")]
    before = sum(s.advance for s in slots)
    cd.adapt_slots(slots, shots, UNIT, st)
    assert sum(s.advance for s in slots) == pytest.approx(before)
    hover = [s.advance for s, c in zip(slots, shots) if s.role == "body" and c.movement == "hover"]
    fly = [s.advance for s, c in zip(slots, shots) if s.role == "body" and c.movement == "flyover"]
    assert min(hover) > max(fly)
    assert all(abs(s.advance / UNIT - round(s.advance / UNIT)) < 1e-6 for s in slots)


def test_diary_keeps_shooting_order(trip):
    cfg = trip["cfg"]
    st = STYLES["diario_viagem"]
    w = cfg.weights_for(st)
    cands = []
    for i, a in enumerate(trip["a"].values()):
        cands += candidates_for_clip(i, a, 3.0, cfg, w, per_clip=2)
    plan = cd.direct(cands, target=24, style=st, weights=w, cfg=cfg, log_fn=lambda *_: None)
    orders = [s.candidate.order for s in plan.shots[1:-1]]      # body follows the trip
    backwards = sum(b < a for a, b in zip(orders, orders[1:]))
    assert backwards <= 1


def test_profile_from_real_analysis(trip):
    p = style_advisor.profile(list(trip["a"].values()), trip["cfg"])
    assert p.clips == 8 and abs(sum(p.categories.values()) - 1) < 1e-6
    assert {"pan", "orbit", "push_in"} & set(p.movements)


def test_automatic_end_to_end(trip, tmp_path):
    from drone.pipeline import Job, run
    cfg = DroneConfig()
    cfg.use_clip = False
    job = Job(folder=os.path.dirname(trip["files"]["DJI_0001.MP4"]), style="automatico",
              duration=12, horizontal=True, vertical=False, output_dir=str(tmp_path),
              horizontal_size=(640, 360), crf=28)
    logs = []
    res = run(job, cfg, cache=trip["cache"], log_fn=logs.append)
    rep = json.load(open(res.report, encoding="utf-8"))
    assert rep["auto_style"]["chosen"] in STYLES and rep["style"] == rep["auto_style"]["chosen"]
    assert rep["style"] != "automatico"
    assert any("Estilo automático" in m for m in logs)
    assert os.path.exists(res.outputs[0])


def test_diario_follows_recording_order_strictly(trip):
    """Diário de viagem: every shot in shooting order — opener, closer and the
    extras that fill the duration included — and never back to a clip already left."""
    cfg = trip["cfg"]
    st = STYLES["diario_viagem"]
    w = cfg.weights_for(st)
    cands = []
    for i, a in enumerate(trip["a"].values()):
        cands += candidates_for_clip(i, a, 3.0, cfg, w, per_clip=4)
    for target in (24, 60):                      # 60 s forces extras and stretching
        plan = cd.direct(cands, target=target, style=st, weights=w, cfg=cfg, title="T",
                         log_fn=lambda *_: None)
        keys = [(p.candidate.order, p.start) for p in plan.shots]
        assert keys == sorted(keys)
        seen, last = set(), None
        for p in plan.shots:
            o = p.candidate.order
            if o != last:
                assert o not in seen, "voltou a um vídeo que já tinha passado"
                seen.add(o)
                last = o
        assert plan.shots[0].text == "T"
        assert all(p.end - p.start >= p.slot.advance - 1e-3 for p in plan.shots)
