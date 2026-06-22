#!/usr/bin/env bash
# Copy LICENSE and .github/ assets from the repo root into the extension so
# they ship inside the .vsix and are accessible via HARNESS_ROOT (server)
# and context.extensionPath (extension) at runtime.
#
# .github/commands/  — slash-command catalog (/help, /feature-dev, ...)
# .github/pipelines/ — pipeline definitions + agent .md prompts; without
#                      bundling these, loadAgentPrompt's extension-bundle
#                      fallback finds nothing and agents drop to a generic
#                      placeholder when the open workspace is not the
#                      CopilotHarness repo.
# .github/skills/    — pushed by harness_read_stage.
# .github/agents/    — cross-pipeline agents (skill-builder, etc).
# .github/instructions/, .github/memory/ — copied if present.
#
# LICENSE — vsce warns if missing from the package directory.
#
# Run from repo root:  bash copilot-harness-extension/scripts/copy-assets.sh
# Or via npm script:   npm run build:assets  (from copilot-harness-extension/)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EXT_DIR="$REPO_ROOT/copilot-harness-extension"

echo "Copying LICENSE + .github assets into extension..."

cp "$REPO_ROOT/LICENSE" "$EXT_DIR/LICENSE"

mkdir -p "$EXT_DIR/.github"
for sub in skills agents commands pipelines instructions memory; do
    rm -rf "$EXT_DIR/.github/$sub"
    if [[ -d "$REPO_ROOT/.github/$sub" ]]; then
        cp -r "$REPO_ROOT/.github/$sub" "$EXT_DIR/.github/$sub"
    fi
done

echo "Assets copied to: $EXT_DIR/.github/ and $EXT_DIR/LICENSE"

# Ensure launch.js is in bin/ (it lives in the repo under bin/ already,
# but vsce will only package files not in .vscodeignore — verify it's there).
mkdir -p "$EXT_DIR/bin"
if [[ ! -f "$EXT_DIR/bin/launch.js" ]]; then
    echo "Warning: $EXT_DIR/bin/launch.js not found — skipping copy."
else
    echo "launch.js already in bin/."
fi
