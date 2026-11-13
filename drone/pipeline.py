# SPDX-License-Identifier: AGPL-3.0-or-later
"""Folder in, finished films out: the whole DroneHighlighter run.

Stages: ingest → per-clip analysis (cached) → landscape pass (CLIP once, then
released) → candidates → music → plan per format → render. Re-running on the
same folder skips straight to candidates, so trying another style or duration
costs only the render.

Originals are never modified, moved or deleted. Output goes to a new folder.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field

from drone import (ai_assist, analyzer, cinematic_director, edl_adapter, grouping, landscape,
                   material, music_selector, shot_selection, style_advisor)
from drone.analysis_cache import AnalysisCache
from drone.config import STYLES, DroneConfig
from drone.feedback import Adjustments, parse_feedback
from drone.ingest import scan_folder
from drone.prompt_parser import parse
from drone.visual_scoring import candidates_for_clip


@dataclass
class Job:
    folder: str
    style: str = "cinematografico"
    duration: float = 90.0                 # horizontal target (s)
    vertical_duration: float = 45.0
    horizontal: bool = True
    vertical: bool = True
    title: str = ""
    music_dir: str = ""
    music_file: str = ""
    music_tags: list = field(default_factory=list)
    priorities: list = field(default_factory=list)
    prompt: str = ""
    output_dir: str = ""
    recursive: bool = False
    horizontal_size: tuple = (3840, 2160)
    vertical_size: tuple = (1080, 1920)
    fps: int = 30
    crf: int = 18
    analyse_only: bool = False
    # "Refazer com ajustes": what to change relative to the previous version.
    feedback: str = ""
    variation: int = 0                     # >0: an alternative version of the same edit
    previous_report: str = ""              # report JSON of the version being redone
    # Length: duration / vertical_duration = 0 means "automática" (from the material).
    shortfall: str = "esticar"             # not enough material: perguntar | encurtar | esticar
    group_by: str = "nada"                 # nada | voo | dia | local | subpasta (drone.grouping)
    summary: bool = False                  # with group_by: also a film of the whole trip

    def apply_prompt(self, log_fn=print) -> None:
        p = parse(self.prompt)
        if p.style:
            self.style = p.style
        if p.duration:
            self.duration = p.duration
        if p.title and not self.title:
            self.title = p.title
        for x in p.priorities:
            if x not in self.priorities:
                self.priorities.append(x)
        for x in p.music_tags:
            if x not in self.music_tags:
                self.music_tags.append(x)
        if p.horizontal is not None:
            self.horizontal = p.horizontal
        if p.vertical is not None:
            self.vertical = p.vertical
        if self.prompt.strip():
            log_fn(f"📝 Instruções: estilo={self.style}, {self.duration:.0f}s, "
                   f"prioridades={self.priorities or '-'}, música={self.music_tags or '-'}, "
                   f"suave={p.smooth}")
        self._smooth = p.smooth


@dataclass
class Result:
    outputs: list = field(default_factory=list)
    edls: list = field(default_factory=list)
    report: str = ""
    analysed: int = 0
    cached: int = 0
    seconds: float = 0.0


class Progress:
    """Maps stage-local progress to one overall 0..100 number."""
    STAGES = [("ingest", 3), ("analysis", 55), ("landscape", 8), ("plan", 4), ("render", 30)]

    def __init__(self, fn):
        self.fn = fn
        self.base = {}
        acc = 0
        for name, w in self.STAGES:
            self.base[name] = (acc, w)
            acc += w

    def __call__(self, stage: str, done: float, total: float, text: str = ""):
        if not self.fn:
            return
        a, w = self.base[stage]
        frac = 0.0 if not total else max(0.0, min(1.0, done / total))
        self.fn(a + w * frac, text)


def _setup(job: Job, cfg: DroneConfig, log_fn):
    """Instructions, feedback and the optional AI → adjustments applied to the job."""
    job.apply_prompt(log_fn)
    ai_client = ai_assist.AIClient(cfg.ai, log_fn) if cfg.ai.enabled else None
    if ai_client is not None:
        log_fn(f"🤖 Assistente de IA ligado: {ai_client.describe()}")
    adj = resolve_adjustments(job, cfg, ai_client, log_fn)
    apply_adjustments(job, adj, log_fn)
    if getattr(job, "_smooth", False) or adj.smooth:
        cfg.weights.smoothness *= 1.3
        cfg.weights.movement *= 1.1
    ai_log = {"enabled": bool(ai_client), "provider": cfg.ai.provider if ai_client else "",
              "model": ai_client.model if ai_client else "", "events": []}
    return adj, ai_client, ai_log, load_previous_shots(job.previous_report)


def _analyse(job: Job, cfg: DroneConfig, cache: AnalysisCache, prog: Progress, res: Result,
             log_fn, cancel_check) -> tuple[list, str]:
    """Steps 1–3: ingest, per-clip analysis (cached), landscape pass."""
    if job.group_by == "subpasta":
        job.recursive = True
    clips = scan_folder(job.folder, recursive=job.recursive, log_fn=log_fn,
                        progress_fn=lambda i, n, s: prog("ingest", i, n, s))
    if not clips:
        raise RuntimeError("nenhum vídeo MP4/MOV encontrado na pasta")
    for c in clips:
        for w in c.warnings:
            log_fn(f"ℹ️ {c.name}: {w}")
    analyses = []
    total_dur = sum(c.info.duration for c in clips) or 1.0
    done_dur = 0.0
    for c in clips:
        if cancel_check is not None and cancel_check():
            raise RuntimeError("cancelado")
        hit = cache.get(c.path, cfg.analysis.key())
        if hit is not None:
            a = hit
            res.cached += 1
        else:
            def p(i, n, name, _d=done_dur, _c=c):
                prog("analysis", _d + _c.info.duration * i / max(1, n), total_dur, f"Analisando {name}")
            try:
                a = analyzer.analyze_clip(c, cfg, cache, log_fn=log_fn, progress_fn=p,
                                          cancel_check=cancel_check)
                res.analysed += 1
            except RuntimeError as exc:
                if "cancel" in str(exc):
                    raise
                log_fn(f"⚠️ {c.name}: {exc}")
                continue
        a["order"] = c.order
        analyses.append(a)
        done_dur += c.info.duration
        prog("analysis", done_dur, total_dur, c.name)
    log_fn(f"✅ Análise: {res.analysed} novo(s), {res.cached} do cache")
    backend = landscape.annotate(analyses, cache, use_clip=cfg.use_clip, log_fn=log_fn,
                                 progress_fn=lambda i, n, s: prog("landscape", i, n, s))
    log_fn(f"🏞️ Classificação de paisagem: {backend}")
    return analyses, backend


def _naming(job: Job) -> tuple[str, str, str]:
    out_dir = job.output_dir or os.path.join(job.folder, "_dh_editados")
    base = (job.title or os.path.basename(os.path.normpath(job.folder)) or "drone")
    safe = re.sub(r"[^\w-]+", "_", base, flags=re.UNICODE).strip("_") or "drone"
    return out_dir, time.strftime("%Y%m%d_%H%M%S"), safe


def run(job: Job, cfg: DroneConfig | None = None, *, cache: AnalysisCache | None = None,
        log_fn=print, progress_fn=None, cancel_check=None, decide_fn=None) -> Result:
    """``decide_fn(info) -> 'encurtar' | 'esticar' | 'cancelar'`` (or a tuple
    ``(answer, remember_for_next_films)``) is called when ``job.shortfall`` is
    'perguntar' and the material cannot fill the requested length."""
    t_start = time.time()
    cfg = cfg or DroneConfig.load()
    cache = cache or AnalysisCache()
    prog = Progress(progress_fn)
    adj, ai_client, ai_log, previous = _setup(job, cfg, log_fn)
    res = Result()
    analyses, backend = _analyse(job, cfg, cache, prog, res, log_fn, cancel_check)

    # 4. one or several films: everything, or per flight / day / place / subfolder
    groups = grouping.make_groups(analyses, job.group_by, root=job.folder, summary=job.summary,
                                  log_fn=log_fn)
    out_dir, stamp, safe = _naming(job)
    report = {"job": {k: v for k, v in asdict(job).items() if k != "prompt"}, "prompt": job.prompt,
              "landscape_backend": backend, "feedback": job.feedback,
              "adjustments": {k: v for k, v in asdict(adj).items() if k != "order"},
              "ai": ai_log, "groups": []}
    ctx = _RunContext(job=job, cfg=cfg, cache=cache, adj=adj, ai_client=ai_client, ai_log=ai_log,
                      previous=previous, prog=prog, log_fn=log_fn, cancel_check=cancel_check,
                      decide_fn=decide_fn, out_dir=out_dir, stamp=stamp, safe=safe,
                      n_sets=len(groups), res=res)
    for gi, g in enumerate(groups):
        if cancel_check is not None and cancel_check():
            raise RuntimeError("cancelado")
        if len(groups) > 1:
            log_fn(f"\n━━━ {g.label} ━━━")
        section = _make_set(ctx, gi, g, [analyses[i] for i in g.indices])
        if section is None:
            continue
        report["groups"].append(section)
        if job.analyse_only:
            res.report = section.get("ranking", "")
            res.seconds = time.time() - t_start
            return res
    if not report["groups"]:
        raise RuntimeError("nenhum vídeo pôde ser montado (material insuficiente em todos os grupos)")
    # Single film (or the whole-trip summary): also at the top level, as before.
    main = next((s for s in report["groups"] if s["key"] == "tudo"), report["groups"][0])
    for k in ("style", "weights", "auto_style", "music", "material", "formats"):
        report[k] = main.get(k)
    res.report = _write_report(report, out_dir, safe, stamp)
    res.seconds = time.time() - t_start
    if progress_fn:
        progress_fn(100.0, "Concluído")
    log_fn(f"🏁 Concluído em {res.seconds:.0f}s: {len(res.outputs)} vídeo(s) → {out_dir}")
    return res


def project_dir(out_dir: str) -> str:
    """Where the working files go: ``_dh_editados/projeto``."""
    return os.path.join(out_dir, edl_adapter.PROJECT_DIR)


def edit_store_path(out_dir: str) -> str:
    """``projeto/edicao_manual.json`` — moved there from older versions' place."""
    new = os.path.join(project_dir(out_dir), "edicao_manual.json")
    old = os.path.join(out_dir, "edicao_manual.json")
    if not os.path.exists(new) and os.path.exists(old):
        try:
            os.makedirs(os.path.dirname(new), exist_ok=True)
            os.replace(old, new)
        except OSError:
            return old
    return new


