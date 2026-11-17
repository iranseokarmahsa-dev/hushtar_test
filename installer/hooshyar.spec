from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(ROOT / "installer" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "app" / "templates"), "app/templates"),
        (str(ROOT / "app" / "static"), "app/static"),
        (str(ROOT / "prompts"), "prompts"),
        (str(ROOT / "schemas"), "schemas"),
    ],
    hiddenimports=collect_submodules("app") + collect_submodules("uvicorn"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Hooshyar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Hooshyar",
)
