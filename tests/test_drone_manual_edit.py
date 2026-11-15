# SPDX-License-Identifier: AGPL-3.0-or-later
"""Edição manual: the editable cut list, planning without rendering, rendering
an edited plan, and the timeline window itself (offscreen)."""
from __future__ import annotations

import json
import os
import subprocess

import numpy as np
import pytest

from tests import test_drone_pipeline as base  # noqa: E402  (real cv2 swap + trip fixture)

trip = base.trip
_real_opencv = base._real_opencv

from drone.config import DroneConfig  # noqa: E402
from drone.edit_session import EditPlan, EditStore, fmt_time  # noqa: E402
from drone.pipeline import Job, Session  # noqa: E402


@pytest.fixture(scope="module")
def session(trip, tmp_path_factory):
    from tests.drone_fixtures import make_music
    music = tmp_path_factory.mktemp("musicas") / "instrumental"
    music.mkdir()
    make_music(str(music / "piano_96.wav"), 60, 96)
    out = tmp_path_factory.mktemp("saida")
    cfg = DroneConfig()
    cfg.use_clip = False
    job = Job(folder=os.path.dirname(trip["files"]["DJI_0001.MP4"]), style="cinematografico",
              duration=20, vertical_duration=10, music_dir=str(music.parent), output_dir=str(out),
              title="Teste", horizontal_size=(640, 360), vertical_size=(360, 640), crf=30)
    return Session(job, cfg, cache=trip["cache"], log_fn=lambda *_: None)


def _edit(session, style="cinematografico", tag="16x9") -> EditPlan:
    gp, plan = session.plan("tudo", style, tag)
    return EditPlan.from_plan(plan, gp, style_key=style, tag=tag, target=plan.total)


def _duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return float(out.strip())


# ------------------------------------------------------------ planning only
def test_any_style_is_planned_without_rendering(session):
    out_dir = session.ctx.out_dir
    for style in ("cinematografico", "trailer_epico", "contemplativo", "automatico"):
        for tag in ("16x9", "9x16"):
            ep = _edit(session, style, tag)
            assert ep.shots and ep.total > 0
            assert all(ep.on_beat(i) for i in range(len(ep.shots)))      # the automatic cut is on the beat
    assert not any(f.endswith(".mp4") for f in os.listdir(out_dir)) if os.path.isdir(out_dir) else True


def test_from_plan_matches_the_automatic_cut(session):
    gp, plan = session.plan("tudo", "cinematografico", "16x9")
    ep = EditPlan.from_plan(plan, gp, style_key="cinematografico", tag="16x9")
    assert ep.total == pytest.approx(plan.total, abs=0.01)
    assert [s.uid for s in ep.shots] == [p.candidate.uid for p in plan.shots]
    assert len(ep.candidates) == len(gp.cands) and set(ep.clips) == {a["path"] for a in gp.analyses}


# ------------------------------------------------------------ editing model
def test_frame_nudge_is_exact_and_ignores_the_magnet(session):
    ep = _edit(session)
    f = ep.frame(ep.shots[0].path)
    end = ep.shots[0].end
    ep.nudge(0, "end", frames=3)
    new = ep.shots[0].end
    assert abs(new / f - round(new / f)) < 0.01             # lands on a real frame
    assert end + 2 * f - 1e-6 < new <= end + 3 * f + 1e-6   # three frame steps forward
    assert not ep.on_beat(0) and ep.edited
    ep.nudge(0, "end", frames=1)
    assert ep.shots[0].end == pytest.approx(new + f, abs=1e-3)   # on the grid: exactly one frame
    ep.nudge(0, "end", frames=-1)
    assert ep.shots[0].end == pytest.approx(new, abs=1e-3)