def _write_report(report: dict, out_dir: str, safe: str, stamp: str) -> str:
    folder = project_dir(out_dir)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{safe}_{stamp}_relatorio.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    return path


@dataclass
class _RunContext:
    job: Job
    cfg: DroneConfig
    cache: AnalysisCache
    adj: Adjustments
    ai_client: object
    ai_log: dict
    previous: list
    prog: Progress
    log_fn: object
    cancel_check: object
    decide_fn: object
    out_dir: str
    stamp: str
    safe: str
    n_sets: int
    res: Result
    remembered: str = ""          # shortfall answer the user chose to reuse


def _resolve_shortfall(ctx: _RunContext, info: dict) -> str:
    """'encurtar' | 'esticar' | 'cancelar' for a film the material cannot fill."""
    policy = ctx.job.shortfall
    if policy in ("encurtar", "esticar"):
        return policy
    if ctx.remembered:
        return ctx.remembered
    if ctx.decide_fn is None:
        return "esticar"
    answer = ctx.decide_fn(info) or "esticar"
    if isinstance(answer, tuple):
        answer, remember = answer
        if remember:
            ctx.remembered = answer
    return answer


@dataclass
class GroupPrep:
    """Everything needed to plan any format of one group in one style."""
    group: object
    analyses: list
    style: object                  # the Style actually used (Automático resolved)
    style_key: str                 # what was asked ("automatico" stays "automatico")
    weights: object
    run_style: object
    auto: dict | None
    music: object
    unit: float
    cands: list
    est: object
    src_sizes: dict

    @property
    def by_uid(self) -> dict:
        return {c.uid: c for c in self.cands}


