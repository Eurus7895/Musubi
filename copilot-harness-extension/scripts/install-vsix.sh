#!/usr/bin/env bash
# Install the most recently built .vsix into the user's VS Code with --force,
# so a same-version rebuild actually replaces the installed copy.
#
# Required shell on Windows: Git Bash (bundled with Git for Windows). WSL is
# explicitly not supported — the WSL `code` command runs the VS Code Server
# inside the distro, which is for Remote-WSL development, not for installing
# extensions into the Windows host. Use Git Bash or PowerShell instead.
#
# Run from repo root:  bash copilot-harness-extension/scripts/install-vsix.sh
# Or via npm script:   npm run install:vsix   (from copilot-harness-extension/)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EXT_DIR="$REPO_ROOT/copilot-harness-extension"

cd "$EXT_DIR"

# Refuse to run under WSL — the WSL `code` wrapper is the wrong target and
# silently breaks (downloads VS Code Server into the WSL distro instead of
# installing into the Windows host).
if [[ -r /proc/sys/kernel/osrelease ]] && grep -qi "microsoft" /proc/sys/kernel/osrelease 2>/dev/null; then
    echo "ERROR: running under WSL. Use Git Bash or PowerShell on Windows."
    echo "  PowerShell:  npm run install:vsix"
    echo "  Git Bash:    same command, from a 'Git Bash Here' shell."
    exit 1
fi

# Pick the newest .vsix in case multiple versions accumulate from past builds.
VSIX="$(ls -t copilot-harness-extension-*.vsix 2>/dev/null | head -n 1 || true)"
if [[ -z "$VSIX" ]]; then
    echo "ERROR: no .vsix found in $EXT_DIR. Run 'npm run package' first."
    exit 1
fi
echo "Installing: $VSIX"

# code.cmd is what Git Bash and PowerShell on Windows resolve to; code is
# the symlink on macOS/Linux. Either works.
if command -v code.cmd &>/dev/null; then
    CODE_BIN="code.cmd"
elif command -v code &>/dev/null; then
    CODE_BIN="code"
else
    echo "ERROR: neither 'code.cmd' nor 'code' found on PATH."
    echo "  Open VS Code → command palette → 'Shell Command: Install code command in PATH'."
    echo "  Or install manually: code --install-extension \"$EXT_DIR/$VSIX\" --force"
    exit 1
fi

echo "Using: $CODE_BIN"
# --force is critical: same version (0.4.0 → 0.4.0) is otherwise a no-op.
"$CODE_BIN" --install-extension "$VSIX" --force
echo
echo "Installed. Reload VS Code: Ctrl+Shift+P → 'Developer: Reload Window'."