def test_magnet_snaps_drags_to_the_beat(session):
    ep = _edit(session)
    s = ep.shots[1]
    ep.trim(1, "end", s.end + 0.9 * ep.unit)          # with the ímã: whole beats
    assert ep.on_beat(1)
    ep.trim(1, "start", s.start + 0.37, snap=False)    # without: free, but on a frame
    f = ep.frame(s.path)
    assert abs(s.start - round(s.start / f) * f) < 1e-3
    assert not ep.on_beat(1)
    assert ep.snap_all() >= 1 and all(ep.on_beat(i) for i in range(len(ep.shots)))


def test_trim_respects_clip_bounds_and_minimum(session):
    ep = _edit(session)
    s = ep.shots[0]
    dur = ep.clips[s.path].duration
    ep.trim(0, "end", dur + 50, snap=False)
    assert s.end <= dur + 1e-6
    ep.trim(0, "start", -10, snap=False)
    assert s.start >= 0
    ep.trim(0, "end", s.start + 0.01, snap=False)
    assert s.length >= 0.5 - 1e-6
    before = s.length
    ep.slide(0, 1000)
    assert s.end <= dur + 1e-6 and s.length == pytest.approx(before, abs=1e-3)


def test_total_follows_edits(session):
    ep = _edit(session)
    t0 = ep.total
    ep.trim(0, "end", ep.shots[0].end + 2 * ep.unit)
    assert ep.total == pytest.approx(t0 + 2 * ep.unit, abs=2e-3) or ep.total <= t0 + 2 * ep.unit + 1e-3
    n = len(ep.shots)
    ep.remove(n - 1)
    assert len(ep.shots) == n - 1 and ep.shots[-1].transition == "cut"
    assert ep.cut_times()[0] == 0 and ep.cut_times() == sorted(ep.cut_times())


def test_add_replace_reorder_and_transitions(session):
    ep = _edit(session)
    used = {(s.path, round(s.start, 2)) for s in ep.shots}
    spare = [c for c in ep.candidates if (c["path"], round(c["start"], 2)) not in used]
    assert spare
    n = len(ep.shots)
    at = ep.insert(spare[0], 1)
    assert at == 1 and len(ep.shots) == n + 1 and ep.shots[1].uid == spare[0]["uid"]
    assert ep.shots[1].start >= spare[0]["scene_lo"] - 1e-6 and ep.shots[1].end <= spare[0]["scene_hi"] + 1e-6
    ep.replace(0, spare[-1])
    assert ep.shots[0].uid == spare[-1]["uid"]
    uids = [s.uid for s in ep.shots]
    ep.reorder(list(reversed(range(len(ep.shots)))))
    assert [s.uid for s in ep.shots] == list(reversed(uids))
    assert ep.shots[-1].transition == "cut"
    ep.set_transition(0, "crossfade", 0.6)
    assert ep.trans_out(0) > 0 and ep.advance(0) == pytest.approx(ep.shots[0].length - ep.trans_out(0))
    ep.set_transition(0, "cut")
    assert ep.trans_out(0) == 0


def test_round_trip_and_store(session, tmp_path):
    ep = _edit(session, "trailer_epico")
    ep.nudge(0, "start", frames=2)
    store = EditStore(str(tmp_path / "edicao_manual.json"))
    store.put("tudo", ep)
    store.save()
    again = EditStore(str(tmp_path / "edicao_manual.json"))
    assert again.edited_styles("tudo", "16x9") == {"trailer_epico"}
    back = EditPlan.from_dict(again.get("tudo", "trailer_epico", "16x9"), clips=ep.clips)
    assert [(s.uid, s.start, s.end) for s in back.shots] == [(s.uid, s.start, s.end) for s in ep.shots]
    assert back.total == pytest.approx(ep.total)
    # an untouched plan is not stored
    store.put("tudo", _edit(session, "contemplativo"))
    assert "contemplativo" not in store.edited_styles("tudo", "16x9")
    assert fmt_time(65.5, 30) == "1:05.15"


