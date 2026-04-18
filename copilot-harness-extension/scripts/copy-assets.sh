#!/usr/bin/env bash
# Copy .github/skills/ and .github/agents/ from the repo root into the extension
# so they ship inside the .vsix and are accessible via HARNESS_ROOT at runtime.
#
# Run from repo root:  bash copilot-harness-extension/scripts/copy-assets.sh
# Or via npm script:   npm run build:assets  (from copilot-harness-extension/)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EXT_DIR="$REPO_ROOT/copilot-harness-extension"

echo "Copying .github assets into extension..."

mkdir -p "$EXT_DIR/.github"
rm -rf "$EXT_DIR/.github/skills" "$EXT_DIR/.github/agents"
cp -r "$REPO_ROOT/.github/skills"  "$EXT_DIR/.github/skills"
cp -r "$REPO_ROOT/.github/agents"  "$EXT_DIR/.github/agents"

echo "Assets copied to: $EXT_DIR/.github/"
