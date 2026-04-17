#!/usr/bin/env python3
"""Render a PlantUML diagram to SVG or PNG via the plantuml CLI or jar."""

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PLANTUML_JAR = os.environ.get("PLANTUML_JAR", "")
TIMEOUT = 60


def find_plantuml() -> list[str]:
    """Return the command prefix to invoke PlantUML."""
    # prefer system plantuml binary
    try:
        subprocess.run(["plantuml", "-version"], capture_output=True, timeout=5, shell=False)
        return ["plantuml"]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # fall back to jar
    if PLANTUML_JAR and Path(PLANTUML_JAR).is_file():
        return ["java", "-jar", PLANTUML_JAR]
    return []


def render(source: str, fmt: str) -> dict:
    cmd = find_plantuml()
    if not cmd:
        return {
            "ok": False,
            "error": "plantuml not found — install plantuml or set PLANTUML_JAR env var",
        }

    fmt = fmt.lower()
    if fmt not in ("svg", "png"):
        return {"ok": False, "error": f"unsupported format: {fmt!r} — use 'svg' or 'png'"}

    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = Path(tmpdir) / "diagram.puml"
        out_path = Path(tmpdir) / f"diagram.{fmt}"
        src_path.write_text(source, encoding="utf-8")

        result = subprocess.run(
            [*cmd, f"-t{fmt}", "-o", tmpdir, str(src_path)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            shell=False,
        )

        if result.returncode != 0:
            return {"ok": False, "error": result.stderr.strip() or result.stdout.strip()}

        if not out_path.exists():
            return {"ok": False, "error": "plantuml produced no output file"}

        content = out_path.read_bytes()
        return {
            "ok": True,
            "format": fmt,
            "content_base64": base64.b64encode(content).decode(),
            "size_bytes": len(content),
        }


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
        source = payload.get("source", "").strip()
        fmt = payload.get("format", "svg")
    except (json.JSONDecodeError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)

    if not source:
        print(json.dumps({"ok": False, "error": "source is required"}))
        sys.exit(1)

    if not source.startswith("@start"):
        source = f"@startuml\n{source}\n@enduml"

    print(json.dumps(render(source, fmt)))


if __name__ == "__main__":
    main()
