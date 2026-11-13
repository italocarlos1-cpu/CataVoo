# SPDX-License-Identifier: AGPL-3.0-or-later
"""DroneHighlighter tests on synthetic aerial footage with known ground truth."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import numpy as np
import pytest

from unittest.mock import MagicMock

# tests/conftest.py replaces cv2 with a MagicMock for the whole suite. These
# tests measure real pixels, so the real module is installed while this file's
# imports bind it, and the shim is handed back when the module's tests finish.
_SHIM = sys.modules.pop("cv2", None)
try:
    import cv2  # noqa: E402
except Exception:
    cv2 = None
if cv2 is None or isinstance(cv2, MagicMock) or shutil.which("ffmpeg") is None:
    if _SHIM is not None:
        sys.modules["cv2"] = _SHIM
    pytest.skip("real OpenCV and ffmpeg required", allow_module_level=True)
# Drop drone modules only if something imported them while cv2 was the shim.
_fm = sys.modules.get("drone.frame_metrics")
if _fm is not None and isinstance(getattr(_fm, "cv2", None), MagicMock):
    for _m in [m for m in sys.modules if m == "drone" or m.startswith("drone.") or m == "drone_fixtures"]:
        del sys.modules[_m]


@pytest.fixture(scope="module", autouse=True)
def _real_opencv():
    sys.modules["cv2"] = cv2
    yield
    if _SHIM is not None:
        sys.modules["cv2"] = _SHIM

sys.path.insert(0, os.path.dirname(__file__))
from drone_fixtures import make_music, make_trip  # noqa: E402

from drone import analyzer, cinematic_director, landscape, motion_analysis  # noqa: E402
from drone.analysis_cache import AnalysisCache  # noqa: E402
from drone.config import STYLES, DroneConfig  # noqa: E402
from drone.duplicate_detection import similarity  # noqa: E402
from drone.ingest import parse_dji_srt, scan_folder  # noqa: E402
from drone.prompt_parser import parse  # noqa: E402
from drone.visual_scoring import candidates_for_clip  # noqa: E402


@pytest.fixture(scope="module")
def trip(tmp_path_factory):
    root = tmp_path_factory.mktemp("trip")
    files = make_trip(str(root / "Brutos"))
    cache = AnalysisCache(str(root / "cache.sqlite"))
    cfg = DroneConfig()
    cfg.use_clip = False
    analyses = {}
    for i, c in enumerate(scan_folder(str(root / "Brutos"), log_fn=lambda *_: None)):
        a = analyzer.analyze_clip(c, cfg, cache, log_fn=lambda *_: None)
        a["order"] = i
        analyses[c.name] = a
    landscape.annotate(list(analyses.values()), cache, use_clip=False, log_fn=lambda *_: None)
    return {"root": root, "files": files, "cache": cache, "cfg": cfg, "a": analyses}


def _movement(a, cfg):
    tl = a["timeline"]
    n = len(tl["t"])
    sl = slice(2, n - 2)
    return motion_analysis.classify_movement(tl["tvx"][sl], tl["tvy"][sl], tl["tzoom"][sl],
                                             tl["parallax"][sl], tl["speed"][sl], cfg=cfg.motion)[0]


@pytest.mark.parametrize("name,expected", [
    ("DJI_0001.MP4", "pan"), ("DJI_0002.MP4", "push_in"), ("DJI_0003.MP4", "rise_reveal"),
    ("DJI_0006.MP4", "orbit"), ("DJI_0008.MOV", "pull_out"), ("DJI_0005.MP4", "hover"),
])
def test_camera_move_is_recognised(trip, name, expected):
    assert _movement(trip["a"][name], trip["cfg"]) == expected


def test_smooth_move_is_not_mistaken_for_shake(trip):
    cfg = trip["cfg"]
    pan = trip["a"]["DJI_0001.MP4"]["timeline"]
    shaky = trip["a"]["DJI_0004.MP4"]["timeline"]
    # The pan moves the whole frame continuously, yet scores as perfectly smooth...
    assert np.mean(pan["speed"]) > cfg.motion.hover_speed
    s_pan = motion_analysis.smoothness_score(np.array(pan["jitter"][2:-2]), np.array(pan["jerk"][2:-2]), cfg=cfg.motion)
    s_shk = motion_analysis.smoothness_score(np.array(shaky["jitter"][2:-2]), np.array(shaky["jerk"][2:-2]), cfg=cfg.motion)
    assert s_pan > 0.9
    # ...while a hovering-but-vibrating camera is rejected outright.
    assert s_shk < 0.2
    w = cfg.weights_for(STYLES["cinematografico"])
    assert candidates_for_clip(0, trip["a"]["DJI_0004.MP4"], 4.0, cfg, w) == []


def test_blur_lowers_quality(trip):
    cfg = trip["cfg"]
    w = cfg.weights_for(STYLES["cinematografico"])
    sharp = candidates_for_clip(0, trip["a"]["DJI_0008.MOV"], 4.0, cfg, w)
    blurry = candidates_for_clip(1, trip["a"]["DJI_0005.MP4"], 4.0, cfg, w)
    assert sharp and blurry
    assert blurry[0].parts["quality"] < sharp[0].parts["quality"] - 0.25


def test_duplicate_takes_detected_and_not_both_selected(trip):
    cfg = trip["cfg"]
    w = cfg.weights_for(STYLES["cinematografico"])
    a1 = candidates_for_clip(0, trip["a"]["DJI_0001.MP4"], 4.0, cfg, w)[0]
    a7 = candidates_for_clip(6, trip["a"]["DJI_0007.MP4"], 4.0, cfg, w)[0]
    other = candidates_for_clip(1, trip["a"]["DJI_0002.MP4"], 4.0, cfg, w)[0]
    assert similarity(a1, a7) > cfg.duplicate_threshold
    assert similarity(a1, other) < similarity(a1, a7)
    from drone.shot_selection import select
    cands = []
    for i, a in enumerate(trip["a"].values()):
        cands += candidates_for_clip(i, a, 4.0, cfg, w)
    chosen = select(cands, 5, w_variety=w.variety, duplicate_threshold=cfg.duplicate_threshold,
                    max_per_clip=2)
    paths = {os.path.basename(c.path) for c in chosen}
    assert not {"DJI_0001.MP4", "DJI_0007.MP4"} <= paths


def test_cache_reuses_analysis_and_survives_rename(trip, tmp_path):
    cache, cfg = trip["cache"], trip["cfg"]
    clip = scan_folder(os.path.dirname(trip["files"]["DJI_0002.MP4"]), log_fn=lambda *_: None)[1]
    t0 = time.time()
    again = analyzer.analyze_clip(clip, cfg, cache)
    assert time.time() - t0 < 0.5
    assert again["timeline"]["t"] == trip["a"]["DJI_0002.MP4"]["timeline"]["t"]
    moved = tmp_path / "renomeado.mp4"
    shutil.copy2(clip.path, moved)
    assert cache.get(str(moved), cfg.analysis.key()) is not None
    cfg2 = DroneConfig()
    cfg2.analysis.sample_hz = 2.0          # other parameters → not a hit
    assert cache.get(clip.path, cfg2.analysis.key()) is None


def test_prompt_parser_reads_the_spec_example():
    p = parse("Crie um vídeo cinematográfico de Pirenópolis com 90 segundos, priorizando "
              "cachoeiras, serras e movimentos suaves, com música instrumental tranquila.")
    assert p.style == "cinematografico"
    assert p.duration == 90
    assert p.title == "Pirenópolis"
    assert {"waterfall", "mountains"} <= set(p.priorities)
    assert p.smooth
    assert {"instrumental", "tranquila"} <= set(p.music_tags)
    q = parse("reels vertical de 1 minuto e 30 segundos, viagem dinâmica")
    assert q.duration == 90 and q.vertical and not q.horizontal and q.style == "redes_sociais"


@pytest.mark.parametrize("style", sorted(STYLES))
def test_slots_land_on_the_beat_and_fill_the_target(style):
    unit = 60 / 96
    slots = cinematic_director.plan_slots(90, STYLES[style], unit)
    total = sum(s.advance for s in slots)
    assert abs(total - 90) <= unit + 1e-6
    for s in slots:
        assert abs(s.advance / unit - round(s.advance / unit)) < 1e-6
    body = [s for s in slots if s.role == "body"]
    st = STYLES[style]
    assert all(st.shot_min - unit <= s.advance <= st.shot_max * 1.6 for s in body)


def test_vertical_crop_follows_subject():
    from drone.edl_adapter import _crop_box
    cw, ch, x, _ = _crop_box(3840, 2160, 0.9, 1080, 1920)
    assert (cw, ch) == (1214, 2160)
    assert x == 3840 - 1214 - (3840 - 1214) % 2 or x + cw <= 3840
    cw, ch, x, _ = _crop_box(3840, 2160, 0.05, 1080, 1920)
    assert x == 0


def test_dji_srt_telemetry(tmp_path):
    srt = tmp_path / "DJI_0001.SRT"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,033\n<font size=\"28\">FrameCnt: 1 "
                   "[iso: 100] [latitude: -15.851234] [longitude: -48.959876] "
                   "[rel_alt: 87.300 abs_alt: 830.1]</font>\n", encoding="utf-8")
    t = parse_dji_srt(str(srt))
    assert t["lat"] == pytest.approx(-15.851234)
    assert t["lon"] == pytest.approx(-48.959876)
    assert t["altitude"] == pytest.approx(87.3)


def test_end_to_end_both_formats_with_music(trip, tmp_path):
    from drone.pipeline import Job, run
    music_dir = tmp_path / "musicas" / "instrumental"
    music_dir.mkdir(parents=True)
    make_music(str(music_dir / "piano_96.wav"), 60, 96)
    job = Job(folder=os.path.dirname(trip["files"]["DJI_0001.MP4"]), style="cinematografico",
              duration=24, vertical_duration=12, title="Pirenópolis — Goiás",
              music_dir=str(tmp_path / "musicas"), output_dir=str(tmp_path / "saida"),
              horizontal_size=(640, 360), vertical_size=(360, 640), fps=30, crf=23)
    cfg = DroneConfig()
    cfg.use_clip = False
    res = run(job, cfg, cache=trip["cache"], log_fn=lambda *_: None)
    assert len(res.outputs) == 2
    unit = 60 / 96
    for out, (w, h), target in zip(res.outputs, [(640, 360), (360, 640)], [24, 12]):
        info = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                               "stream=codec_type,width,height:format=duration",
                               "-of", "json", out], capture_output=True, text=True).stdout
        import json
        d = json.loads(info)
        kinds = {s["codec_type"] for s in d["streams"]}
        assert kinds == {"video", "audio"}
        v = next(s for s in d["streams"] if s["codec_type"] == "video")
        assert (v["width"], v["height"]) == (w, h)
        assert abs(float(d["format"]["duration"]) - target) <= 2 * unit + 0.2
    # originals untouched, EDL saved and reopenable by the engine
    assert len(os.listdir(os.path.dirname(trip["files"]["DJI_0001.MP4"]))) >= 8
    from modules.media.edl import load_edl
    for e in res.edls:
        edl = load_edl(e)
        assert edl.cuts and all(os.path.exists(c.source) for c in edl.cuts)
        assert os.path.exists(edl.music)


def test_clip_backend_plumbing_and_embedding_cache(trip, monkeypatch):
    """CLIP itself needs model weights; this checks everything around it with a
    stand-in that 'sees' a waterfall: probabilities, interest, cache, unload."""
    from drone import landscape as ls
    calls = {"infer": 0, "unload": 0}

    class FakeOut:
        def __init__(self, n, k):
            logits = np.zeros((n, k))
            logits[:, 0] = 5.0                      # "waterfall"
            self.logits_per_image = logits

    class FakePF:
        def infer(self, texts, images):
            calls["infer"] += 1
            return FakeOut(len(images), len(texts))

    monkeypatch.setattr(ls.ClipScorer, "available", staticmethod(lambda: True))
    monkeypatch.setattr(ls.ClipScorer, "load", lambda self: setattr(self, "_pf", FakePF()))
    orig_unload = ls.ClipScorer.unload
    monkeypatch.setattr(ls.ClipScorer, "unload",
                        lambda self: (calls.__setitem__("unload", calls["unload"] + 1), orig_unload(self)))
    monkeypatch.setattr(ls, "MODEL_TAG", "fake-clip-test")
    analyses = [dict(a) for a in trip["a"].values()][:3]
    for a in analyses:
        a["timeline"] = dict(a["timeline"])
    backend = ls.annotate(analyses, trip["cache"], use_clip=True, log_fn=lambda *_: None)
    assert backend == "clip" and calls["infer"] >= 3 and calls["unload"] == 1
    tl = analyses[0]["timeline"]
    assert np.argmax(np.asarray(tl["cat_probs"]).mean(axis=0)) == ls.CAT_KEYS.index("waterfall")
    assert np.mean(tl["interest"]) > 0.8
    # Second run: embeddings come from the cache, the model is never loaded.
    calls["infer"] = 0
    ls.annotate(analyses, trip["cache"], use_clip=True, log_fn=lambda *_: None)
    assert calls["infer"] == 0
