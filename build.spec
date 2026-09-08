# PyInstaller spec: builds a single-file Windows exe.
# Run with: pyinstaller build.spec
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

block_cipher = None

hidden_imports = (
    collect_submodules("google_auth_oauthlib")
    + collect_submodules("googleapiclient")
    + collect_submodules("google.auth")
    + collect_submodules("onnxruntime")
)
# onnxruntime ships its actual inference engine as compiled DLLs that
# PyInstaller's static import scan can't see - collect_dynamic_libs finds
# them from the installed package directly.
binaries = collect_dynamic_libs("onnxruntime")

a = Analysis(
    ["src/main.py"],
    pathex=["src"],
    binaries=binaries,
    datas=[("assets", "assets")],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Sonara",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)