def test_to_plan_is_renderable(session):
    ep = _edit(session)
    plan = ep.to_plan(title="Título")
    assert plan.total == pytest.approx(ep.total)
    assert plan.shots[0].text == "Título" and plan.shots[-1].slot.transition == "cut"
    from drone.cinematic_director import to_edl
    edl = to_edl(plan, title="Título", width=640, height=360, fps=30, crf=30)
    assert [c.source for c in edl.cuts] == [s.path for s in ep.shots]


# ------------------------------------------------------------ rendering
def test_render_an_edited_plan(session):
    ep = _edit(session)
    ep.trim(0, "end", ep.shots[0].end + 1.0, snap=False)       # a frame-exact manual change
    gp = session.prepare("tudo", "cinematografico")
    out = session.render(gp, ep.to_plan(gp.music, "Teste"), "16x9")
    assert os.path.exists(out["video"]) and "_manual_" in os.path.basename(out["video"])
    assert abs(_duration(out["video"]) - ep.total) <= 0.5
    rep = json.load(open(out["report"], encoding="utf-8"))
    assert rep["manual_edit"] and rep["formats"]["16x9"]["shots"][0]["out"] == pytest.approx(ep.shots[0].end)


# ------------------------------------------------------------ the window
def test_timeline_window_offscreen(session, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["DRONEHIGHLIGHTER_NO_VIDEO_PREVIEW"] = "1"
    qtw = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    if not hasattr(qtw, "QApplication") or "Mock" in type(qtw.QApplication).__name__:
        pytest.skip("real PySide6 required")
    app = qtw.QApplication.instance() or qtw.QApplication([])
    from drone.editor_window import EditorWindow
    store = EditStore(str(tmp_path / "edicao_manual.json"))
    w = EditorWindow(session, style_key="cinematografico", tag="16x9", store=store, title="Teste",
                     main_style="cinematografico")
    w.show()
    app.processEvents()
    n = len(w.plan.shots)
    assert n >= 2 and len(w.shot_items) == n and w.strip.count() == n
    w.select(0, "end")
    end = w.plan.shots[0].end
    w.view.setFocus()
    QTest.keyClick(w.view, Qt.Key_Right)
    f = w.plan.frame(w.plan.shots[0].path)
    assert end < w.plan.shots[0].end <= end + f + 1e-6
    QTest.keyClick(w.view, Qt.Key_BracketLeft)
    assert w.edge == "start"
    before = w.plan.shots[0].end
    w.drag_edge(0, "end", before - 0.8)
    w.commit_drag()
    assert w.plan.shots[0].end == pytest.approx(before - 0.8, abs=f)   # free edit: no snapping
    edited = w.plan
    w.style_box.setCurrentIndex(w.style_box.findData("trailer_epico"))
    app.processEvents()
    assert w.plan is not edited and w.plan.style_key == "trailer_epico"
    marked = [w.style_box.itemText(i) for i in range(w.style_box.count())
              if w.style_box.itemData(i) == "cinematografico"][0]
    assert "✎" in marked and "gerado" in marked
    w.style_box.setCurrentIndex(w.style_box.findData("cinematografico"))
    app.processEvents()
    assert w.plan is edited                                       # edits kept per style
    w.save()
    assert "tudo|cinematografico|16x9" in json.load(open(store.path, encoding="utf-8"))["plans"]
    w.close()


def test_mini_editor_reorder_preview_and_colours(session, tmp_path):
    """Dragging a card moves the shot; the preview plays the film's pieces in
    order and on time; landscape colours are fixed between runs."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["DRONEHIGHLIGHTER_NO_VIDEO_PREVIEW"] = "1"
    qtw = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtw, "QApplication") or "Mock" in type(qtw.QApplication).__name__:
        pytest.skip("real PySide6 required")
    from PySide6.QtCore import QPoint
    app = qtw.QApplication.instance() or qtw.QApplication([])
    from drone import theme
    from drone.editor_window import EditorWindow, _colour
    w = EditorWindow(session, style_key="cinematografico", tag="16x9",
                     store=EditStore(str(tmp_path / "e.json")), title="T", main_style="cinematografico")
    w.resize(1400, 900)
    w.show()
    app.processEvents()
    uids = [s.uid for s in w.plan.shots]
    assert len(uids) >= 3
    # drop slot: left half of a card = before it, right half = after it
    r1 = w.strip.visualItemRect(w.strip.item(1))
    assert w.strip.drop_slot(QPoint(r1.left() + 2, r1.center().y())) == 1
    assert w.strip.drop_slot(QPoint(r1.right() - 2, r1.center().y())) == 2
    w.strip.moved.emit(0, 2)
    app.processEvents()
    assert [s.uid for s in w.plan.shots][:3] == [uids[1], uids[2], uids[0]] and w.sel == 2
    assert w.plan.edited and w.strip.count() == len(uids)
    pieces = w._sequence_pieces()
    assert [p.path for p in pieces] == [s.path for s in w.plan.shots]
    assert abs(pieces[-1].film_end - w.plan.total) < 1e-6
    assert all(abs(pieces[i + 1].film_start - (pieces[i].film_start + w.plan.advance(i))) < 1e-6
               for i in range(len(pieces) - 1))
    assert w.preview.duration == pytest.approx(w.plan.total)
    assert w.preview.piece_at(pieces[1].film_start + 0.01) == 1
    assert _colour("waterfall").name() == theme.CATEGORY_COLOURS["waterfall"]
    assert "floresta" in w.legend.text() or "■" in w.legend.text()
    w.close()


def test_working_files_go_to_the_projeto_folder(session):
    gp, plan = session.plan("tudo", "cinematografico", "16x9")
    out = session.render(gp, plan, "16x9")
    folder = os.path.dirname(out["video"])
    assert os.path.basename(folder) == os.path.basename(session.ctx.out_dir)
    assert os.path.dirname(out["edl"]) == os.path.join(folder, "projeto")
    assert os.path.dirname(out["report"]) == os.path.join(folder, "projeto")
    assert [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))
            and not f.endswith(".mp4")] == []


def test_edit_store_moves_to_projeto(tmp_path):
    from drone.pipeline import edit_store_path
    (tmp_path / "edicao_manual.json").write_text('{"version": 1, "plans": {}}', encoding="utf-8")
    p = edit_store_path(str(tmp_path))
    assert p == str(tmp_path / "projeto" / "edicao_manual.json") and os.path.exists(p)
    assert not (tmp_path / "edicao_manual.json").exists()


def test_hide_child_consoles_sets_the_flag(monkeypatch):
    from drone import win_console
    seen = {}

    def fake_init(self, *a, **kw):
        seen.update(kw)

    monkeypatch.setattr(subprocess.Popen, "__init__", fake_init)
    monkeypatch.setattr(win_console.os, "name", "nt")
    monkeypatch.setattr(win_console, "_installed", False)
    assert win_console.hide_child_consoles()
    subprocess.Popen.__init__(object.__new__(subprocess.Popen), ["x"])
    assert seen["creationflags"] & win_console.CREATE_NO_WINDOW
    seen.clear()
    subprocess.Popen.__init__(object.__new__(subprocess.Popen), ["x"], creationflags=0x10)
    assert seen["creationflags"] == 0x10                     # asked for a console: left alone


def test_dragging_a_block_with_the_mouse(session, tmp_path):
    """A real press-move-release on a block slides it, and on its edge trims it —
    also on the block that is already selected (its outline used to eat the click)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["DRONEHIGHLIGHTER_NO_VIDEO_PREVIEW"] = "1"
    qtw = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtw, "QApplication") or "Mock" in type(qtw.QApplication).__name__:
        pytest.skip("real PySide6 required")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    app = qtw.QApplication.instance() or qtw.QApplication([])
    from drone.editor_window import EditorWindow
    w = EditorWindow(session, style_key="cinematografico", tag="16x9",
                     store=EditStore(str(tmp_path / "d.json")), title="T", main_style="cinematografico")
    w.resize(1600, 960)
    w.show()
    app.processEvents()
    w._initial_sizes()
    app.processEvents()
    assert w.sel == 0
    it = w.shot_items[0]
    w.view.ensureVisible(it)
    app.processEvents()
    s0 = w.plan.shots[0].start
    c = w.view.mapFromScene(it.rect().center())
    QTest.mousePress(w.view.viewport(), Qt.LeftButton, Qt.NoModifier, c)
    for dx in range(5, 45, 5):
        QTest.mouseMove(w.view.viewport(), c + QPoint(dx, 0))
    QTest.mouseRelease(w.view.viewport(), Qt.LeftButton, Qt.NoModifier, c + QPoint(40, 0))
    app.processEvents()
    app.processEvents()
    assert w.plan.shots[0].start > s0 + 20 / w.pps          # moved with the mouse
    r = w.shot_items[0].rect()
    e = w.view.mapFromScene(r.right() - 2, r.center().y())
    end0 = w.plan.shots[0].end
    QTest.mousePress(w.view.viewport(), Qt.LeftButton, Qt.NoModifier, e)
    for dx in range(-5, -35, -5):
        QTest.mouseMove(w.view.viewport(), e + QPoint(dx, 0))
    QTest.mouseRelease(w.view.viewport(), Qt.LeftButton, Qt.NoModifier, e + QPoint(-30, 0))
    app.processEvents()
    assert w.plan.shots[0].end < end0 - 15 / w.pps
    # the film's line: a click jumps the monitor there
    w._seek_program(w.plan.cut_times()[1] + 0.1)
    assert w.preview.piece_at(w.preview.t) == 1 and w.sel == 1
    w.stop()
    assert w.preview.t == 0.0 and w.sel == 0
    w.close()