def prepare_group(ctx: _RunContext, group, analyses: list, style_key: str = "") -> GroupPrep:
    """Style (Automático resolved), music, candidates and material for one group."""
    job, cfg, log_fn, adj = ctx.job, ctx.cfg, ctx.log_fn, ctx.adj
    style_key = style_key or job.style
    style = STYLES.get(style_key) or STYLES["cinematografico"]
    auto = None
    if style_key == "automatico":
        only_v = job.vertical and not job.horizontal
        key, scores, _p = style_advisor.choose(
            analyses, cfg, vertical_only=only_v,
            target=(job.vertical_duration if only_v else job.duration) or 90.0, log_fn=log_fn)
        auto = {"chosen": key, "scores": scores}
        style = dataclasses.replace(STYLES[key], adaptive=True)
    weights = cfg.weights_for(style)

    # Music: with an automatic length the final duration is not known yet, so a
    # long track is preferred (it is trimmed and faded to the film anyway).
    music = None
    if job.horizontal or job.vertical:
        wanted = [d for d, on in ((job.duration, job.horizontal), (job.vertical_duration, job.vertical)) if on]
        longest = max(wanted) if wanted and all(wanted) else 180.0
        tags = job.music_tags or []
        music = music_selector.choose(job.music_dir, target=longest, bpm_range=style.bpm_range,
                                      tags=tuple(tags) + style.music_tags, cache=ctx.cache,
                                      explicit=job.music_file, seed=job.folder + group.key, log_fn=log_fn)
        if music:
            log_fn(f"🎶 Música: {os.path.basename(music.path)} ({music.bpm:.0f} BPM; {music.reason})")
        else:
            log_fn("ℹ️ Sem música (biblioteca vazia): cortes em grade fixa, áudio original removido")
    unit = music.unit() if music else 0.5
    run_style = cinematic_director.adjusted_style(style, adj)
    body = cinematic_director._beats_for(unit, run_style.shot_min, run_style.shot_max) * unit
    cand_len = body + run_style.transition_duration
    cands = []
    for i, a in enumerate(analyses):
        cands += candidates_for_clip(i, a, cand_len * 1.25, cfg, weights, priorities=job.priorities)
    for i, c in enumerate(cands):
        c.uid = f"S{i + 1}"
    log_fn(f"🎞️ {len(cands)} trechos candidatos em {len(analyses)} vídeo(s)")
    est = material.estimate(cands, run_style, cfg, weights) if cands else material.MaterialEstimate()
    return GroupPrep(group=group, analyses=analyses, style=style, style_key=style_key,
                     weights=weights, run_style=run_style, auto=auto, music=music, unit=unit,
                     cands=cands, est=est,
                     src_sizes={a["path"]: _display_size(a) for a in analyses})


