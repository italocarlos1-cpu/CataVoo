# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
"""PyInstaller recipe for DroneHighlighter (Windows .exe; also builds on Linux/macOS).

Produces one folder ``dist/DroneHighlighter/`` with two programs sharing the
same libraries:

    DroneHighlighter.exe       the window (no console)
    _internal/DroneHighlighter-cli.exe   the command line (console)

ffmpeg comes from the imageio-ffmpeg wheel (a static build), so nothing has to
be installed on the PC that runs the program.

CLIP (landscape recognition) is *not* inside the .exe: PyTorch is several GB.
``Instalar_IA_Paisagens.bat`` (copied next to the .exe) installs it later into
``packs/clip/site-packages`` and the runtime hook ``packaging/packs/rthook_packs.py``
(VideoHighlighter's own pack loader) puts that folder on the import path when
the program starts. For that to work the bundle follows the upstream rules in
``tools/pack_core_args.py``: torch stays out, the *whole* standard library goes
in, and packages both sides use are bundled whole.

DH_WITH_CLIP=1 when building bundles torch + transformers instead (huge;
for a self-contained offline copy).

Build:  pyinstaller packaging/drone/DroneHighlighter.spec --noconfirm
(run from the repository root; ``packaging\\drone\\build_exe.bat`` does it all)
"""
import importlib.util
import os
import shutil
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
    hidden += ["transformers", "torch"]


def _load_upstream(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Upstream's rules for a core that loads PyTorch from a pack.
_core = _load_upstream("pack_core_args", os.path.join("tools", "pack_core_args.py"))
_args = _core._stdlib()
for flag, name in zip(_args[::2], _args[1::2]):
    if flag == "--collect-submodules":
        hidden += collect_submodules(name)
    else:
        hidden.append(name)
for name in _core.SHARED:
    if importlib.util.find_spec(name.split(".")[0]) is not None:
        hidden += collect_submodules(name)

# Heavy packages VideoHighlighter can use but DroneHighlighter does not need.
excludes = ["tkinter", "matplotlib", "IPython", "jupyter", "notebook", "pandas", "sklearn",
            "tensorflow", "keras", "ultralytics", "onnxruntime", "openvino", "optimum",
            "pytest", "faster_whisper", "whisper", "speechbrain", "pyannote", "ollama"]
if not WITH_CLIP:
    excludes += list(_core.PACK_ONLY) + ["torchaudio", "transformers", "tokenizers", "safetensors",
                                         "huggingface_hub", "timm", "accelerate"]

binaries = collect_dynamic_libs("imageio_ffmpeg", search_patterns=["ffmpeg*", "*.exe"])
datas = [(ICON, "packaging/drone")]


def analysis(script):
    return Analysis([os.path.join(SPECPATH, script)], pathex=[ROOT], binaries=binaries,
                    datas=datas, hiddenimports=hidden, excludes=excludes,
                    hookspath=[os.path.join(ROOT, "packaging", "pyinstaller-hooks")],
                    runtime_hooks=[os.path.join(ROOT, "packaging", "packs", "rthook_packs.py")],
                    noarchive=False)


a_gui = analysis("launch_gui.py")
a_cli = analysis("launch_cli.py")
pyz_gui = PYZ(a_gui.pure)
pyz_cli = PYZ(a_cli.pure)

exe_gui = EXE(pyz_gui, a_gui.scripts, [], exclude_binaries=True, name="DroneHighlighter",
              console=False, icon=ICON, upx=False)
# The command line lives *inside* _internal (out of the user's way), so it is
# told that its support files are in its own folder.
exe_cli = EXE(pyz_cli, a_cli.scripts, [], exclude_binaries=True, name="DroneHighlighter-cli",
              console=True, icon=ICON, upx=False, contents_directory=".")

coll = COLLECT(exe_gui, exe_cli,
               a_gui.binaries, a_gui.datas, a_cli.binaries, a_cli.datas,
               strip=False, upx=False, name="DroneHighlighter")

# Next to the .exe: the CLIP installer and the LEIA-ME; the Python X.Y the pack must match.
_out = os.path.join(DISTPATH, "DroneHighlighter")
if os.path.isdir(_out):
    shutil.copy2(os.path.join(SPECPATH, "Instalar_IA_Paisagens.bat"), _out)
    shutil.copy2(os.path.join(SPECPATH, "LEIA-ME.txt"), _out)
    for cli in ("DroneHighlighter-cli.exe", "DroneHighlighter-cli"):
        if os.path.exists(os.path.join(_out, cli)):
            os.replace(os.path.join(_out, cli), os.path.join(_out, "_internal", cli))
    # Inside _internal, out of the user's way; only the installer reads it.
    with open(os.path.join(_out, "_internal", "dh_python.txt"), "w", encoding="ascii") as fh:
        fh.write(f"{sys.version_info.major}.{sys.version_info.minor}")
