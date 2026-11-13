# SPDX-License-Identifier: AGPL-3.0-or-later
"""All tunable numbers in one place: thresholds, score weights, edit styles.

Every value here is an *initial development value*, chosen on synthetic test
footage, not a validated constant. They are meant to be calibrated on real DJI
footage; ``DroneConfig.load``/``save`` keep a user copy as JSON so calibration
survives updates.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field


@dataclass
class AnalysisParams:
    """Changing any of these invalidates the analysis cache (they are its key)."""
    sample_hz: float = 4.0          # frame pairs per second of footage
    width: int = 480                # analysis width in pixels
    thumb_every: float = 1.0        # seconds between thumbnails kept for CLIP

    def key(self) -> dict:
        return asdict(self)


@dataclass
class MotionThresholds:
    # Units: frame-widths per second (a value of 0.1 = 10% of the width per s).
    hover_speed: float = 0.012
    comfort_speed: float = 0.22
    fast_speed: float = 0.60
    zoom_rate: float = 0.025         # |log scale| per second
    parallax_orbit: float = 0.010
    jitter_ok: float = 0.020
    jitter_bad: float = 0.12
    jitter_reject: float = 0.25      # p80 jitter above this: the window is discarded
    jerk_ok: float = 0.05
    jerk_bad: float = 0.40


@dataclass
class ScoreWeights:
    """Spec section 3 initial weights (sum need not be 1; they are normalised)."""
    quality: float = 0.30            # visual quality and composition
    smoothness: float = 0.25         # smoothness and stability
    interest: float = 0.20           # landscape interest
    movement: float = 0.15           # cinematic movement
    variety: float = 0.10            # variety / originality (applied at selection)

    def normalised(self) -> "ScoreWeights":
        s = self.quality + self.smoothness + self.interest + self.movement + self.variety
        s = s or 1.0
        return ScoreWeights(self.quality / s, self.smoothness / s, self.interest / s,
                            self.movement / s, self.variety / s)


@dataclass
class Style:
    key: str
    label: str
    shot_min: float                  # seconds on screen per shot
    shot_max: float
    transition: str                  # a modules.media.transitions kind
    transition_duration: float
    bpm_range: tuple = (60, 140)
    music_tags: tuple = ()
    default_duration: float = 90.0
    weight_overrides: dict = field(default_factory=dict)
    # Fraction of joins that use the transition; the rest are hard cuts on the beat.
    transition_share: float = 1.0
    description: str = ""
    prefer: tuple = ()               # landscape categories this style favours
    opener: str = ""                 # default first shot (category or movement)
    closer: str = ""                 # default last shot
    # Shot-length multipliers along the film, start → end (interpolated).
    # (1.6, 1.0, 0.6) = long establishing shots that accelerate into quick cuts.
    pace_curve: tuple = ()
    lively: bool = False             # vary lengths ±1 beat for a less mechanical rhythm
    chronological: float = 0.0       # 0..1: how strongly to keep shooting order
    movement_bias: dict = field(default_factory=dict)   # e.g. {"orbit": 1.3}
    adaptive: bool = False           # each shot's length follows its own character


_CALM = ("ambient", "tranquil", "calm", "calma", "piano", "chill", "instrument")

STYLES: dict[str, Style] = {
    "automatico": Style(
        "automatico", "Automático (analisa as cenas e escolhe)", 3.5, 6.5, "crossfade", 0.6,
        bpm_range=(60, 140), default_duration=90, transition_share=0.5, adaptive=True,
        description="Lê as paisagens, os movimentos, a luz e a quantidade de material e "
                    "escolhe o estilo mais adequado; o ritmo se adapta a cada tomada."),
    "cinematografico": Style(
        "cinematografico", "Cinematográfico", 3.5, 6.5, "crossfade", 0.6,
        bpm_range=(70, 120), music_tags=("cinemat", "instrument", "epic", "orquest", "piano"),
        default_duration=90, transition_share=0.5, adaptive=True,
        description="Equilíbrio entre impacto e respiro; fusões alternadas com cortes na batida."),
    "contemplativo": Style(
        "contemplativo", "Natureza contemplativa", 5.0, 9.0, "crossfade", 1.0,
        bpm_range=(55, 100), music_tags=_CALM,
        default_duration=120, transition_share=1.0, adaptive=True,
        weight_overrides={"smoothness": 0.32, "movement": 0.10},
        prefer=("waterfall", "mountains", "lake", "river", "forest"), closer="pull_out",
        description="Tomadas longas e suaves, fusões lentas, paisagens naturais."),
    "por_do_sol": Style(
        "por_do_sol", "Pôr do sol / golden hour", 5.5, 9.0, "crossfade", 1.2,
        bpm_range=(55, 95), music_tags=_CALM + ("sunset", "lofi"),
        default_duration=60, transition_share=1.0, adaptive=True,
        weight_overrides={"interest": 0.26, "smoothness": 0.28},
        prefer=("sunset", "coast", "lake", "mountains"), closer="sunset",
        pace_curve=(1.0, 1.0, 1.2),
        description="Luz dourada: dissolves longos, termina no pôr do sol, ritmo que desacelera."),
    "minimalista": Style(
        "minimalista", "Minimalista", 7.0, 12.0, "crossfade", 1.5,
        bpm_range=(50, 90), music_tags=_CALM + ("minimal",),
        default_duration=60, transition_share=1.0, adaptive=True,
        weight_overrides={"quality": 0.35, "smoothness": 0.32, "movement": 0.05},
        movement_bias={"hover": 1.5, "pan": 1.3, "fast": 0.3},
        description="Poucas tomadas muito longas e estáveis; cada imagem respira."),
    "cidade_historica": Style(
        "cidade_historica", "Cidade histórica / arquitetura", 3.5, 6.0, "crossfade", 0.6,
        bpm_range=(70, 115), music_tags=("instrument", "acoust", "acúst", "folk", "piano", "orquest"),
        default_duration=75, transition_share=0.4, adaptive=True, chronological=0.2,
        prefer=("historic", "city"), opener="rise_reveal", closer="pull_out",
        movement_bias={"orbit": 1.3, "rise_reveal": 1.2},
        description="Órbitas em torno de igrejas e casarios, revelações do centro histórico."),
    "diario_viagem": Style(
        "diario_viagem", "Diário de viagem (ordem cronológica)", 3.0, 5.5, "crossfade", 0.5,
        bpm_range=(85, 125), music_tags=("indie", "acoust", "folk", "alternat", "travel"),
        default_duration=90, transition_share=0.35, adaptive=True, chronological=1.0,
        closer="pull_out",
        description="Conta a viagem na ordem em que foi filmada, como um diário."),
    "trailer_epico": Style(
        "trailer_epico", "Trailer épico", 2.0, 5.0, "crossfade", 0.4,
        bpm_range=(80, 140), music_tags=("epic", "épic", "trailer", "cinemat", "orquest", "score"),
        default_duration=60, transition_share=0.3,
        weight_overrides={"interest": 0.26, "movement": 0.20},
        opener="rise_reveal", closer="pull_out",
        pace_curve=(1.7, 1.3, 1.0, 0.75, 0.6, 0.55),
        description="Começa lento e grandioso e acelera até cortes rápidos; fecha com um afastamento."),
    "viagem_dinamica": Style(
        "viagem_dinamica", "Viagem dinâmica", 1.8, 3.6, "cut", 0.0,
        bpm_range=(100, 150), music_tags=("indie", "alternat", "upbeat", "pop", "rock", "eletr", "electr"),
        default_duration=60, transition_share=0.0, lively=True,
        weight_overrides={"movement": 0.22, "smoothness": 0.20},
        description="Cortes curtos na batida, muita variedade de lugares."),
    "aventura_fpv": Style(
        "aventura_fpv", "Aventura / FPV radical", 1.2, 2.6, "cut", 0.0,
        bpm_range=(120, 175), music_tags=("rock", "eletr", "electr", "drum", "energ", "dubstep", "trap"),
        default_duration=45, transition_share=0.0, lively=True,
        weight_overrides={"movement": 0.28, "smoothness": 0.15},
        movement_bias={"flyover": 1.4, "fast": 3.0, "hover": 0.5},
        opener="flyover",
        description="Velocidade: sobrevoos rasantes e movimentos rápidos, cortes secos curtos."),
    "redes_sociais": Style(
        "redes_sociais", "Vídeo curto para redes sociais", 1.5, 3.0, "cut", 0.0,
        bpm_range=(95, 150), music_tags=("upbeat", "pop", "eletr", "electr", "indie"),
        default_duration=30, transition_share=0.0, lively=True,
        weight_overrides={"interest": 0.26, "movement": 0.20},
        description="Gancho forte no primeiro segundo e cortes rápidos para Reels/Shorts."),
}

# Concept from SkyGrid's per-camera "source profiles", reimplemented: what a
# camera model's footage typically needs. Used for warnings and defaults only.
DJI_PROFILES = {
    "mini 2": {"gimbal": True, "log": False},
    "mini 3": {"gimbal": True, "log": True},
    "mini 4": {"gimbal": True, "log": True},
    "air 2s": {"gimbal": True, "log": True},
    "air 3": {"gimbal": True, "log": True},
    "mavic 3": {"gimbal": True, "log": True},
    "avata": {"gimbal": False, "log": True},   # FPV: roll is intentional
}


@dataclass
class OutputSpec:
    horizontal: bool = True
    vertical: bool = True
    horizontal_size: tuple = (3840, 2160)
    vertical_size: tuple = (1080, 1920)
    fps: int = 30
    crf: int = 18
    horizontal_duration: float = 90.0
    vertical_duration: float = 45.0


@dataclass
class AISettings:
    """Optional AI assistant (drone.ai_assist). Disabled unless the user turns it on."""
    enabled: bool = False
    provider: str = "ollama"      # ollama | lmstudio | compatible | openai | anthropic
    model: str = ""               # empty = provider default / the model loaded locally
    base_url: str = ""            # empty = provider default
    api_key: str = ""             # empty = read OPENAI_API_KEY / ANTHROPIC_API_KEY
    interpret: bool = True        # let it read the instructions / feedback text
    direct: bool = True           # let it choose and order the shots
    timeout: float = 180.0
    max_candidates: int = 40      # rows of the shot table sent to the model


@dataclass
class DroneConfig:
    analysis: AnalysisParams = field(default_factory=AnalysisParams)
    motion: MotionThresholds = field(default_factory=MotionThresholds)
    weights: ScoreWeights = field(default_factory=ScoreWeights)
    use_clip: bool = True
    duplicate_threshold: float = 0.90      # similarity above this = same shot again
    max_shots_per_clip: int = 3
    render_mode: str = "gpu"               # modules.system.encoder_select mode
    hwaccel: str = "auto"                  # decoder: auto | cuda | none
    ai: AISettings = field(default_factory=AISettings)

    def weights_for(self, style: Style) -> ScoreWeights:
        d = asdict(self.weights)
        d.update(style.weight_overrides)
        return ScoreWeights(**d).normalised()

    # -- persistence ---------------------------------------------------
    @staticmethod
    def default_path() -> str:
        from drone.analysis_cache import default_cache_dir
        return os.path.join(default_cache_dir(), "config.json")

    def save(self, path: str = "") -> str:
        path = path or self.default_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2, ensure_ascii=False)
        return path

    @classmethod
    def load(cls, path: str = "") -> "DroneConfig":
        path = path or cls.default_path()
        cfg = cls()
        if not os.path.exists(path):
            return cfg
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return cfg
        for name, sub in (("analysis", cfg.analysis), ("motion", cfg.motion),
                          ("weights", cfg.weights), ("ai", cfg.ai)):
            for k, v in (data.get(name) or {}).items():
                if hasattr(sub, k):
                    setattr(sub, k, type(getattr(sub, k))(v))
        for k in ("use_clip", "duplicate_threshold", "max_shots_per_clip",
                  "render_mode", "hwaccel"):
            if k in data:
                setattr(cfg, k, data[k])
        return cfg