def plan_format(ctx: _RunContext, gp: GroupPrep, tag: str, vertical: bool):
    """The cut list of one format (no rendering). None when nothing usable.

    Returns (plan, target, automatic_length)."""
    job, cfg, log_fn, est = ctx.job, ctx.cfg, ctx.log_fn, gp.est
    asked = job.vertical_duration if vertical else job.duration
    target, auto_len = (asked or (est.suggested_vertical if vertical else est.suggested)), not asked
    if auto_len:
        log_fn(f"⏱️ Duração automática {tag}: {target:.0f} s")
    elif material.shortfall(target, est, gp.unit):
        info = {"group": gp.group.label, "format": tag, "asked": target,
                "comfortable": est.comfortable, "maximum": est.maximum,
                "shorter": max(10.0, material._floor5(est.comfortable))}
        log_fn(f"⚠️ Pouco material para {target:.0f} s ({tag}): ~{est.comfortable:.0f} s em ritmo "
               f"normal, até ~{est.maximum:.0f} s esticando")
        choice = _resolve_shortfall(ctx, info)
        if choice == "cancelar":
            raise RuntimeError("cancelado")
        if choice == "encurtar":
            target = info["shorter"]
            log_fn(f"✂️ Encurtando o vídeo para {target:.0f} s")
        else:
            log_fn("↔️ Esticando as tomadas e aceitando cenas mais parecidas para chegar perto do pedido")
    fmt_adj = ctx.adj
    if ctx.ai_client is not None and cfg.ai.direct:
        fmt_adj = ai_direct(ctx.ai_client, gp.cands, ctx.adj, job, cfg, gp.run_style, gp.weights,
                            gp.unit, target, vertical, ctx.previous, ctx.ai_log,
                            f"{gp.group.key}/{tag}", log_fn)
    try:
        plan = cinematic_director.direct(gp.cands, target=target, style=gp.style, weights=gp.weights,
                                         cfg=cfg, music=gp.music, title=job.title,
                                         vertical=vertical, adj=fmt_adj, previous=ctx.previous,
                                         log_fn=log_fn)
    except RuntimeError as exc:
        log_fn(f"⚠️ {gp.group.label} {tag}: {exc}")
        return None
    log_fn(f"🎬 Roteiro {tag}:\n{plan.describe()}")
    if plan.total + 0.5 < target:
        log_fn(f"ℹ️ Saiu com {plan.total:.0f} s (pedido {target:.0f} s): não há mais material distinto")
    return plan, target, auto_len


