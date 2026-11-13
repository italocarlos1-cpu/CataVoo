# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
"""PyInstaller recipe for DroneHighlighter (Windows .exe; also builds on Linux/macOS).

Produces one folder ``dist/DroneHighlighter/`` with two programs sharing the
same libraries:

    DroneHighlighter.exe       the window (no console)
    DroneHighlighter-cli.exe   the command line (console)

ffmpeg comes from the imageio-ffmpeg wheel (a static build), so nothing has to
be installed on the PC that runs the program.

Editions (environment variable DH_WITH_CLIP when building):

    (unset)  "lite": no PyTorch/CLIP — landscapes recognised by colour.
             ~250–350 MB folder.
    1        with PyTorch + transformers for CLIP landscape recognition.
             Several GB (CUDA build of torch); install torch first.

Build:  pyinstaller packaging/drone/DroneHighlighter.spec --noconfirm
(run from the repository root; ``packaging\\drone\\build_exe.bat`` does it all)
"""
import os
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
sys.path.insert(0, ROOT)
WITH_CLIP = os.environ.get("DH_WITH_CLIP", "") == "1"
ICON = os.path.join(SPECPATH, "DroneHighlighter.ico")

hidden = collect_submodules("drone") + [
    # VideoHighlighter engine pieces the drone package imports inside functions
    "modules.media.edl", "modules.media.transitions", "modules.media.ffmpeg_tools",
    "modules.media.video_cutter", "modules.media.combine_videos",
    "modules.audio.music_analysis", "modules.segments.scene_cuts",
    "modules.system.encoder_select", "modules.system.app_paths",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "av",
]
if WITH_CLIP:
    hidden += ["llm.clip_prefilter", "transformers", "torch"]

# Heavy packages VideoHighlighter can use but DroneHighlighter does not need.
excludes = ["tkinter", "matplotlib", "IPython", "jupyter", "notebook", "pandas", "sklearn",
            "tensorflow", "keras", "ultralytics", "onnxruntime", "openvino", "optimum",
            "pytest", "faster_whisper", "whisper", "speechbrain", "pyannote", "ollama"]
if not WITH_CLIP:
    excludes += ["torch", "torchvision", "torchaudio", "transformers", "timm", "accelerate"]

binaries = collect_dynamic_libs("imageio_ffmpeg", search_patterns=["ffmpeg*", "*.exe"])
datas = [(ICON, "packaging/drone")]


def analysis(script):
    return Analysis([os.path.join(SPECPATH, script)], pathex=[ROOT], binaries=binaries,
                    datas=datas, hiddenimports=hidden, excludes=excludes,
                    hookspath=[os.path.join(ROOT, "packaging", "pyinstaller-hooks")],
                    noarchive=False)


a_gui = analysis("launch_gui.py")
a_cli = analysis("launch_cli.py")
pyz_gui = PYZ(a_gui.pure)
pyz_cli = PYZ(a_cli.pure)

exe_gui = EXE(pyz_gui, a_gui.scripts, [], exclude_binaries=True, name="DroneHighlighter",
              console=False, icon=ICON, upx=False)
exe_cli = EXE(pyz_cli, a_cli.scripts, [], exclude_binaries=True, name="DroneHighlighter-cli",
              console=True, icon=ICON, upx=False)

coll = COLLECT(exe_gui, exe_cli,
               a_gui.binaries, a_gui.datas, a_cli.binaries, a_cli.datas,
               strip=False, upx=False, name="DroneHighlighter")
