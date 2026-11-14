# SPDX-License-Identifier: AGPL-3.0-or-later
"""Entry point of DroneHighlighter-cli.exe (command line, with a console).

The .exe lives in ``_internal`` (only the window program sits in the main
folder), so the pack loader's "beside the exe" is ``_internal\\packs``. The
CLIP pack is installed beside the *window* program, one folder up: add it here.
"""
import json
import multiprocessing
import os
import sys


def _parent_packs() -> None:
    if not getattr(sys, "frozen", False):
        return
    here = os.path.dirname(sys.executable)
    if os.path.basename(here).lower() != "_internal":
        return
    root = os.path.join(os.path.dirname(here), "packs")
    if not os.path.isdir(root):
        return
    best = {}
    for name in sorted(os.listdir(root)):
        site = os.path.join(root, name, "site-packages")
        if name.startswith(".") or name.endswith(".pending") or not os.path.isdir(site):
            continue
        try:
            with open(os.path.join(root, name, "pack.json"), encoding="utf-8-sig") as fh:
                info = json.load(fh)
        except (OSError, ValueError):
            info = {}
        dep, prio = info.get("dep", name), int(info.get("priority", 0) or 0)
        if dep not in best or prio > best[dep][0]:
            best[dep] = (prio, site)
    for _prio, site in best.values():
        if site not in sys.path:
            sys.path.append(site)          # appended: the bundle always wins


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _parent_packs()
    from drone.cli import main
    raise SystemExit(main())