def render_plan(ctx: _RunContext, gp: GroupPrep, plan, tag: str, vertical: bool, prefix: str,
                progress_fn=None) -> tuple[str, str]:
    """Render one planned (or hand-edited) cut list with its music."""
    job, cfg = ctx.job, ctx.cfg
    os.makedirs(ctx.out_dir, exist_ok=True)
    out = os.path.join(ctx.out_dir, f"{prefix}_{tag}_{ctx.stamp}.mp4")
    music_path = ""
    if gp.music:
        # Kept next to the video: the saved EDL points at it, so the cut can be
        # reopened and re-rendered in VideoHighlighter's editor.
        music_path = music_selector.prepare(
            gp.music, plan.total, edl_adapter.side_path(out, "_musica.m4a"))
    if vertical:
        return edl_adapter.render_vertical(
            plan, out, title=job.title, size=job.vertical_size, fps=job.fps, crf=job.crf,
            music_path=music_path, src_sizes=gp.src_sizes, mode=cfg.render_mode,
            log_fn=ctx.log_fn, progress_fn=progress_fn, cancel_check=ctx.cancel_check)
    return edl_adapter.render_horizontal(
        plan, out, title=job.title, size=job.horizontal_size, fps=job.fps, crf=job.crf,
        music_path=music_path, mode=cfg.render_mode, log_fn=ctx.log_fn,
        progress_fn=progress_fn, cancel_check=ctx.cancel_check)


def shots_report(plan) -> list:
    return [{"uid": s.candidate.uid, "source": s.candidate.path, "in": s.start, "out": s.end,
             "role": s.slot.role, "category": s.candidate.category,
             "movement": s.candidate.movement, "score": round(s.candidate.base, 4),
             "parts": {k: round(v, 3) for k, v in s.candidate.parts.items()},
             "transition": s.slot.transition, "crop_x": round(s.crop_x, 3)}
            for s in plan.shots]


def _make_set(ctx: _RunContext, gi: int, group, analyses: list) -> dict | None:
    """Plan and render the film(s) of one group. Returns its report section."""
    job, log_fn = ctx.job, ctx.log_fn
    gp = prepare_group(ctx, group, analyses)
    ctx.prog("plan", 1, 2, "Selecionando tomadas")
    music = gp.music
    section = {"key": group.key, "label": group.label, "clips": [a["path"] for a in analyses],
               "style": gp.style.key, "weights": asdict(gp.weights), "auto_style": gp.auto,
               "music": music and {"path": music.path, "bpm": music.bpm, "start": music.start},
               "formats": {}}
    if job.analyse_only:
        section["ranking"] = "\n".join(c.describe() for c in sorted(gp.cands, key=lambda c: -c.base)[:40])
        return section
    if not gp.cands:
        log_fn(f"⚠️ {group.label}: nenhum trecho utilizável — grupo ignorado")
        return None
    section["material"] = asdict(gp.est)
    log_fn(f"📏 Material: {gp.est.describe()}")
    prefix = ctx.safe + (f"_{group.slug}" if group.slug else "")
    formats = [f for f in (("16x9", False) if job.horizontal else None,
                           ("9x16", True) if job.vertical else None) if f]
    for n, (tag, vertical) in enumerate(formats):
        if ctx.cancel_check is not None and ctx.cancel_check():
            raise RuntimeError("cancelado")
        planned = plan_format(ctx, gp, tag, vertical)
        if planned is None:
            continue
        plan, target, auto_len = planned
        step = gi * len(formats) + n
        total_steps = ctx.n_sets * max(1, len(formats))
        rp = lambda i, k, *s, _s=step: ctx.prog("render", _s + (i / max(1, k)), total_steps,  # noqa: E731
                                                f"Renderizando {group.label} {tag}")
        video, edl = render_plan(ctx, gp, plan, tag, vertical, prefix, rp)
        ctx.res.outputs.append(video)
        ctx.res.edls.append(edl)
        section["formats"][tag] = {"output": video, "edl": edl, "duration": plan.total,
                                   "asked": target, "automatic_length": auto_len,
                                   "shots": shots_report(plan)}
        log_fn(f"✅ {group.label} {tag}: {video}")
    return section


