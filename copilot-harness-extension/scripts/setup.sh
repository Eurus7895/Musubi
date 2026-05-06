#!/usr/bin/env bash
# One-time setup for a fresh checkout. Installs npm deps for the extension,
# creates a repo-root .venv/, and installs the Python server in editable mode
# plus PyInstaller (needed by build:server).
#
# Run from repo root:  bash copilot-harness-extension/scripts/setup.sh
# Or via npm script:   npm run setup   (from copilot-harness-extension/)
#
# Idempotent: re-running only reinstalls what has changed. The venv is re-used
# across builds — PyInstaller and its transitive deps are the bulk of the cost,
# so caching them in .venv/ saves ~30–60 s per rebuild on a clean machine.

set -euo pipefail

# Refuse to run under WSL — the Windows-targeted bringup needs Git Bash or
# PowerShell. Under WSL, /mnt/c/... paths break Windows Python's pip and the
# bundled `code` command targets the wrong VS Code.
if [[ -r /proc/sys/kernel/osrelease ]] && grep -qi "microsoft" /proc/sys/kernel/osrelease 2>/dev/null; then
    echo "ERROR: running under WSL. Use Git Bash or PowerShell on Windows."
    echo "  PowerShell:  npm run setup"
    echo "  Git Bash:    same command, from a 'Git Bash Here' shell."
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EXT_DIR="$REPO_ROOT/copilot-harness-extension"
SERVER_DIR="$REPO_ROOT/copilot-harness"
VENV_DIR="$REPO_ROOT/.venv"

echo "── CopilotHarness setup ──"
echo "Repo root: $REPO_ROOT"

# 1. Node / npm deps for the extension. Use `npm ci` when a lockfile exists —
#    it's reproducible and typically 2–3× faster than `npm install`.
echo
echo "[1/3] npm deps (extension)"
cd "$EXT_DIR"
if [[ -f "package-lock.json" ]]; then
    npm ci --no-audit --no-fund
else
    npm install --no-audit --no-fund
fi

# 2. Python venv at repo root.
echo
echo "[2/3] Python venv + server deps"
if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating venv at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

# Resolve venv python across Linux / macOS / Windows-Git-Bash.
if [[ -f "$VENV_DIR/Scripts/python.exe" ]]; then
    VENV_PY="$VENV_DIR/Scripts/python.exe"
else
    VENV_PY="$VENV_DIR/bin/python"
fi

"$VENV_PY" -m pip install --upgrade pip --quiet
# Editable install of the server package + PyInstaller for build:server.
"$VENV_PY" -m pip install --quiet -e "$SERVER_DIR"
"$VENV_PY" -m pip install --quiet pyinstaller

# 3. Summary.
echo
echo "[3/3] Done"
echo "  venv:       $VENV_DIR"
echo "  python:     $VENV_PY"
echo "  node_modules: $EXT_DIR/node_modules"
echo
echo "Next: npm run build   (runs build:server, build:assets, compile in parallel)"
