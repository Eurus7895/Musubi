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

# composer.py is a top-level module (not inside any of the first-party
# packages above), and PyInstaller's static scan doesn't always pick up the
# `import composer` in server.py reliably across builds. Name it explicitly
# so a clean build always includes it.
hidden.append("composer")

# Phase H.1 — composer.py and policy_engine.py both load pipeline.yaml at
# runtime. Both import yaml *lazily* (so the server boots even without
# PyYAML in the bundle and falls back to canonical feature-dev defaults),
# but a properly-built bundle should include PyYAML so the new pipelines
# (and feature-dev's own pipeline.yaml-driven skill injection) actually
# work. PyInstaller's import-graph misses lazy imports, so list yaml here.
hidden.append("yaml")

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
