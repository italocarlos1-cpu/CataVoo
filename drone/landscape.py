# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the landscape is, and how interesting it is — CLIP when available.

Two backends with one output shape (per-thumbnail category probabilities):

* **CLIP zero-shot** (ViT-B/32) through PyTorch + transformers — fp16 on an
  NVIDIA card, fp32 on the processor otherwise. ~600 MB of VRAM, loaded once
  for the whole folder after the decode pass, then released. In the .exe these
  come from the separately installed CLIP pack (``Instalar_IA_Paisagens.bat``). Embeddings are cached,
  so a second montage of the same trip never runs CLIP again.
* **Colour heuristic** when the CLIP stack is not installed: water, vegetation,
  sky, warm roofs and texture from :mod:`drone.frame_metrics`. Coarser — it
  cannot tell a mountain from a hill — but never fails.

This replaces SkyGrid's YOLO-based subject detection (ultralytics is AGPL and
banned from this codebase by upstream's own test) with a model-free design.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

# key, CLIP prompt, Portuguese label, base interest 0..1
CATEGORIES = [
    ("waterfall", "an aerial drone photo of a waterfall", "cachoeira", 1.00),
    ("mountains", "an aerial drone photo of mountains and ridges", "serra/montanha", 0.95),
    ("historic", "an aerial drone photo of a historic colonial town with old church and tiled roofs", "cidade histórica", 0.95),
    ("sunset", "an aerial drone photo at sunset with a golden sky", "pôr do sol", 0.95),
    ("coast", "an aerial drone photo of a beach and ocean coastline", "praia/litoral", 0.90),
    ("canyon", "an aerial drone photo of a canyon with rock cliffs", "cânion/rochas", 0.90),
    ("lake", "an aerial drone photo of a lake", "lago", 0.85),
    ("river", "an aerial drone photo of a river winding through the landscape", "rio", 0.85),
    ("forest", "an aerial drone photo of a dense green forest", "floresta/mata", 0.75),
    ("city", "an aerial drone photo of a modern city with buildings", "cidade", 0.75),
    ("countryside", "an aerial drone photo of farmland and fields", "campo/fazenda", 0.60),
    ("road", "an aerial drone photo of a road", "estrada", 0.50),
]
BORING = [
    "a blurry out of focus photo",
    "a photo of only the empty sky",
    "a dark underexposed photo",
    "a photo looking straight down at bare ground",
]
CAT_KEYS = [c[0] for c in CATEGORIES]
CAT_VALUE = np.array([c[3] for c in CATEGORIES])
LABEL_PT = {c[0]: c[2] for c in CATEGORIES}

# Portuguese / English words in a prompt → category keys (used by prompt_parser)
KEYWORDS = {
    "waterfall": ("cachoeira", "cascata", "queda d", "salto", "waterfall"),
    "mountains": ("montanha", "serra", "morro", "pico", "mountain", "chapada"),
    "historic": ("histórica", "historica", "colonial", "igreja", "centro histórico", "historic"),
    "sunset": ("pôr do sol", "por do sol", "entardecer", "amanhecer", "nascer do sol", "sunset", "golden hour"),
    "coast": ("praia", "mar", "litoral", "oceano", "beach", "coast"),
    "canyon": ("cânion", "canion", "canyon", "rocha", "paredão", "falésia"),
    "lake": ("lago", "lagoa", "represa", "lake"),
    "river": ("rio ", "rios", "rio,", "river", "córrego"),
    "forest": ("floresta", "mata", "cerrado", "forest", "árvores"),
    "city": ("cidade", "urbano", "prédios", "city"),
    "countryside": ("campo", "fazenda", "plantação", "countryside"),
    "road": ("estrada", "rodovia", "road"),
}

MODEL_TAG = "clip-vit-b32-v1"


def _softmax(x, axis=-1):
    x = x - x.max(axis=axis, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=axis, keepdims=True)


