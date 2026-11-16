# SPDX-License-Identifier: AGPL-3.0-or-later
"""Automatic length, the not-enough-footage decision and one film per group."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta

import numpy as np
import pytest

from tests import test_drone_pipeline as base  # noqa: E402  (real cv2 swap + trip fixture)

trip = base.trip
_real_opencv = base._real_opencv

from drone import grouping, material, music_selector  # noqa: E402
from drone.config import STYLES, DroneConfig  # noqa: E402
from drone.pipeline import Job, run  # noqa: E402
from drone.visual_scoring import candidates_for_clip  # noqa: E402


def _duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return float(out.strip())


def _cfg():
    cfg = DroneConfig()
    cfg.use_clip = False
    return cfg


def _job(trip, tmp_path, **kw):
    kw.setdefault("folder", os.path.dirname(trip["files"]["DJI_0001.MP4"]))
    kw.setdefault("horizontal", True)
    kw.setdefault("vertical", False)
    return Job(output_dir=str(tmp_path), horizontal_size=(640, 360), vertical_size=(360, 640),
               crf=30, **kw)


# ------------------------------------------------------------------ material
def test_material_estimate_is_consistent(trip):
    cfg = trip["cfg"]
    st = STYLES["cinematografico"]
    w = cfg.weights_for(st)
    cands = []
    for i, a in enumerate(trip["a"].values()):
        cands += candidates_for_clip(i, a, 6.0, cfg, w)
    est = material.estimate(cands, st, cfg, w)
    assert 3 <= est.distinct <= 8                     # 8 clips, one shaky, one duplicate
    assert est.strong <= est.distinct
    assert 0 < est.comfortable <= est.maximum
    assert material.MIN_SUGGESTED <= est.suggested <= max(material.MIN_SUGGESTED, est.comfortable)
    assert est.suggested % 5 == 0 and est.suggested_vertical <= 60
    assert material.shortfall(est.comfortable + 30, est, 0.5)
    assert not material.shortfall(est.comfortable - 5, est, 0.5)


def test_automatic_length(trip, tmp_path):
    res = run(_job(trip, tmp_path, duration=0), _cfg(), cache=trip["cache"], log_fn=lambda *_: None)
    rep = json.load(open(res.report, encoding="utf-8"))
    fmt = rep["formats"]["16x9"]
    assert fmt["automatic_length"] and fmt["asked"] == rep["material"]["suggested"]
    assert abs(_duration(res.outputs[0]) - fmt["asked"]) <= 2.0


def test_not_enough_material_shorten(trip, tmp_path):
    asked = []
    res = run(_job(trip, tmp_path, duration=300, shortfall="perguntar"), _cfg(), cache=trip["cache"],
              log_fn=lambda *_: None, decide_fn=lambda info: asked.append(info) or "encurtar")
    assert len(asked) == 1 and asked[0]["asked"] == 300
    rep = json.load(open(res.report, encoding="utf-8"))
    assert rep["formats"]["16x9"]["asked"] == asked[0]["shorter"] < 300
    assert _duration(res.outputs[0]) <= asked[0]["shorter"] + 2


def test_not_enough_material_cancel(trip, tmp_path):
    with pytest.raises(RuntimeError, match="cancelado"):
        run(_job(trip, tmp_path, duration=300, shortfall="perguntar"), _cfg(), cache=trip["cache"],
            log_fn=lambda *_: None, decide_fn=lambda info: "cancelar")


def test_fixed_policies_do_not_ask(trip, tmp_path):
    called = []
    res = run(_job(trip, tmp_path, duration=300, shortfall="encurtar"), _cfg(), cache=trip["cache"],
              log_fn=lambda *_: None, decide_fn=lambda info: called.append(1) or "esticar")
    assert not called and _duration(res.outputs[0]) < 300


# ------------------------------------------------------------------ grouping
def _fake(i, minutes, lat=None, lon=None, dur=60.0, folder="/v"):
    t0 = datetime(2026, 5, 10, 9, 0)
    return {"path": f"{folder}/DJI_{i:04d}.MP4", "duration": dur, "lat": lat, "lon": lon,
            "creation_time": (t0 + timedelta(minutes=minutes)).isoformat()}


def test_group_by_flight_day_and_place():
    a = [_fake(1, 0), _fake(2, 2), _fake(3, 4),            # flight 1
         _fake(4, 40), _fake(5, 42),                       # flight 2 (battery swap)
         _fake(6, 60 * 24 + 5)]                            # next day
    flights = grouping.by_flight(a)
    assert [g.indices for g in flights] == [[0, 1, 2], [3, 4], [5]]
    days = grouping.by_day(a)
    assert [len(g.indices) for g in days] == [5, 1]
    b = [_fake(1, 0, -15.851, -48.959), _fake(2, 5, -15.852, -48.958),   # same waterfall
         _fake(3, 90, -15.700, -48.800),                                 # ~25 km away
         _fake(4, 92)]                                                   # no GPS → nearest in time
    places = grouping.by_place(b, radius_km=1.5)
    assert sorted(sorted(g.indices) for g in places) == [[0, 1], [2, 3]]
    assert grouping.by_place([_fake(1, 0), _fake(2, 50)], log_fn=lambda *_: None)[0].key == "voo1"


def test_group_list_adds_summary_only_when_useful():
    a = [_fake(1, 0), _fake(2, 60)]
    gs = grouping.make_groups(a, "voo", summary=True, log_fn=lambda *_: None)
    assert [g.key for g in gs] == ["voo1", "voo2", "tudo"]
    one = grouping.make_groups([_fake(1, 0)], "voo", summary=True, log_fn=lambda *_: None)
    assert [g.key for g in one] == ["tudo"]


def test_one_film_per_subfolder_plus_summary(trip, tmp_path):
    src = os.path.dirname(trip["files"]["DJI_0001.MP4"])
    root = tmp_path / "Viagem"
    for sub, names in (("Cachoeira", ["DJI_0002.MP4", "DJI_0003.MP4"]),
                       ("Cidade", ["DJI_0006.MP4", "DJI_0008.MOV"])):
        (root / sub).mkdir(parents=True)
        for n in names:
            shutil.copy2(os.path.join(src, n), root / sub / n)
    res = run(_job(trip, tmp_path / "out", folder=str(root), duration=8, group_by="subpasta",
                   summary=True, shortfall="encurtar"),
              _cfg(), cache=trip["cache"], log_fn=lambda *_: None)
    names = sorted(os.path.basename(p) for p in res.outputs)
    assert len(names) == 3
    assert any("_Cachoeira_" in n for n in names) and any("_Cidade_" in n for n in names)
    assert any("_viagem_completa_" in n for n in names)
    rep = json.load(open(res.report, encoding="utf-8"))
    assert [g["key"] for g in rep["groups"]] == ["sub1", "sub2", "tudo"]
    cach = rep["groups"][0]
    assert all("Cachoeira" in p for p in cach["clips"])
    assert all("Cachoeira" in s["source"] for s in cach["formats"]["16x9"]["shots"])
    assert len(os.listdir(root / "Cachoeira")) == 2                    # originals untouched


# --------------------------------------------------------------------- music
def test_short_music_is_looped_and_faded(tmp_path):
    from tests.drone_fixtures import make_music
    track = make_music(str(tmp_path / "curta.wav"), 5, 120)
    choice = music_selector.MusicChoice(path=track, duration=5.0, bpm=120, beat_interval=0.5, start=0.0)
    out = music_selector.prepare(choice, 12.0, str(tmp_path / "m.m4a"))
    assert abs(_duration(out) - 12.0) < 0.3


def test_parallel_analysis_matches_one_at_a_time(trip, tmp_path, monkeypatch):
    """Analysing several clips at once must give exactly the same analyses, in
    shooting order, as one at a time."""
    from drone import pipeline
    from drone.analysis_cache import AnalysisCache
    from drone.config import DroneConfig

    def analyse(workers, name):
        monkeypatch.setenv("CATAVOO_WORKERS", str(workers))
        cfg = DroneConfig()
        cfg.use_clip = False
        job = pipeline.Job(folder=str(trip["root"] / "Brutos"), output_dir=str(tmp_path / name))
        res = pipeline.Result()
        seen = []
        out, _ = pipeline._analyse(job, cfg, AnalysisCache(str(tmp_path / f"{name}.sqlite")),
                                   pipeline.Progress(lambda p, t: seen.append(p)), res,
                                   lambda *_: None, None)
        assert seen == sorted(seen) or max(seen) <= 66.0001     # never past the stage's share
        return out, res

    one, r1 = analyse(1, "um")
    par, r2 = analyse(3, "tres")
    assert r1.analysed == r2.analysed == len(one) and r2.cached == 0
    assert [a["name"] for a in one] == [a["name"] for a in par]
    assert [a["order"] for a in par] == sorted(a["order"] for a in par)
    for a, b in zip(one, par):
        assert a["timeline"].keys() == b["timeline"].keys()
        for k in a["timeline"]:
            assert np.array_equal(np.asarray(a["timeline"][k]), np.asarray(b["timeline"][k])), k
