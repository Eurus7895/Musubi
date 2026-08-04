"""Every `musubi_*` tool name the host cites actually exists on the server.

musubi-tier: substrate test — the driver addresses the substrate by string.
A name that no longer resolves fails SILENTLY (the tool is simply never
matched), so nothing in a run says the reference broke.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

MUSUBI_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = MUSUBI_ROOT.parent
TOOL_NAME_RE = re.compile(r"^musubi_[a-z0-9_]+$")


def _defined_tool_names() -> set[str]:
    """Tool names registered on the MCP server, read from its source.

    Read from the AST rather than by importing and introspecting FastMCP:
    the registry is a private attribute whose shape is the `mcp` package's
    business, while `@mcp.tool()` on a `def musubi_…` is the contract this
    repository actually writes.
    """
    tree = ast.parse((MUSUBI_ROOT / "server.py").read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and TOOL_NAME_RE.match(node.name)
        and any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "tool"
            for d in node.decorator_list
        )
    }


def _referenced_tool_names() -> dict[str, set[str]]:
    """`musubi_*` string constants in host code → the files citing them.

    ContextVar labels share the prefix without being tools (they name a
    variable, not an endpoint), so anything passed to `ContextVar(...)` is
    excluded rather than allowlisted by hand.
    """
    sources = [
        *(MUSUBI_ROOT / "agent").rglob("*.py"),
        MUSUBI_ROOT / "tool_surface.py",
    ]
    found: dict[str, set[str]] = {}
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        context_var_args = {
            id(arg)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                getattr(node.func, "id", "") == "ContextVar"
                or getattr(node.func, "attr", "") == "ContextVar"
            )
            for arg in node.args
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and TOOL_NAME_RE.match(node.value)
                and id(node) not in context_var_args
            ):
                found.setdefault(node.value, set()).add(path.name)
    for path in (REPO_ROOT / ".github" / "agents").rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for name in re.findall(r"\bmusubi_[a-z0-9_]+\b", text):
            found.setdefault(name, set()).add(str(path.relative_to(REPO_ROOT)))
    return found


def test_every_cited_tool_name_exists_on_the_server() -> None:
    defined = _defined_tool_names()
    assert len(defined) > 50, "tool discovery broke, not the references"

    missing = {
        name: sorted(files)
        for name, files in _referenced_tool_names().items()
        if name not in defined
    }
    assert not missing, (
        "host code cites tool names the server does not define — a rename "
        f"or typo that fails silently at runtime: {missing}"
    )
