# -*- mode: python ; coding: utf-8 -*-
# Build:  pyinstaller packaging/roblox_tracker.spec --noconfirm
# One-folder build (starts faster and trips antivirus less than one-file). The installer wraps dist/Lookout/.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(SPECPATH, "app.ico")
VERSION_FILE = os.path.join(SPECPATH, "version_info.txt")     # written by write_build_info.py

# RapidOCR ships its detection/recognition .onnx models and config.yaml as package data.
datas = collect_data_files("rapidocr_onnxruntime")
for _pkg in ("sv_ttk",):                       # theme files (optional: the app has a built-in look if it is missing)
    try:
        datas += collect_data_files(_pkg)
    except Exception:
        pass
if os.path.isdir(os.path.join(ROOT, "roblox_tracker", "assets")):      # icons from packaging/fetch_icons.py
    datas += [(os.path.join(ROOT, "roblox_tracker", "assets"), "roblox_tracker/assets")]
binaries = collect_dynamic_libs("onnxruntime")
hiddenimports = (collect_submodules("rapidocr_onnxruntime")
                 + ["mss.windows", "pystray._win32", "keyboard._winkeyboard", "keyboard._winmouse",
                    "PIL.ImageTk", "PIL._tkinter_finder"])

# The optional YOLO person detector drags in PyTorch (over a gigabyte), so the installer build leaves it out.
# The app falls back to a fixed-size box under each nametag when it is absent.
excludes = ["ultralytics", "torch", "torchvision", "tensorflow", "matplotlib", "scipy", "pandas", "IPython", "pytest"]

a = Analysis(
    [os.path.join(ROOT, "run_tracker.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Lookout",
    console=False,                       # windowed; errors go to %APPDATA%\Lookout\logs\tracker.log
    icon=ICON if os.path.exists(ICON) else None,
    version=VERSION_FILE if os.path.exists(VERSION_FILE) else None,
    upx=False,                           # UPX-packed exes are flagged by antivirus far more often
)
coll = COLLECT(exe, a.binaries, a.datas, name="Lookout", upx=False)
