# PyInstaller spec — builds a single-file copilot-harness binary.
# Output: dist/copilot-harness  (or dist/copilot-harness.exe on Windows)
#
# Usage:
#   cd copilot-harness
#   pyinstaller copilot-harness.spec

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Collect every module under each first-party package so future additions
# (Phase A/B/C/D modules etc.) ship without hand-editing this spec. The
# hand-list approach has rotted twice already — once at Phase A.2 (validation/
# subagent_context.py + new sub_sessions) and once at Phase C.1 (conversations
# table + summarizer). collect_submodules does the static walk for us.
hidden = []
for pkg in (
    "server",
    "session",
    "validation",
    "skills",
    "memory",
    "execution",
    "storage",
):
    hidden.extend(collect_submodules(pkg))

# scripts/policy_engine.py is imported dynamically via sys.path manipulation
# (validation/subagent_context.py inserts ../scripts at runtime). PyInstaller
# can't follow that, so the module has to be both on pathex AND named here.
hidden.append("policy_engine")

a = Analysis(
    ["cli.py"],
    pathex=[".", "../scripts"],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
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
