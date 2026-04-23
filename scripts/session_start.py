#!/usr/bin/env python3
"""SessionStart hook — run the baseline_checks block from pipeline.yaml.

Invocation (stdin is JSON, optional):
    python scripts/session_start.py
    stdin (optional): {
      "pipeline": "feature-dev",
      "workspace_root": "/path/to/workspace"
    }

Defaults:
    pipeline        = "feature-dev"
    workspace_root  = parent of scripts/

Exit codes:
    0 — all baseline checks passed (or no pipeline.yaml)
    1 — one or more checks failed; reasons on stderr
    2 — malformed input on stdin

Never send an LLM to do a linter's job: checks are declarative reads
from the YAML file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:
    print(
        "session_start: PyYAML not installed; skipping baseline checks.",
        file=sys.stderr,
    )
    sys.exit(0)


def _pipeline_yaml_path(workspace_root: Path, pipeline: str) -> Path:
    return workspace_root / ".github" / "pipelines" / pipeline / "pipeline.yaml"


def run_baseline_checks(workspace_root: Path, pipeline: str) -> list[str]:
    """Return a list of failure messages (empty list = all passed)."""
    pyaml = _pipeline_yaml_path(workspace_root, pipeline)
    if not pyaml.exists():
        return []  # no pipeline config → nothing to check
    with open(pyaml, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    checks = config.get("baseline_checks") or []
    failures: list[str] = []
    for check in checks:
        ctype = check.get("type")
        if ctype == "file_read":
            target = workspace_root / check.get("path", "")
            if not target.exists():
                failures.append(
                    check.get("error") or f"Baseline check failed: {target} does not exist."
                )
        # Unknown check types are skipped silently — forward-compat.
    return failures


def main() -> int:
    raw = sys.stdin.read()
    payload: dict = {}
    if raw.strip():
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"session_start: invalid JSON on stdin: {exc}", file=sys.stderr)
            return 2

    pipeline = str(payload.get("pipeline") or "feature-dev")
    workspace_root = Path(
        payload.get("workspace_root") or Path(__file__).resolve().parent.parent
    ).resolve()

    failures = run_baseline_checks(workspace_root, pipeline)
    if failures:
        for msg in failures:
            print(f"session_start: {msg}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
