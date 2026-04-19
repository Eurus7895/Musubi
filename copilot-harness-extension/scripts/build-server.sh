#!/usr/bin/env bash
# Build the copilot-harness Python server into a single-file binary via PyInstaller.
# Output: copilot-harness-extension/bin/copilot-harness  (platform-native)
#
# Run from repo root:  bash copilot-harness-extension/scripts/build-server.sh
# Or via npm script:   npm run build:server  (from copilot-harness-extension/)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SERVER_DIR="$REPO_ROOT/copilot-harness"
EXT_DIR="$REPO_ROOT/copilot-harness-extension"
BIN_DIR="$EXT_DIR/bin"

echo "Building copilot-harness binary..."

# Prefer the venv Python when npm spawns bash without activating the venv.
# Falls back to whatever python is on PATH (system or CI environment).
if [[ -f "$REPO_ROOT/.venv/Scripts/python.exe" ]]; then
    PYTHON="$REPO_ROOT/.venv/Scripts/python.exe"
elif [[ -f "$REPO_ROOT/.venv/bin/python" ]]; then
    PYTHON="$REPO_ROOT/.venv/bin/python"
else
    PYTHON="python"
fi
echo "Using Python: $PYTHON"

if ! "$PYTHON" -m PyInstaller --version &>/dev/null; then
    echo "Error: PyInstaller not found in $PYTHON. Install it first:"
    echo "  pip install pyinstaller"
    exit 1
fi

cd "$SERVER_DIR"

# WSL bash passes /mnt/c/... paths to Windows Python, which misreads them as
# C:\mnt\c\... Use wslpath to convert to native Windows paths when in WSL.
if command -v wslpath &>/dev/null; then
    DIST_PATH="$(wslpath -w "$SERVER_DIR/dist")"
    WORK_PATH="$(wslpath -w "$SERVER_DIR/build")"
    SPEC_PATH="$(wslpath -w "$SERVER_DIR/copilot-harness.spec")"
else
    DIST_PATH="$SERVER_DIR/dist"
    WORK_PATH="$SERVER_DIR/build"
    SPEC_PATH="$SERVER_DIR/copilot-harness.spec"
fi

"$PYTHON" -m PyInstaller "$SPEC_PATH" --distpath "$DIST_PATH" --workpath "$WORK_PATH" --noconfirm

mkdir -p "$BIN_DIR"
# Detect the actual output — Windows PyInstaller produces .exe regardless of
# how bash identifies the OS (WSL reports linux-gnu, not msys/win32).
if [[ -f "$SERVER_DIR/dist/copilot-harness.exe" ]]; then
    cp "$SERVER_DIR/dist/copilot-harness.exe" "$BIN_DIR/copilot-harness.exe"
    echo "Binary copied to: $BIN_DIR/copilot-harness.exe"
else
    cp "$SERVER_DIR/dist/copilot-harness" "$BIN_DIR/copilot-harness"
    chmod +x "$BIN_DIR/copilot-harness"
    echo "Binary copied to: $BIN_DIR/copilot-harness"
fi
