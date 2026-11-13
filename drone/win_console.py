# SPDX-License-Identifier: AGPL-3.0-or-later
"""No black console windows flashing on Windows.

The window program is built ``--windowed``, so every ffmpeg/ffprobe it starts
would open a console of its own for a split second. DroneHighlighter's own
calls already pass ``CREATE_NO_WINDOW``, but the rendering engine it reuses
from VideoHighlighter starts processes too. :func:`hide_child_consoles` makes
``CREATE_NO_WINDOW`` the default for every ``subprocess.Popen`` in the process
(``subprocess.run``/``check_output`` go through it), unless a call asks for a
console explicitly.
"""
from __future__ import annotations

import os
import subprocess

CREATE_NO_WINDOW = 0x08000000
_CONSOLE_FLAGS = 0x00000010 | 0x00000008   # CREATE_NEW_CONSOLE | DETACHED_PROCESS
_installed = False


def hide_child_consoles() -> bool:
    """Install the default once (Windows only). Returns True when active."""
    global _installed
    if os.name != "nt":
        return False
    if _installed:
        return True
    original = subprocess.Popen.__init__

    def __init__(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0) or 0
        if not flags & (_CONSOLE_FLAGS | CREATE_NO_WINDOW):
            kwargs["creationflags"] = flags | CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = __init__
    _installed = True
    return True
