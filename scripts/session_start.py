#!/usr/bin/env python3
"""SessionStart hook — run the baseline_checks block from pipeline.yaml
AND auto-detect the project profile at session start (MVP item 4 /
Track D.1).

musubi-tier: substrate
expires-when: never — SessionStart lifecycle is the right place for
  profile detection regardless of pipeline shape.

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
    0 — all baseline checks passed (or no pipeline.yaml).
        Project-profile detection failures do NOT change the exit
        code; profile is observability for the skill router, not
        load-bearing for the pipeline run.
    1 — one or more baseline checks failed; reasons on stderr.
    2 — malformed input on stdin.

Never send an LLM to do a linter's job: checks are declarative reads
from the YAML file. Profile detection is pure manifest parsing.
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


def write_project_profile(workspace_root: Path) -> str | None:
    """MVP item 4 / Track D.1 — auto-detect the project profile and
    write it to `.github/memory/project-profile.md` (tier-2 memory).

    Best-effort: any failure logs to stderr and returns None. Profile
    is observability for the skill router; a write failure must not
    abort SessionStart. Returns the written path on success, None on
    failure.

    Importable detector lives at `musubi/workspace/detector.py`.
    The harness path is added to sys.path because this script is
    invoked directly by the SessionStart hook (no installed package
    on sys.path by default in that context).
    """
    try:
        musubi_root = Path(__file__).resolve().parent.parent / "musubi"
        if str(musubi_root) not in sys.path:
            sys.path.insert(0, str(musubi_root))
        from workspace.detector import (  # type: ignore[import-not-found]
            detect_profile,
            format_profile_md,
        )

        profile = detect_profile(workspace_root)
        profile_path = workspace_root / ".github" / "memory" / "project-profile.md"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(format_profile_md(profile), encoding="utf-8")
        return str(profile_path)
    except Exception as exc:  # noqa: BLE001 — best-effort
        print(
            f"session_start: project profile detection failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None


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

    # MVP item 4 / Track D.1 — run profile detection only after
    # baseline checks pass. Failures here are non-fatal (logged but
    # don't change the exit code).
    write_project_profile(workspace_root)

    return 0


if __name__ == "__main__":
    sys.exit(main())
