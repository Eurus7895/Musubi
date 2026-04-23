#!/usr/bin/env python3
"""Run pytest with coverage and return a structured per-module report."""

import json
import subprocess
import sys
import re
from pathlib import Path


TIMEOUT = 120


def parse_coverage_output(output: str, min_coverage: int) -> list[dict]:
    """Parse pytest-cov text output into per-module dicts."""
    modules = []
    for line in output.splitlines():
        # match lines like: copilot-harness/state.py   120   8   93%   45-52
        match = re.match(
            r"^\s*([\w/\\.]+\.py)\s+(\d+)\s+(\d+)\s+(\d+)%\s*([\d,\s\-]*)?$",
            line,
        )
        if not match:
            continue
        path, stmts, miss, pct, missing = match.groups()
        coverage = int(pct)
        modules.append({
            "name": Path(path).stem,
            "path": path,
            "statements": int(stmts),
            "missing": int(miss),
            "coverage": coverage,
            "missing_lines": missing.strip() if missing else "",
            "pass": coverage >= min_coverage,
        })
    return modules


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
        test_dir = payload.get("test_dir", "tests/")
        source_dir = payload.get("source_dir", "copilot-harness/")
        min_coverage = int(payload.get("min_coverage", 80))
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)

    if not Path(test_dir).exists():
        print(json.dumps({"ok": False, "error": f"test_dir not found: {test_dir}"}))
        sys.exit(1)

    result = subprocess.run(
        [
            "python", "-m", "pytest",
            test_dir,
            f"--cov={source_dir}",
            "--cov-report=term-missing",
            "--tb=no",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        shell=False,
    )

    combined = result.stdout + result.stderr
    modules = parse_coverage_output(combined, min_coverage)

    if not modules:
        print(json.dumps({
            "ok": False,
            "error": "no coverage data found — is pytest-cov installed?",
            "raw": combined[:2000],
        }))
        sys.exit(1)

    failing = [m for m in modules if not m["pass"]]
    print(json.dumps({
        "ok": len(failing) == 0,
        "min_coverage": min_coverage,
        "modules": modules,
        "failing_modules": [m["name"] for m in failing],
        "returncode": result.returncode,
    }))


if __name__ == "__main__":
    main()