def heuristic_probs(tl: dict, idx: np.ndarray) -> np.ndarray:
    """Category probabilities from colour statistics (fallback backend)."""
    g = np.asarray(tl["green"])[idx]
    w = np.asarray(tl["water"])[idx]
    warm = np.asarray(tl["warm"])[idx]
    e = np.asarray(tl["edges"])[idx]
    white = np.asarray(tl["white"])[idx]
    sky = np.asarray(tl["sky"])[idx]
    lum = np.asarray(tl["mean_l"])[idx]
    n = len(idx)
    logits = np.zeros((n, len(CATEGORIES)))
    col = {k: i for i, k in enumerate(CAT_KEYS)}
    logits[:, col["forest"]] = 4 * g
    logits[:, col["lake"]] = 4 * w
    logits[:, col["river"]] = 3 * w * g * 4
    logits[:, col["waterfall"]] = 5 * white * (g + w) * 3
    logits[:, col["historic"]] = 6 * warm + 2 * e
    logits[:, col["city"]] = 3 * e * (1 - g)
    logits[:, col["countryside"]] = 2.5 * g * (1 - e * 4)
    logits[:, col["mountains"]] = 2.0 * sky * g * 3
    logits[:, col["sunset"]] = 5 * warm * sky * (lum < 0.5)
    return _softmax(logits * 3.0)


def heuristic_interest(tl: dict, idx: np.ndarray) -> np.ndarray:
    colorful = np.clip(np.asarray(tl["colorful"])[idx] / 70.0, 0, 1)
    edges = np.asarray(tl["edges"])[idx]
    detail = np.clip(1.0 - np.abs(edges - 0.10) / 0.10, 0, 1)   # neither flat nor noise
    sky = np.asarray(tl["sky"])[idx]
    horizon = np.clip(1.0 - np.abs(sky - 0.3) / 0.3, 0, 1)        # a sky band reads as a vista
    return 0.45 * colorful + 0.30 * detail + 0.25 * horizon


CLIP_MODEL_ID = "openai/clip-vit-base-patch32"


class _TorchClip:
    """CLIP through PyTorch + transformers only: the NVIDIA card when there is
    one, the processor otherwise. No OpenVINO, so the separately installed
    "pacote CLIP" (packs/clip) is all a PC needs. The weights (~600 MB) are
    fetched from Hugging Face on first use and cached; offline afterwards."""

    def __init__(self, model_id: str = CLIP_MODEL_ID):
        import torch
        from transformers import CLIPModel, CLIPProcessor
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.model = CLIPModel.from_pretrained(model_id, torch_dtype=self.dtype).to(self.device).eval()
        try:
            self.processor = CLIPProcessor.from_pretrained(model_id, use_fast=False)
        except TypeError:
            self.processor = CLIPProcessor.from_pretrained(model_id)

    def infer(self, texts: list, images: list):
        torch = self.torch
        inputs = self.processor(text=list(texts), images=images, return_tensors="pt", padding=True)
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        if "pixel_values" in inputs:
            inputs["pixel_values"] = inputs["pixel_values"].to(self.dtype)
        with torch.no_grad():
            out = self.model(**inputs)
        return SimpleNamespace(logits_per_image=out.logits_per_image.float().cpu())