def test_capcut_folder_has_every_shot_cut_in_order(session, tmp_path):
    from drone import capcut_export
    ep = _edit(session)
    gp = session.prepare("tudo", "cinematografico")
    folder = capcut_export.export(ep, out_dir=str(tmp_path), music=gp.music, src_sizes=gp.src_sizes,
                                  title="T", mode="cpu", log_fn=lambda *_: None)
    clips = sorted(f for f in os.listdir(folder) if f.endswith(".mp4"))
    assert len(clips) == len(ep.shots) and clips[0].startswith("01_")
    lengths = [_duration(os.path.join(folder, f)) for f in clips]
    for i, d in enumerate(lengths):             # time on screen: back to back = the film
        want = ep.advance(i) if i < len(ep.shots) - 1 else ep.shots[i].length
        assert abs(d - want) < 0.12
    assert abs(sum(lengths) - ep.total) < 0.05 * len(lengths) + 0.1
    assert os.path.exists(os.path.join(folder, "ORDEM.txt"))
    if gp.music is not None:
        assert abs(_duration(os.path.join(folder, "00_musica.m4a")) - ep.total) < 0.3


def test_proxy_is_small_and_same_length(session, tmp_path, monkeypatch):
    from drone import proxy
    monkeypatch.setenv("DRONEHIGHLIGHTER_CACHE", str(tmp_path))
    clip = next(iter(_edit(session).clips.values()))
    out = proxy.make(clip.path, clip.content_hash or "abc")
    assert out and proxy.existing(clip.content_hash or "abc") == out
    assert abs(_duration(out) - _duration(clip.path)) < 0.1


