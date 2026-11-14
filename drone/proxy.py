# SPDX-License-Identifier: AGPL-3.0-or-later
"""Light copies of the raw clips for the editor's preview ("proxies").

A DJI 4K H.265 file is heavy to decode and slow to jump around in, so the
manual editor's monitor stutters on it. Like any video editor, the preview
uses a small copy instead: 540p H.264 with a keyframe every half second
(instant seeks), no audio. Made once per clip in the background, kept in the
cache folder by content hash (renaming or moving the footage doesn't redo
it), never used for the final render.
"""
from __future__ import annotations

import os

from drone.analysis_cache import default_cache_dir
from drone.media_io import _hw_args, _run, ffmpeg_bin

HEIGHT = 540
VERSION = 1


def proxy_dir() -> str:
    d = os.path.join(default_cache_dir(), "proxies")
    os.makedirs(d, exist_ok=True)
    return d


def proxy_path(content_hash: str) -> str:
    return os.path.join(proxy_dir(), f"{content_hash}_{HEIGHT}p_v{VERSION}.mp4")


def existing(content_hash: str) -> str:
    """The proxy's path if it is already made, else ''."""
    if not content_hash:
        return ""
    p = proxy_path(content_hash)
    return p if os.path.exists(p) and os.path.getsize(p) > 0 else ""


def make(src: str, content_hash: str, cancel_check=None) -> str:
    """Create the proxy (GPU decode when available). Returns its path or ''."""
    if not content_hash:
        return ""
    dst = proxy_path(content_hash)
    if os.path.exists(dst):
        return dst
    tmp = dst + ".tmp.mp4"
    for hw in ("auto", "none"):
        if cancel_check is not None and cancel_check():
            return ""
        cmd = [ffmpeg_bin(), "-y", "-v", "error", "-nostdin"] + _hw_args(hw) + [
            "-i", src, "-map", "0:v:0", "-an", "-sn", "-dn",
            "-vf", f"scale=-2:{HEIGHT}:flags=bilinear,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-tune", "fastdecode",
            "-g", "15", "-keyint_min", "15", "-bf", "0", "-movflags", "+faststart", tmp]
        r = _run(cmd, capture_output=True)
        if r.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, dst)
            return dst
    try:
        os.remove(tmp)
    except OSError:
        pass
    return ""