class ClipScorer:
    """Multi-label zero-shot landscape recognition with CLIP."""

    def __init__(self, log_fn=print):
        self.log_fn = log_fn
        self._pf = None

    @staticmethod
    def available() -> bool:
        """True when PyTorch and transformers can be imported — in the .exe,
        from the CLIP pack next to the program (packs/clip)."""
        import importlib.util
        try:
            return (importlib.util.find_spec("torch") is not None
                    and importlib.util.find_spec("transformers") is not None)
        except (ImportError, ValueError):
            return False

    def load(self):
        self.log_fn("🧠 Carregando o CLIP (na primeira vez baixa ~600 MB da internet)…")
        self._pf = _TorchClip()
        where = "placa NVIDIA" if self._pf.device == "cuda" else "processador"
        self.log_fn(f"🧠 CLIP carregado no {where} ({self._pf.device})")

    def probs(self, images_bgr: list) -> tuple[np.ndarray, np.ndarray]:
        """(category probs [n, C], P(boring) [n]) for BGR images."""
        import cv2
        from PIL import Image
        labels = [c[1] for c in CATEGORIES] + BORING
        cats, bor = [], []
        for i in range(0, len(images_bgr), 32):
            batch = [Image.fromarray(cv2.cvtColor(im, cv2.COLOR_BGR2RGB))
                     for im in images_bgr[i:i + 32]]
            out = self._pf.infer(labels, batch)
            logits = out.logits_per_image
            try:
                logits = logits.float().cpu().numpy()
            except AttributeError:
                logits = np.asarray(logits)
            p = _softmax(logits)
            cats.append(p[:, :len(CATEGORIES)])
            bor.append(p[:, len(CATEGORIES):].sum(axis=1))
        c = np.concatenate(cats)
        c = c / np.clip(c.sum(axis=1, keepdims=True), 1e-9, None)
        return c, np.concatenate(bor)

    def unload(self):
        self._pf = None
        try:
            import gc
            gc.collect()
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


def annotate(analyses: list[dict], cache, *, use_clip: bool = True, log_fn=print,
             progress_fn=None) -> str:
    """Add per-sample ``cat_probs`` and ``interest`` to every timeline in place.

    Returns the backend that ran ("clip" or "heuristic").
    """
    import cv2
    backend = "heuristic"
    scorer = None
    if use_clip:
        todo = [a for a in analyses if cache.get_embeddings(a["content_hash"], MODEL_TAG) is None]
        if not todo:
            backend = "clip" if analyses else "heuristic"
        elif ClipScorer.available():
            try:
                scorer = ClipScorer(log_fn)
                scorer.load()
                backend = "clip"
            except Exception as exc:
                log_fn(f"⚠️ CLIP indisponível ({exc}); usando heurística de cores")
                scorer = None
        else:
            log_fn("ℹ️ CLIP não instalado; usando heurística de cores para paisagens")

    try:
        for n, a in enumerate(analyses):
            tl = a["timeline"]
            t = np.asarray(tl["t"])
            data = cache.get_embeddings(a["content_hash"], MODEL_TAG) if use_clip else None
            if data is None and scorer is not None:
                thumbs = cache.get_thumbs(a["content_hash"])
                imgs = [cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR) for _, j in thumbs]
                keep = [(tt, im) for (tt, _), im in zip(thumbs, imgs) if im is not None]
                if keep:
                    cp, pb = scorer.probs([im for _, im in keep])
                    data = {"t": [k[0] for k in keep], "cats": np.round(cp, 4).tolist(),
                            "boring": np.round(pb, 4).tolist()}
                    cache.put_embeddings(a["content_hash"], MODEL_TAG, data)
            if data is not None and data.get("t"):
                tt = np.asarray(data["t"])
                nearest = np.clip(np.searchsorted(tt, t), 0, len(tt) - 1)
                prev = np.clip(nearest - 1, 0, len(tt) - 1)
                pick = np.where(np.abs(tt[prev] - t) < np.abs(tt[nearest] - t), prev, nearest)
                cats = np.asarray(data["cats"])[pick]
                boring = np.asarray(data["boring"])[pick]
                interest = (1.0 - boring) * (cats @ CAT_VALUE)
                a["landscape_backend"] = "clip"
            else:
                idx = np.arange(len(t))
                cats = heuristic_probs(tl, idx)
                interest = heuristic_interest(tl, idx)
                a["landscape_backend"] = "heuristic"
            tl["cat_probs"] = cats
            tl["interest"] = np.clip(interest, 0, 1)
            if progress_fn:
                progress_fn(n + 1, len(analyses), a["name"])
    finally:
        if scorer is not None:
            scorer.unload()
    return backend