def test_videos_made_by_editors_are_not_footage(tmp_path):
    from drone.ingest import is_derived
    assert is_derived("DJI_0420_DJI_0419_highlight.mp4") and is_derived("DJI_0421_highlight.MP4")
    assert not is_derived("DJI_0421.MP4") and not is_derived("highlights_da_serra.mp4")


def test_heartbeat_shows_percent_time_and_quiet_stretches(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtw = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtw, "QApplication") or "Mock" in type(qtw.QApplication).__name__:
        pytest.skip("real PySide6 required")
    qtw.QApplication.instance() or qtw.QApplication([])
    from drone import progress_ui
    bar, status, pct = qtw.QProgressBar(), qtw.QLabel(), qtw.QLabel()
    bar.setRange(0, 1000)
    now = [100.0]
    monkeypatch.setattr(progress_ui.time, "monotonic", lambda: now[0])
    hb = progress_ui.Heartbeat(bar, status, pct)
    hb.start("Analisando")
    hb.update(42.4, "Renderizando 16:9")
    assert pct.text() == "42%" and bar.value() == 424 and "Renderizando 16:9" in status.text()
    now[0] += 65
    hb._tick()
    first, second = status.text().split("\n")
    assert "1:05" in first and second.startswith("Ainda trabalhando, normal nas etapas longas") and "1:05" in second
    hb.touch()
    hb._tick()
    assert "Ainda trabalhando" not in status.text()
    hb.finish("Concluído")
    assert pct.text() == "100%" and status.text() == "Concluído" and not hb.timer.isActive()


