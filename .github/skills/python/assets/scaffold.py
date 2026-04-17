#!/usr/bin/env python3
"""Generate boilerplate for a new harness module."""

import json
import sys
from textwrap import dedent


def render_dataclass(name: str) -> str:
    return dedent(f"""\
        @dataclass(frozen=True)
        class {name}:
            ok: bool
            errors: list[str]
    """)


def render_stub(func_name: str, module: str) -> str:
    return dedent(f"""\
        def {func_name}() -> None:
            raise NotImplementedError("{module}.{func_name} not yet implemented")
    """)


def render_module(module: str, classes: list[str], functions: list[str]) -> str:
    lines: list[str] = []

    lines.append(f'"""{module} — harness component."""\n')
    lines.append("from __future__ import annotations\n")
    lines.append("import logging")
    lines.append("from dataclasses import dataclass\n")
    lines.append(f"logger = logging.getLogger(__name__)\n")

    for cls in classes:
        lines.append(render_dataclass(cls))

    for fn in functions:
        lines.append(render_stub(fn, module))

    return "\n".join(lines)


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
        module = payload.get("module", "").strip()
        classes = payload.get("classes", [])
        functions = payload.get("functions", [])
    except (json.JSONDecodeError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)

    if not module:
        print(json.dumps({"ok": False, "error": "module name required"}))
        sys.exit(1)

    content = render_module(module, classes, functions)
    print(json.dumps({
        "ok": True,
        "filename": f"copilot-harness/{module}.py",
        "content": content,
    }))


if __name__ == "__main__":
    main()
