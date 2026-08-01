"""Deterministic stage acceptance predicates (zero LLM calls).

musubi-tier: substrate
expires-when: never - deterministic verification remains model-independent
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from workspace.grants import RootRegistry


@dataclass(frozen=True)
class CheckResult:
    type: str
    status: str
    message: str
    evidence: Mapping[str, Any]


@dataclass(frozen=True)
class GateResult:
    status: str
    checks: tuple[CheckResult, ...]


def fingerprint_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    content = path.read_bytes()
    return {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}


@dataclass
class _Element:
    tag: str
    attrs: dict[str, str]
    text: list[str]


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[_Element] = []
        self.stack: list[_Element] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = _Element(tag.lower(), {k: v or "" for k, v in attrs}, [])
        self.elements.append(element)
        self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag.lower():
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        for element in self.stack:
            element.text.append(data)


_ATTR_SELECTOR = re.compile(
    r"^(?P<tag>[a-zA-Z][\w-]*)?\[(?P<attr>[\w:-]+)"
    r"(?:=(?:'(?P<sq>[^']*)'|\"(?P<dq>[^\"]*)\"|(?P<bare>[\w-]+)))?\]$"
)


def _select(html: str, selector: str) -> list[_Element]:
    tree = _Tree()
    tree.feed(html)
    match = _ATTR_SELECTOR.fullmatch(selector)
    if match:
        expected = match.group("sq") or match.group("dq") or match.group("bare")
        return [
            element for element in tree.elements
            if (not match.group("tag") or element.tag == match.group("tag").lower())
            and match.group("attr") in element.attrs
            and (expected is None or element.attrs[match.group("attr")] == expected)
        ]
    if selector.startswith("."):
        name = selector[1:]
        return [e for e in tree.elements if name in e.attrs.get("class", "").split()]
    if selector.startswith("#"):
        return [e for e in tree.elements if e.attrs.get("id") == selector[1:]]
    if re.fullmatch(r"[a-zA-Z][\w-]*", selector):
        return [e for e in tree.elements if e.tag == selector.lower()]
    raise ValueError(f"unsupported static DOM selector {selector!r}")


def _text(element: _Element) -> str:
    return " ".join("".join(element.text).split())


def _runner_status(value: Any) -> tuple[str, Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return str(value.get("status") or "error"), value
    return str(getattr(value, "status", "error")), {
        "message": str(getattr(value, "message", value)),
    }


def evaluate_stage_gate(
    contract: Mapping[str, Any] | Any,
    snapshot: Mapping[str, Any],
    manifest: Sequence[Mapping[str, Any]],
    command_runner: Callable[[str], Any] | None = None,
    *,
    roots: RootRegistry,
) -> GateResult:
    predicates = (
        contract.get("exit_when", [])
        if isinstance(contract, Mapping)
        else getattr(contract, "exit_when", ())
    )
    results: list[CheckResult] = []
    for check in predicates:
        check_type = str(check.get("type") or "")
        try:
            evidence: dict[str, Any] = {}
            passed = False
            if check_type in {
                "file_exists", "file_created_or_modified", "dom_count",
                "dom_distinct_text", "dom_text_set",
            }:
                path = roots.resolve(str(check["root"]), str(check["path"]))
            if check_type == "file_exists":
                current = fingerprint_file(path)
                passed = current is not None and current["size"] > 0
                evidence = {"fingerprint": current}
            elif check_type == "file_created_or_modified":
                current = fingerprint_file(path)
                key = f"{check['root']}:{check['path']}"
                passed = current is not None and current["size"] > 0 and current != snapshot.get(key)
                evidence = {"before": snapshot.get(key), "after": current}
            elif check_type.startswith("dom_"):
                html = path.read_text(encoding="utf-8")
                elements = _select(html, str(check["selector"]))
                texts = [_text(element) for element in elements]
                if check_type == "dom_count":
                    actual: Any = len(elements)
                elif check_type == "dom_distinct_text":
                    actual = len({value for value in texts if value})
                else:
                    actual = sorted({value for value in texts if value})
                expected = check["equals"]
                if check_type == "dom_text_set":
                    expected = sorted({" ".join(str(v).split()) for v in expected})
                passed = actual == expected
                evidence = {"actual": actual, "expected": expected}
            elif check_type in {"named_command", "lint_clean"}:
                if command_runner is None:
                    raise RuntimeError("governed command runner is unavailable")
                key = str(check.get("command_id") or check_type)
                status, evidence = _runner_status(command_runner(key))
                passed = status == "pass" or (
                    check_type == "lint_clean" and status == "skipped"
                    and bool(check.get("allow_skipped"))
                )
                if status == "error":
                    raise RuntimeError(str(evidence.get("message") or status))
            else:
                raise ValueError(f"unknown check type {check_type!r}")
            results.append(CheckResult(
                check_type, "pass" if passed else "fail",
                "predicate passed" if passed else "predicate did not pass",
                evidence,
            ))
        except (OSError, UnicodeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            results.append(CheckResult(
                check_type, "error", f"{type(exc).__name__}: {exc}", {},
            ))
    status = (
        "gate_error" if any(result.status == "error" for result in results)
        else "fail" if any(result.status == "fail" for result in results)
        else "pass"
    )
    return GateResult(status, tuple(results))