class Session:
    """Analysis done once; any style/format can then be planned without
    rendering — the basis of the manual-edit timeline.

    ``Session(job, cfg)`` runs ingest, analysis and the landscape pass (cached),
    ``plan(group_key, style_key, tag)`` returns ``(GroupPrep, Plan)`` and
    ``render(gp, plan, tag)`` renders a (possibly hand-edited) plan.
    """

    def __init__(self, job: Job, cfg: DroneConfig | None = None, *, cache: AnalysisCache | None = None,
                 log_fn=print, progress_fn=None, cancel_check=None):
        self.job = job
        self.cfg = cfg or DroneConfig.load()
        self.cache = cache or AnalysisCache()
        self.log_fn = log_fn
        prog = Progress(progress_fn)
        adj, ai_client, ai_log, previous = _setup(job, self.cfg, log_fn)
        self.res = Result()
        self.analyses, self.backend = _analyse(job, self.cfg, self.cache, prog, self.res, log_fn, cancel_check)
        self.groups = grouping.make_groups(self.analyses, job.group_by, root=job.folder,
                                           summary=job.summary, log_fn=log_fn)
        out_dir, stamp, safe = _naming(job)
        # Editing never asks about missing footage: what is short is visible on the timeline.
        self.ctx = _RunContext(job=job, cfg=self.cfg, cache=self.cache, adj=adj, ai_client=ai_client,
                               ai_log=ai_log, previous=previous, prog=prog, log_fn=log_fn,
                               cancel_check=cancel_check, decide_fn=None, out_dir=out_dir,
                               stamp=stamp, safe=safe, n_sets=1, res=self.res)
        self._preps: dict = {}

    def set_log(self, fn) -> None:
        """Send this session's log lines somewhere else (the editor, a render worker)."""
        self.log_fn = fn
        self.ctx.log_fn = fn

    def thumbs(self, content_hash: str) -> list:
        return self.cache.get_thumbs(content_hash) if content_hash else []

    def group(self, key: str):
        return next(g for g in self.groups if g.key == key)

    def prepare(self, group_key: str, style_key: str) -> GroupPrep:
        k = (group_key, style_key)
        if k not in self._preps:
            g = self.group(group_key)
            self._preps[k] = prepare_group(self.ctx, g, [self.analyses[i] for i in g.indices], style_key)
        return self._preps[k]

    def plan(self, group_key: str, style_key: str, tag: str):
        gp = self.prepare(group_key, style_key)
        if not gp.cands:
            return gp, None
        planned = plan_format(self.ctx, gp, tag, tag == "9x16")
        return gp, (planned[0] if planned else None)

    def render(self, gp: GroupPrep, plan, tag: str, *, progress_fn=None, cancel_check=None) -> dict:
        self.ctx.stamp = time.strftime("%Y%m%d_%H%M%S")
        if cancel_check is not None:
            self.ctx.cancel_check = cancel_check
        prefix = self.ctx.safe + (f"_{gp.group.slug}" if gp.group.slug else "") + "_manual"
        video, edl = render_plan(self.ctx, gp, plan, tag, tag == "9x16", prefix, progress_fn)
        section = {"key": gp.group.key, "label": gp.group.label, "style": gp.style.key,
                   "auto_style": gp.auto, "manual": True,
                   "music": gp.music and {"path": gp.music.path, "bpm": gp.music.bpm, "start": gp.music.start},
                   "formats": {tag: {"output": video, "edl": edl, "duration": plan.total,
                                     "shots": shots_report(plan)}}}
        report = {"job": {k: v for k, v in asdict(self.job).items() if k != "prompt"},
                  "manual_edit": True, "groups": [section], **{k: section.get(k) for k in
                                                               ("style", "auto_style", "music", "formats")}}
        rep = _write_report(report, self.ctx.out_dir, self.ctx.safe + "_manual", self.ctx.stamp)
        self.log_fn(f"✅ Edição manual renderizada: {video}")
        return {"video": video, "edl": edl, "report": rep}


