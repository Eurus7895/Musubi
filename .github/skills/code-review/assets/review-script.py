#!/usr/bin/env python3
"""Run static analysis on provided files and return structured findings."""

import json
import subprocess
import sys
from pathlib import Path


def run_ruff(files: list[str]) -> list[dict]:
    result = subprocess.run(
        ["ruff", "check", "--output-format=json", *files],
        capture_output=True,
        text=True,
        timeout=60,
        shell=False,
    )
    if not result.stdout.strip():
        return []
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        return [{"tool": "ruff", "error": "failed to parse output", "raw": result.stdout}]
    return [
        {
            "tool": "ruff",
            "file": item.get("filename"),
            "line": item.get("location", {}).get("row"),
            "code": item.get("code"),
            "message": item.get("message"),
            "severity": "high" if item.get("code", "").startswith(("E", "F")) else "low",
        }
        for item in raw
    ]


def run_mypy(files: list[str]) -> list[dict]:
    result = subprocess.run(
        ["mypy", "--no-error-summary", *files],
        capture_output=True,
        text=True,
        timeout=60,
        shell=False,
    )
    findings = []
    for line in result.stdout.splitlines():
        parts = line.split(":", 3)
        if len(parts) >= 4:
            findings.append({
                "tool": "mypy",
                "file": parts[0].strip(),
                "line": parts[1].strip(),
                "level": parts[2].strip(),
                "message": parts[3].strip(),
                "severity": "high" if "error" in parts[2] else "medium",
            })
    return findings


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
        files = payload.get("files", [])
    except (json.JSONDecodeError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)

    if not files:
        print(json.dumps({"ok": False, "error": "no files provided"}))
        sys.exit(1)

    existing = [f for f in files if Path(f).exists()]
    if not existing:
        print(json.dumps({"ok": False, "error": "no files found on disk"}))
        sys.exit(1)

    findings = run_ruff(existing) + run_mypy(existing)
    print(json.dumps({"ok": True, "findings": findings, "file_count": len(existing)}))


if __name__ == "__main__":
    main()
