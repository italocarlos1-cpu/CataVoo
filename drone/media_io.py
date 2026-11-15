# SPDX-License-Identifier: AGPL-3.0-or-later
"""Probe drone clips and stream downscaled frames out of them via ffmpeg.

Why ffmpeg and not ``cv2.VideoCapture``: a DJI 4K clip is H.264/HEVC, often
10-bit, and decoding it through OpenCV is single-threaded and CPU-only. ffmpeg
decodes on all cores (or NVDEC on an NVIDIA card), drops the frames we do not
need, and scales the rest down *before* they cross into Python — so a 4K clip
costs about what a 480p clip costs on the Python side.

Sampling pattern
================
Frames are delivered in **consecutive pairs** every ``step`` frames::

    n = 0, 1,   step, step+1,   2*step, 2*step+1, ...

The first frame of each pair carries the visual measurements; the pair itself
gives the camera's *instantaneous* motion between two adjacent frames. That is
what lets the motion analyser tell vibration (adjacent-frame motion that
disagrees with the trend) from a deliberate, smooth move (adjacent-frame motion
that matches it). Sampling single frames at 4 Hz would alias vibration away.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Iterator, Optional

import numpy as np


def ffmpeg_bin() -> str:
    try:
        from modules.system.app_paths import ffmpeg_exe
        return ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_bin() -> Optional[str]:
    found = shutil.which("ffprobe")
    if found:
        return found
    try:
        from modules.media.ffmpeg_tools import ffprobe_exe
        return ffprobe_exe()
    except Exception:
        return None


_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


def _run(cmd, **kw):
    if _NO_WINDOW:
        kw.setdefault("creationflags", _NO_WINDOW)
    return subprocess.run(cmd, **kw)


@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    codec: str = ""
    pix_fmt: str = ""
    bit_depth: int = 8
    rotation: int = 0
    has_audio: bool = False
    creation_time: str = ""
    make: str = ""
    model: str = ""
    location: str = ""          # ISO 6709 string when the container has one
    tags: dict = field(default_factory=dict)

    @property
    def display_size(self) -> tuple[int, int]:
        """Width/height after the rotation flag is applied (ffmpeg autorotates)."""
        if abs(self.rotation) % 180 == 90:
            return self.height, self.width
        return self.width, self.height


def _fraction(text) -> float:
    try:
        if isinstance(text, str) and "/" in text:
            a, b = text.split("/", 1)
            return float(a) / float(b) if float(b) else 0.0
        return float(text)
    except Exception:
        return 0.0


def probe(path: str, timeout: float = 30) -> MediaInfo:
    """Container/stream metadata. Falls back to OpenCV when ffprobe is absent."""
    info = MediaInfo(path=path)
    exe = ffprobe_bin()
    data = None
    if exe:
        try:
            out = _run([exe, "-v", "error", "-print_format", "json",
                        "-show_format", "-show_streams", path],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
            if out.returncode == 0:
                data = json.loads(out.stdout or "{}")
        except Exception:
            data = None
    if data is None:
        try:
            from modules.media.ffmpeg_tools import probe as vh_probe
            data = vh_probe(path, timeout=timeout)
        except Exception:
            data = None

    if data:
        fmt = data.get("format", {}) or {}
        tags = {str(k).lower(): v for k, v in (fmt.get("tags") or {}).items()}
        info.duration = _fraction(fmt.get("duration", 0))
        for st in data.get("streams", []) or []:
            kind = st.get("codec_type")
            if kind == "video" and not info.width:
                if (st.get("disposition") or {}).get("attached_pic"):
                    continue
                info.width = int(st.get("width") or 0)
                info.height = int(st.get("height") or 0)
                info.codec = st.get("codec_name", "")
                info.pix_fmt = st.get("pix_fmt", "")
                info.fps = _fraction(st.get("avg_frame_rate")) or _fraction(st.get("r_frame_rate"))
                bits = st.get("bits_per_raw_sample")
                if bits and str(bits).isdigit():
                    info.bit_depth = int(bits)
                elif "10" in info.pix_fmt:
                    info.bit_depth = 10
                stags = {str(k).lower(): v for k, v in (st.get("tags") or {}).items()}
                rot = stags.get("rotate")
                for sd in st.get("side_data_list", []) or []:
                    if "rotation" in sd:
                        rot = sd["rotation"]
                try:
                    info.rotation = int(float(rot or 0))
                except Exception:
                    info.rotation = 0
                if not info.duration:
                    info.duration = _fraction(st.get("duration", 0))
                for k, v in stags.items():
                    tags.setdefault(k, v)
            elif kind == "audio":
                info.has_audio = True
        info.tags = tags
        info.creation_time = str(tags.get("creation_time", ""))
        info.make = str(tags.get("make") or tags.get("com.apple.quicktime.make") or "")
        info.model = str(tags.get("model") or tags.get("com.apple.quicktime.model") or "")
        info.location = str(tags.get("location") or tags.get("com.apple.quicktime.location.iso6709") or "")

    if not info.width or not info.duration:
        try:
            import cv2
            cap = cv2.VideoCapture(path)
            if cap.isOpened():
                info.width = info.width or int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                info.height = info.height or int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                info.fps = info.fps or float(cap.get(cv2.CAP_PROP_FPS) or 0)
                n = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                if not info.duration and info.fps:
                    info.duration = n / info.fps
            cap.release()
        except Exception:
            pass
    return info


_HWACCEL_CACHE: dict = {}


def available_hwaccels() -> list[str]:
    if "list" not in _HWACCEL_CACHE:
        try:
            out = _run([ffmpeg_bin(), "-hide_banner", "-hwaccels"],
                       capture_output=True, text=True, timeout=15)
            names = [l.strip() for l in (out.stdout or "").splitlines()[1:] if l.strip()]
        except Exception:
            names = []
        _HWACCEL_CACHE["list"] = names
    return _HWACCEL_CACHE["list"]


def _hw_args(hwaccel: str) -> list[str]:
    if hwaccel in ("", "none", "cpu"):
        return []
    if hwaccel == "auto":
        hw = available_hwaccels()
        # cuda on the user's RTX card; d3d11va is a reasonable Windows fallback.
        for name in ("cuda", "d3d11va", "videotoolbox"):
            if name in hw:
                return ["-hwaccel", name]
        return []
    return ["-hwaccel", hwaccel]


def _even(n: float) -> int:
    return max(2, int(round(n / 2.0)) * 2)


def analysis_size(info: MediaInfo, width: int) -> tuple[int, int]:
    w, h = info.display_size
    if not (w and h):
        return width, _even(width * 9 / 16)
    out_w = min(width, _even(w))
    return out_w, _even(out_w * h / w)


def iter_frame_pairs(info: MediaInfo, *, sample_hz: float = 4.0,
                     width: int = 480, hwaccel: str = "auto",
                     cancel_check=None) -> Iterator[tuple[float, np.ndarray, np.ndarray]]:
    """Yield ``(t, frame0, frame1)`` — two adjacent BGR frames every 1/sample_hz s.

    Retries without hardware decoding when the accelerated decoder produced
    nothing (driver missing, unsupported profile), so callers never need to
    know which path ran.
    """
    fps = info.fps if info.fps and info.fps > 1 else 30.0
    step = max(2, int(round(fps / max(0.1, sample_hz))))
    out_w, out_h = analysis_size(info, width)
    frame_bytes = out_w * out_h * 3
    vf = (f"select='lt(mod(n\\,{step})\\,2)',"
          f"scale={out_w}:{out_h}:flags=area,format=bgr24")

    attempts = [hwaccel] if hwaccel in ("", "none", "cpu") else [hwaccel, "none"]
    for attempt in attempts:
        cmd = [ffmpeg_bin(), "-v", "error", "-nostdin"] + _hw_args(attempt) + [
            "-i", info.path, "-an", "-sn", "-dn", "-vf", vf,
            "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
        kw = {"creationflags": _NO_WINDOW} if _NO_WINDOW else {}
        # stderr is discarded: a pipe nobody reads fills up and freezes ffmpeg
        # on a file that prints many warnings.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                bufsize=frame_bytes * 4, **kw)
        produced = 0
        pending = None
        try:
            while True:
                if cancel_check is not None and cancel_check():
                    return
                buf = proc.stdout.read(frame_bytes)
                if not buf or len(buf) < frame_bytes:
                    break
                frame = np.frombuffer(buf, dtype=np.uint8).reshape(out_h, out_w, 3)
                if pending is None:
                    pending = frame
                    continue
                t = (produced * step) / fps
                produced += 1
                yield t, pending, frame
                pending = None
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass
            proc.wait()
        if produced > 0:
            return
    return


def grab_frame(path: str, t: float, width: int = 640) -> Optional[np.ndarray]:
    """One BGR frame at ``t`` seconds (for previews and CLIP thumbnails)."""
    cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.3f}",
           "-i", path, "-frames:v", "1", "-vf", f"scale={width}:-2",
           "-f", "image2pipe", "-vcodec", "bmp", "pipe:1"]      # bmp: no time lost compressing
    try:
        out = _run(cmd, capture_output=True, timeout=60)
        if out.returncode != 0 or not out.stdout:
            return None
        import cv2
        return cv2.imdecode(np.frombuffer(out.stdout, np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None