def resolve_adjustments(job: Job, cfg: DroneConfig, ai_client, log_fn) -> Adjustments:
    """Keyword reading always; the AI (if switched on) refines it. AI failure
    is logged and the keyword result stands."""
    adj = parse_feedback(job.feedback)
    if job.variation:
        adj.variation = job.variation
    if job.feedback.strip() and adj.notes:
        log_fn("🔁 Ajustes entendidos: " + "; ".join(adj.notes))
    text = "\n".join(x for x in (job.prompt.strip() and f"Instruções: {job.prompt.strip()}",
                                 job.feedback.strip() and f"Ajustes pedidos após ver o vídeo: {job.feedback.strip()}")
                     if x)
    if ai_client is not None and cfg.ai.interpret and text:
        current = {"style": job.style, "duration": job.duration,
                   "vertical_duration": job.vertical_duration, "title": job.title,
                   "priorities": job.priorities}
        try:
            ai_adj = ai_assist.interpret(ai_client, text, current=current)
            adj = adj.merge(ai_adj)
            log_fn("🤖 IA entendeu: " + ("; ".join(ai_adj.notes) or "(sem observações)"))
        except ai_assist.AIError as exc:
            log_fn(f"⚠️ IA não pôde interpretar ({exc}); usando leitura por palavras-chave")
    return adj


def apply_adjustments(job: Job, adj: Adjustments, log_fn=print) -> None:
    if adj.style in STYLES:
        job.style = adj.style
    if adj.duration:
        job.duration = adj.duration
    elif adj.duration_scale != 1.0:
        job.duration = round(job.duration * adj.duration_scale)
        job.vertical_duration = round(job.vertical_duration * adj.duration_scale)
    if adj.vertical_duration:
        job.vertical_duration = adj.vertical_duration
    for c in adj.prefer:
        if c not in job.priorities:
            job.priorities.append(c)
    job.priorities = [c for c in job.priorities if c not in adj.avoid_categories]
    if adj.title and not job.title:
        job.title = adj.title
    for t in adj.music_tags:
        if t not in job.music_tags:
            job.music_tags.append(t)


def load_previous_shots(report_path: str) -> list:
    """(source, in, out) of every shot in a previous version's report."""
    if not report_path or not os.path.exists(report_path):
        return []
    try:
        with open(report_path, "r", encoding="utf-8") as fh:
            rep = json.load(fh)
    except Exception:
        return []
    out = []
    sections = rep.get("groups") or [rep]
    for sec in sections:
        for fmt in (sec.get("formats") or {}).values():
            for sh in fmt.get("shots", []):
                out.append((sh["source"], float(sh["in"]), float(sh["out"])))
    return list(dict.fromkeys(out))


def ai_direct(client, cands, adj, job, cfg, run_style, weights, unit, target, vertical,
              previous, ai_log, tag, log_fn) -> Adjustments:
    """Let the AI cast and order the shots from a diverse shortlist.

    The shortlist is built by the normal selector (so it is already free of
    shaky, blurry and duplicate material); the AI only chooses among good
    options. Any failure returns the adjustments unchanged."""
    n = len(cinematic_director.plan_slots(target, run_style, unit))
    bonus = cinematic_director.make_bonus(vertical=vertical, adj=adj, previous=previous,
                                          style=run_style)
    k = min(len(cands), max(2 * n, cfg.ai.max_candidates))
    shortlist = shot_selection.select(cands, k, w_variety=weights.variety,
                                      duplicate_threshold=cfg.duplicate_threshold,
                                      max_per_clip=max(cfg.max_shots_per_clip, 4),
                                      extra_bonus=bonus, log_fn=lambda *_: None)
    if len(shortlist) < 2:
        return adj
    where = "no seu PC" if client.p.local else "para a nuvem"
    log_fn(f"🤖 Enviando {where} a descrição em texto de {len(shortlist)} trechos "
           f"(sem imagens nem vídeo) para escolher {n} tomadas ({tag})…")
    request = "\n".join(x for x in (job.prompt, job.feedback) if x and x.strip())
    try:
        order, reason = ai_assist.direct(client, shortlist, n,
                                         style_label=run_style.label, request=request)
    except ai_assist.AIError as exc:
        log_fn(f"⚠️ IA não montou a sequência ({exc}); usando o diretor automático")
        ai_log["events"].append({"format": tag, "ok": False, "error": str(exc)})
        return adj
    log_fn(f"🤖 Sequência da IA: {' '.join(order)} — {reason}")
    ai_log["events"].append({"format": tag, "ok": True, "order": order, "reason": reason})
    return dataclasses.replace(adj, order=order)


def _display_size(a: dict) -> tuple[int, int]:
    return int(a.get("width") or 1920), int(a.get("height") or 1080)
