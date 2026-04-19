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

if ! command -v pyinstaller &>/dev/null; then
    echo "Error: pyinstaller not found. Install it first:"
    echo "  pip install pyinstaller"
    exit 1
fi

cd "$SERVER_DIR"
pyinstaller copilot-harness.spec --distpath "$SERVER_DIR/dist" --workpath "$SERVER_DIR/build" --noconfirm

mkdir -p "$BIN_DIR"
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    cp "$SERVER_DIR/dist/copilot-harness.exe" "$BIN_DIR/copilot-harness.exe"
    echo "Binary copied to: $BIN_DIR/copilot-harness.exe"
else
    cp "$SERVER_DIR/dist/copilot-harness" "$BIN_DIR/copilot-harness"
    chmod +x "$BIN_DIR/copilot-harness"
    echo "Binary copied to: $BIN_DIR/copilot-harness"
fi
