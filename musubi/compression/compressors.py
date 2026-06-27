"""Deterministic, pure-Python compressors. Zero LLM calls (HI #1).

musubi-tier: substrate
expires-when: never — token cost is permanent.

Each compressor maps text -> shorter text while preserving meaning. They
are allowed to be *lossy* (drop comments, whitespace, JSON indentation)
because the verbatim original is always kept in `store` and reachable via
`musubi_retrieve`. They must never raise on valid input; the router
treats any exception as "skip compression".
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter, OrderedDict
from typing import Any

# A line that, after leading whitespace, begins a single-line comment.
# Lossy (a `#`/`//` line inside a multi-line string would be dropped from
# the model's reading copy) but the original is recoverable via the store.
_LINE_COMMENT = re.compile(r"^[ \t]*(#|//)")
_MULTI_BLANK = re.compile(r"\n{3,}")
_LOG_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ][0-2]\d:[0-5]\d:[0-5]\d(?:\.\d+)?Z?)?\b"
)
_LOG_HASH = re.compile(r"\b[0-9a-fA-F]{12,}\b")
_LOG_NUMBER = re.compile(r"\b\d+\b")
_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")


def minify_json(text: str) -> str:
    """Re-emit JSON with no indentation or inter-token spaces (lossless)."""
    obj = json.loads(text)
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def smart_crush_json(text: str) -> str:
    """Summarize JSON shape, counts, path stats, and bounded samples."""
    obj = json.loads(text)
    path_counts: OrderedDict[str, Counter[str]] = OrderedDict()
    arrays: list[str] = []

    def type_name(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int) and not isinstance(value, bool):
            return "int"
        if isinstance(value, float):
            return "float"
        if isinstance(value, str):
            return "str"
        if isinstance(value, list):
            return "array"
        if isinstance(value, dict):
            return "object"
        return type(value).__name__

    def short_json(value: Any, limit: int = 180) -> str:
        raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        if len(raw) <= limit:
            return raw
        return raw[: limit - 3] + "..."

    def path_key(parent: str, key: str) -> str:
        if key.replace("_", "").isalnum():
            return f"{parent}.{key}"
        return f"{parent}[{json.dumps(key, ensure_ascii=False)}]"

    def walk(value: Any, path: str) -> None:
        path_counts.setdefault(path, Counter())[type_name(value)] += 1
        if isinstance(value, dict):
            for key in sorted(value):
                walk(value[key], path_key(path, key))
        elif isinstance(value, list):
            sample_types = Counter(type_name(item) for item in value)
            parts = [
                f"{path}[] count={len(value)}",
                "types=" + ",".join(
                    f"{name}:{count}" for name, count in sample_types.most_common()
                ),
            ]
            if value:
                parts.append(f"sample[0]={short_json(value[0])}")
                if len(value) > 1:
                    parts.append(f"sample[-1]={short_json(value[-1])}")
            arrays.append(" ".join(parts))
            for item in value:
                walk(item, f"{path}[]")

    walk(obj, "$")
    root = type_name(obj)
    lines = [f"json smart crush root={root}"]
    if isinstance(obj, dict):
        lines.append("root_keys=" + ",".join(sorted(map(str, obj.keys()))))
    if arrays:
        lines.append("arrays:")
        lines.extend(f"- {entry}" for entry in arrays[:20])
    lines.append("paths:")
    for path, counts in list(path_counts.items())[:60]:
        counts_text = ",".join(
            f"{name}:{count}" for name, count in sorted(counts.items())
        )
        lines.append(f"- {path} {counts_text}")
    return "\n".join(lines)


def collapse_text(text: str) -> str:
    """Strip trailing whitespace per line; collapse 3+ blank lines to one."""
    lines = [ln.rstrip() for ln in text.split("\n")]
    return _MULTI_BLANK.sub("\n\n", "\n".join(lines))


def outline_text(text: str) -> str:
    """Preserve headings plus bounded section snippets."""
    compact = collapse_text(text)
    lines = compact.splitlines()
    heading_indexes = [
        index for index, line in enumerate(lines) if _HEADING.match(line)
    ]
    if not heading_indexes:
        paragraphs = [p for p in compact.split("\n\n") if p.strip()]
        if len(paragraphs) <= 1:
            return compact
        return _outline_paragraphs("text outline", paragraphs)

    sections: list[tuple[str, list[str]]] = []
    for pos, start in enumerate(heading_indexes):
        end = heading_indexes[pos + 1] if pos + 1 < len(heading_indexes) else len(lines)
        sections.append((lines[start].strip(), lines[start + 1:end]))

    out = ["text outline"]
    for heading, body_lines in sections:
        body = "\n".join(body_lines).strip()
        paragraphs = [p for p in body.split("\n\n") if p.strip()]
        out.append(f"{heading} paragraphs={len(paragraphs)} chars={len(body)}")
        if paragraphs:
            out.append(f"  first={_snippet(paragraphs[0])}")
            if len(paragraphs) > 1:
                out.append(f"  last={_snippet(paragraphs[-1])}")
    return "\n".join(out)


def strip_code(text: str) -> str:
    """Drop full-line comments + blank runs; keep code structure.

    Whole-line `#` / `//` comments and runs of blank lines are removed;
    indentation and code lines are preserved verbatim. Lossy on comments
    only — the original is retrievable.
    """
    kept: list[str] = []
    prev_blank = False
    for raw in text.split("\n"):
        line = raw.rstrip()
        if _LINE_COMMENT.match(line):
            continue
        if not line.strip():
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        kept.append(line)
    # Drop a leading/trailing blank introduced by the pass.
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept)


def compress_python_code(text: str) -> str:
    """Summarize Python imports/classes/functions; fallback on invalid code."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return strip_code(text)

    imports: list[str] = []
    top_level: list[str] = []
    classes: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(ast.unparse(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top_level.append(_format_function(node))
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(base) for base in node.bases)
            header = f"class {node.name}({bases})" if bases else f"class {node.name}"
            methods = [
                _format_function(child)
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            if methods:
                classes.append(header + "\n" + "\n".join(f"  {m}" for m in methods))
            else:
                classes.append(header)

    lines = ["python structure"]
    if imports:
        lines.append("imports:")
        lines.extend(f"- {line}" for line in imports)
    if classes:
        lines.append("classes:")
        lines.extend(f"- {block}" for block in classes)
    if top_level:
        lines.append("functions:")
        lines.extend(f"- {line}" for line in top_level)
    if len(lines) == 1:
        return strip_code(text)
    return "\n".join(lines)


def group_log_patterns(text: str) -> str:
    """Group repeated log lines by normalized deterministic patterns."""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    groups: OrderedDict[str, list[str]] = OrderedDict()
    for line in lines:
        pattern = _normalize_log_line(line)
        groups.setdefault(pattern, []).append(line)
    if not groups:
        return collapse_text(text)

    out = ["log pattern groups"]
    for pattern, examples in sorted(
        groups.items(), key=lambda item: (-len(item[1]), item[0])
    ):
        out.append(f"x{len(examples)} {pattern}")
        out.append(f"  first={_snippet(examples[0], 160)}")
        if len(examples) > 1:
            out.append(f"  last={_snippet(examples[-1], 160)}")
    return "\n".join(out)


def _format_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    signature = f"{prefix} {node.name}({_format_args(node.args)})"
    if node.returns:
        signature += f" -> {ast.unparse(node.returns)}"
    return signature


def _format_args(args: ast.arguments) -> str:
    parts: list[str] = []
    positional = list(args.posonlyargs) + list(args.args)
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for arg, default in zip(positional, defaults, strict=True):
        parts.append(_format_arg(arg, default))
    if args.vararg:
        parts.append("*" + _format_arg(args.vararg, None))
    elif args.kwonlyargs:
        parts.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        parts.append(_format_arg(arg, default))
    if args.kwarg:
        parts.append("**" + _format_arg(args.kwarg, None))
    return ", ".join(parts)


def _format_arg(arg: ast.arg, default: ast.expr | None) -> str:
    text = arg.arg
    if arg.annotation:
        text += f": {ast.unparse(arg.annotation)}"
    if default:
        text += "=" + ast.unparse(default)
    return text


def _normalize_log_line(line: str) -> str:
    normalized = _LOG_TIMESTAMP.sub("<ts>", line)
    normalized = _LOG_HASH.sub("<hash>", normalized)
    normalized = _LOG_NUMBER.sub("<num>", normalized)
    return normalized


def _outline_paragraphs(title: str, paragraphs: list[str]) -> str:
    out = [title, f"paragraphs={len(paragraphs)}"]
    out.append(f"first={_snippet(paragraphs[0])}")
    if len(paragraphs) > 1:
        out.append(f"last={_snippet(paragraphs[-1])}")
    return "\n".join(out)


def _snippet(text: str, limit: int = 140) -> str:
    one_line = " ".join(text.split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 3] + "..."
