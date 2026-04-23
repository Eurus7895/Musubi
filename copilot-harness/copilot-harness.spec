# PyInstaller spec — builds a single-file copilot-harness binary.
# Output: dist/copilot-harness  (or dist/copilot-harness.exe on Windows)
#
# Usage:
#   cd copilot-harness
#   pyinstaller copilot-harness.spec

block_cipher = None

a = Analysis(
    ["cli.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[
        "server",
        "state",
        "context_builder",
        "verifier",
        "executor",
        "correction_loop",
        "skill_loader",
        "storage",
        "storage.db",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="copilot-harness",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
