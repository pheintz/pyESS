# PyInstaller spec for pyESS.  Build with:  pyinstaller pyESS.spec
#
# ONE-FOLDER on purpose, not one-file:
#   * one-file unpacks ~50MB to a temp dir on every launch (slow start), and that
#     temp dir is exactly where the config must NOT live;
#   * one-file executables are a well-known antivirus false-positive magnet.
# The result is dist/pyESS/ - zip that folder for the release.

import os

from PyInstaller.utils.hooks import collect_dynamic_libs

# Paths in a spec resolve relative to the SPEC FILE, not the working directory, and this
# spec lives in packaging/. Anchor everything to the project root so the build works the
# same whether it is invoked from the root or from anywhere else.
ROOT = os.path.dirname(SPECPATH)

# vgamepad ships ViGEmClient.dll (x64/x86) as package data. PyInstaller does not always
# pick native DLLs up by itself, so collect them explicitly - without this the app
# builds fine and then fails at runtime the moment it tries to create the pad.
binaries = collect_dynamic_libs("vgamepad")

a = Analysis(
    [os.path.join(ROOT, "src", "pyESS_app.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=binaries,
    # NOTE: LICENSE / README / pyESS_zones.json are deliberately NOT listed here.
    # PyInstaller 6 puts datas inside _internal/, but the licence has to be visible
    # beside the .exe and the config must sit where _base_dir() looks for it. The
    # build script copies those three to the top level after COLLECT.
    datas=[],
    hiddenimports=["vgamepad", "pygame"],
    hookspath=[],
    runtime_hooks=[],
    # Trim things a tkinter app never touches. pygame is only used for joystick input.
    # Do NOT exclude setuptools: pygame.pkgdata imports pkg_resources at import time,
    # which pulls in jaraco.* - excluding it builds fine and then dies on startup with
    # "No module named 'jaraco'". Found by actually running the build.
    excludes=["numpy", "scipy", "matplotlib", "PIL", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="pyESS",
    icon=os.path.join(ROOT, "docs", "pyess.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX compression is another AV false-positive trigger
    console=False,        # GUI app - no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="pyESS",
)
