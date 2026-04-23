#!/usr/bin/env python3
"""PreToolUse hook — reject tool calls that violate the pipeline policy.

Invocation (stdin is JSON):
    python scripts/pre_tool_use.py
    stdin: {"pipeline": "feature-dev", "agent": "planner", "tool": "Write"}

Exit codes:
    0 — tool call allowed
    1 — tool call denied (reason on stderr)
    2 — malformed input (reason on stderr)

Never send an LLM to do a linter's job: the decision is a pure dict
lookup in policy_engine.PIPELINE_POLICIES.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make scripts/ importable when this file is invoked from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from policy_engine import check_tool_allowed, deny_reason


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        print(f"pre_tool_use: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 2

    pipeline = payload.get("pipeline")
    agent = payload.get("agent")
    tool = payload.get("tool")
    if not (pipeline and agent and tool):
        print(
            "pre_tool_use: missing required keys. Expected "
            "{pipeline, agent, tool}.",
            file=sys.stderr,
        )
        return 2

    if check_tool_allowed(str(pipeline), str(agent), str(tool)):
        return 0

    print(deny_reason(str(pipeline), str(agent), str(tool)), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
