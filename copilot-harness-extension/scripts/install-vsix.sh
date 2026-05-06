#!/usr/bin/env bash
# Install the most recently built .vsix into the user's VS Code with --force,
# so a same-version rebuild actually replaces the installed copy.
#
# WSL caveat. Inside WSL, `code` is a wrapper that downloads/runs the VS Code
# Server *inside the WSL distro* — that's for Remote-WSL development, not for
# installing extensions into the Windows host VS Code. To install into the
# Windows VS Code from WSL you must call `code.exe` directly. This script
# auto-prefers `code.exe` when running under WSL.
#
# Run from repo root:  bash copilot-harness-extension/scripts/install-vsix.sh
# Or via npm script:   npm run install:vsix   (from copilot-harness-extension/)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EXT_DIR="$REPO_ROOT/copilot-harness-extension"

cd "$EXT_DIR"

# Pick the newest .vsix in case multiple versions accumulate from past builds.
VSIX="$(ls -t copilot-harness-extension-*.vsix 2>/dev/null | head -n 1 || true)"
if [[ -z "$VSIX" ]]; then
    echo "ERROR: no .vsix found in $EXT_DIR. Run 'npm run package' first."
    exit 1
fi
echo "Installing: $VSIX"

# Detect WSL — /proc/sys/kernel/osrelease mentions 'microsoft' on WSL1/2.
IS_WSL=0
if [[ -r /proc/sys/kernel/osrelease ]] && grep -qi "microsoft" /proc/sys/kernel/osrelease 2>/dev/null; then
    IS_WSL=1
fi

# Resolution order:
#   WSL → code.exe (Windows host); code as last-ditch fallback.
#   Native (macOS/Linux/Git-Bash on Windows) → code.cmd, then code.
CODE_BIN=""
VSIX_PATH_FOR_CODE="$EXT_DIR/$VSIX"
if [[ "$IS_WSL" == "1" ]]; then
    if command -v code.exe &>/dev/null; then
        CODE_BIN="code.exe"
        # Windows code.exe needs a Windows-style path.
        if command -v wslpath &>/dev/null; then
            VSIX_PATH_FOR_CODE="$(wslpath -w "$EXT_DIR/$VSIX")"
        fi
    fi
fi
if [[ -z "$CODE_BIN" ]]; then
    if command -v code.cmd &>/dev/null; then
        CODE_BIN="code.cmd"
    elif command -v code &>/dev/null; then
        CODE_BIN="code"
    fi
fi

if [[ -z "$CODE_BIN" ]]; then
    echo "ERROR: neither 'code.exe', 'code.cmd', nor 'code' found on PATH."
    echo "  Open VS Code → command palette → 'Shell Command: Install code command in PATH'."
    echo "  Or install manually: code --install-extension \"$EXT_DIR/$VSIX\" --force"
    exit 1
fi

echo "Using: $CODE_BIN"
# --force is critical: same version (0.4.0 → 0.4.0) is otherwise a no-op.
"$CODE_BIN" --install-extension "$VSIX_PATH_FOR_CODE" --force
echo
echo "Installed. Reload VS Code: Ctrl+Shift+P → 'Developer: Reload Window'."