def test_duration_is_refitted_from_the_same_analysis(session, tmp_path):
    """Editor's Duração: another length is planned from the analysis already done
    (no new analysis), shorter and longer, for every style of that format."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["DRONEHIGHLIGHTER_NO_VIDEO_PREVIEW"] = "1"
    qtw = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtw, "QApplication") or "Mock" in type(qtw.QApplication).__name__:
        pytest.skip("real PySide6 required")
    app = qtw.QApplication.instance() or qtw.QApplication([])
    from drone import analyzer, material
    from drone.editor_window import EditorWindow
    w = EditorWindow(session, style_key="cinematografico", tag="16x9",
                     store=EditStore(str(tmp_path / "t.json")), title="T", main_style="cinematografico")
    w.show()
    app.processEvents()
    est = w.gp.est
    secs = [w.dur_box.itemData(i) for i in range(w.dur_box.count())]
    assert secs == sorted(secs)
    assert any("sugerida" in w.dur_box.itemText(i) for i in range(w.dur_box.count()))
    assert "bom ~" in w.material_lbl.text() and "bruto" in w.material_lbl.text()
    calls = []
    real = analyzer.analyze_clip
    analyzer.analyze_clip = lambda *a, **k: calls.append(1) or real(*a, **k)
    try:
        long_total = w.plan.total
        assert w.set_duration(15.0, ask=False)
        short = w.plan
        assert short.total <= 15.0 + 2 * short.unit and short.total < long_total
        top = est.maximum
        assert w.set_duration(top, ask=False)
        assert w.plan.total > short.total and len(w.plan.shots) >= len(short.shots)
        # the other styles of this format follow the chosen length
        w.style_box.setCurrentIndex(w.style_box.findData("redes_sociais"))
        app.processEvents()
        assert w.targets[("tudo", "16x9")] == top and abs(w.plan.target - w.plan.total) < 0.05
    finally:
        analyzer.analyze_clip = real
    assert calls == []                       # nothing was analysed again
    w.close()


def test_material_choices_stop_at_what_the_footage_gives():
    from drone import material
    est = material.MaterialEstimate(distinct=20, strong=8, comfortable=95.0, maximum=140.0, suggested=60.0)
    assert material.choices(est) == [15.0, 30.0, 45.0, 60.0, 90.0, 120.0]
    big = material.MaterialEstimate(distinct=300, comfortable=900.0, maximum=2000.0, suggested=600.0)
    assert material.choices(big)[-1] == 1920.0 and material.clock(605) == "10:05"
    assert [material.parse_clock(x) for x in ("90", "1:30", "1m30", "2:00  ·  sugerida", "x")] == [90, 90, 90, 120, 0]


def test_redo_and_another_version_inside_the_editor(session, tmp_path):
    """Refazer / Outra versão live in the editor: re-planned from the same
    analysis; another version stays away from the shots on screen."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["DRONEHIGHLIGHTER_NO_VIDEO_PREVIEW"] = "1"
    qtw = pytest.importorskip("PySide6.QtWidgets")
    if not hasattr(qtw, "QApplication") or "Mock" in type(qtw.QApplication).__name__:
        pytest.skip("real PySide6 required")
    app = qtw.QApplication.instance() or qtw.QApplication([])
    from drone.editor_window import EditorWindow
    w = EditorWindow(session, style_key="cinematografico", tag="16x9",
                     store=EditStore(str(tmp_path / "r.json")), title="T", main_style="cinematografico",
                     target=15.0)
    w.show()
    app.processEvents()
    assert abs(w.plan.total - 15.0) <= 2 * w.plan.unit          # opened at the length chosen after the analysis
    before = [(s.path, round(s.start, 2)) for s in w.plan.shots]
    w.redo(True, wait=True)
    after = [(s.path, round(s.start, 2)) for s in w.plan.shots]
    assert after != before and w.variation == 1
    w.feedback.setText("vídeo mais longo")
    w.redo(False, wait=True)
    assert w.targets[("tudo", "16x9")] == pytest.approx(15.0 * 1.25, abs=1.5) and w.plan.total > 15.0
    w.dur_box.setEditText("0:12")
    w._duration_typed()
    assert abs(w.plan.total - 12.0) <= 2 * w.plan.unit
    w.close()
    session.readjust("", 0, [])                                   # leave the shared session as it was


