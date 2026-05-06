#!/usr/bin/env bash
# Install the most recently built .vsix into the user's VS Code with --force,
# so a same-version rebuild actually replaces the installed copy. Requires
# `code` on PATH; falls back to a clear error message if not found.
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

if ! command -v code &>/dev/null; then
    echo "ERROR: 'code' CLI not on PATH."
    echo "  Windows: open VS Code, run 'Shell Command: Install code command in PATH' from the command palette."
    echo "  macOS / Linux: same command palette entry, or check your VS Code install."
    echo ""
    echo "Manual fallback:"
    echo "  code --install-extension \"$EXT_DIR/$VSIX\" --force"
    exit 1
fi

# --force is critical: same version (0.4.0 → 0.4.0) is otherwise a no-op.
code --install-extension "$VSIX" --force
echo
echo "Installed. Reload VS Code: Ctrl+Shift+P → 'Developer: Reload Window'."
