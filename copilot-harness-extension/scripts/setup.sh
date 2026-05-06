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

# 1. Node / npm deps for the extension. Skip when node_modules is already
#    populated AND no lockfile changes are pending — the slow `npm ci` step
#    dominates a re-run otherwise.
echo
echo "[1/3] npm deps (extension)"
cd "$EXT_DIR"
NEED_NPM_INSTALL=1
if [[ -d "node_modules" && -f "node_modules/.package-lock.json" ]]; then
    # node_modules' .package-lock.json is npm's cached resolver state; if its
    # mtime matches the project lockfile, deps are already in sync.
    if [[ -f "package-lock.json" && "node_modules/.package-lock.json" -nt "package-lock.json" ]]; then
        NEED_NPM_INSTALL=0
    elif [[ ! -f "package-lock.json" && "node_modules/.package-lock.json" -nt "package.json" ]]; then
        NEED_NPM_INSTALL=0
    fi
fi
if [[ "$NEED_NPM_INSTALL" == "1" ]]; then
    if [[ -f "package-lock.json" ]]; then
        npm ci --no-audit --no-fund
    else
        npm install --no-audit --no-fund
    fi
else
    echo "node_modules up to date — skipping."
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

# Skip the pip steps when the venv already has both the editable server
# install and pyinstaller. `pip show` is cheap (~100 ms) compared to the
# 5-30 s cost of even a no-op pip install.
NEED_PIP_INSTALL=0
if ! "$VENV_PY" -m pip show --quiet copilot-harness 2>/dev/null; then NEED_PIP_INSTALL=1; fi
if ! "$VENV_PY" -m pip show --quiet pyinstaller    2>/dev/null; then NEED_PIP_INSTALL=1; fi
if [[ "$NEED_PIP_INSTALL" == "1" ]]; then
    "$VENV_PY" -m pip install --upgrade pip --quiet
    "$VENV_PY" -m pip install --quiet -e "$SERVER_DIR"
    "$VENV_PY" -m pip install --quiet pyinstaller
else
    echo "venv up to date — skipping."
fi

# 3. Summary.
echo
echo "[3/3] Done"
echo "  venv:       $VENV_DIR"
echo "  python:     $VENV_PY"
echo "  node_modules: $EXT_DIR/node_modules"
echo
echo "Next: npm run build   (runs build:server, build:assets, compile in parallel)"