def test_focus_on_a_landscape_and_on_free_text(session, tmp_path, monkeypatch):
    """Foco: a built-in landscape boosts the shots of that kind; free text goes
    through CLIP on the cached thumbnails (a fake model here) and boosts the
    matching seconds; without CLIP a keyword falls back to its category."""
    from drone import landscape
    from drone.landscape import CAT_KEYS
    gp0 = session.prepare("tudo", "cinematografico")
    cats = {c.category for c in gp0.cands}
    key = sorted(cats)[0]
    assert "🎯" in session.set_focus(landscape.LABEL_PT[key])
    gp = session.prepare("tudo", "cinematografico")
    assert gp is not gp0 and max(c.focus for c in gp.cands) == 1.0
    best = max(gp.cands, key=lambda c: c.focus)
    assert best.cat_vec[CAT_KEYS.index(key)] == max(c.cat_vec[CAT_KEYS.index(key)] for c in gp.cands)
    _, plan = session.plan("tudo", "cinematografico", "16x9")
    assert any(p.candidate.focus >= 0.6 for p in plan.shots)

    # free text with a fake CLIP: "the second clip" is the subject
    target_hash = session.analyses[1]["content_hash"]

    class FakePF:
        def infer(self, labels, images):
            import types
            n = len(images)
            hit = FakePF.current == target_hash
            logits = np.zeros((n, len(labels)))
            logits[:, 0] = 5.0 if hit else -5.0
            return types.SimpleNamespace(logits_per_image=logits)

    class FakeScorer:
        def __init__(self, *a, **k):
            self._pf = FakePF()

        def load(self):
            pass

        def unload(self):
            pass

        @staticmethod
        def available():
            return True

    real_get = session.cache.get_thumbs

    def thumbs(key):
        FakePF.current = key
        return real_get(key)

    monkeypatch.setattr(landscape, "ClipScorer", FakeScorer)
    monkeypatch.setattr(session.cache, "get_thumbs", thumbs)
    msg = session.set_focus("ponte")
    assert "ponte" in msg
    gp = session.prepare("tudo", "cinematografico")
    on = [c for c in gp.cands if c.focus > 0.5]
    assert on and all(session.analyses[c.clip]["content_hash"] == target_hash for c in on)

    # without CLIP: "igreja" falls back to the historic-town category, a word with none fails clearly
    monkeypatch.setattr(FakeScorer, "available", staticmethod(lambda: False))
    assert "cidade histórica" in session.set_focus("igreja")
    with pytest.raises(RuntimeError, match="IA de paisagens"):
        session.set_focus("piscina")
    session.set_focus("")
    assert session.focus is None
