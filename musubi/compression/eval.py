"""Deterministic compression eval gate.

musubi-tier: substrate
expires-when: never - compression quality must stay measurable without
  substrate-side LLM calls.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent.context import fit_context

from .router import compress, retrieve

_RETRIEVE_RE = re.compile(r'musubi_retrieve\("([0-9a-f]{16})"\)')


def run_compression_eval(db_path: Path | None = None) -> dict[str, Any]:
    """Run the built-in deterministic compression/context eval suite."""
    if db_path is not None:
        Path(db_path).unlink(missing_ok=True)
    payloads = [_eval_payload(case, db_path=db_path) for case in _payload_cases()]
    context = _eval_context(db_path=db_path)

    total_original = sum(p["original_chars"] for p in payloads)
    total_visible = sum(p["model_visible_chars"] for p in payloads)
    payload_savings = _savings_pct(total_original, total_visible)
    retrieve_ok = sum(1 for p in payloads if p["retrieve_ok"])
    marker_count = sum(p["retrieve_marker_count"] for p in payloads)
    quality_proxy_ok = all(p["quality_proxy_ok"] for p in payloads)
    gate_ok = retrieve_ok == len(payloads) and quality_proxy_ok and context["quality_proxy_ok"]

    return {
        "status": "ok" if gate_ok else "fail",
        "feature_level": (
            "Token economics Step 5: cache hardening, output steering, "
            "and compression eval"
        ),
        "benchmark_note": (
            "model_visible_chars includes retrieval marker overhead; "
            "eval is deterministic and zero-LLM by default"
        ),
        "summary": {
            "payload_cases": len(payloads),
            "retrieve_ok": retrieve_ok,
            "retrieve_failures": len(payloads) - retrieve_ok,
            "payload_original_chars": total_original,
            "payload_model_visible_chars": total_visible,
            "payload_savings_pct": payload_savings,
            "context_original_chars": context["original_context_chars"],
            "context_packed_chars": context["packed_context_chars"],
            "context_savings_pct": context["savings_pct"],
            "retrieve_marker_count": marker_count + context["retrieve_marker_count"],
            "quality_proxy_ok": quality_proxy_ok and context["quality_proxy_ok"],
        },
        "payloads": payloads,
        "context_packing": context,
    }


def _eval_payload(case: dict[str, str], *, db_path: Path | None) -> dict[str, Any]:
    result = compress(case["text"], hint=case["hint"], db_path=db_path)
    markers = _RETRIEVE_RE.findall(result.compressed)
    retrieved = retrieve(result.ref_id, db_path=db_path) if result.ref_id else None
    quality_ok = all(token in result.compressed for token in case["expected_tokens"])
    return {
        "name": case["name"],
        "hint": case["hint"],
        "kind": result.kind,
        "ref_id": result.ref_id,
        "original_chars": result.original_chars,
        "model_visible_chars": result.compressed_chars,
        "ratio": round(result.ratio, 4),
        "savings_pct": _savings_pct(result.original_chars, result.compressed_chars),
        "retrieve_ok": retrieved == case["text"],
        "retrieve_marker_count": len(markers),
        "quality_proxy_ok": bool(result.ref_id) and quality_ok,
        "compressed_preview": result.compressed[:800],
    }


def _eval_context(*, db_path: Path | None) -> dict[str, Any]:
    compressible = _json_payload()
    raw = "opaque-token-stream " + ("Z" * 20_000)
    recent = "recent result must remain visible"
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "summarise compression behavior"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "json", "name": "read", "input": {}}],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "json", "content": compressible}
            ],
        },
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "raw", "name": "read", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "raw", "content": raw}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": recent}]},
    ]
    original_chars = len(json.dumps(messages, ensure_ascii=False, default=str))
    packed = fit_context(
        messages,
        budget_chars=7_000,
        keep_last_turns=1,
        compression_db_path=db_path,
    )
    packed_chars = len(json.dumps(packed, ensure_ascii=False, default=str))
    tool_texts = [
        block.get("content", "")
        for message in packed
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
    return {
        "strategy": "Budgeted fit_context packing",
        "budget_chars": 7_000,
        "original_context_chars": original_chars,
        "packed_context_chars": packed_chars,
        "savings_pct": _savings_pct(original_chars, packed_chars),
        "compressed_tool_results": sum("[musubi:compressed" in t for t in tool_texts),
        "trimmed_tool_results": sum(str(t).startswith("[context-trimmed:") for t in tool_texts),
        "retrieve_marker_count": sum(len(_RETRIEVE_RE.findall(str(t))) for t in tool_texts),
        "recent_turn_preserved": recent in json.dumps(packed, ensure_ascii=False),
        "quality_proxy_ok": any("json smart crush" in str(t) for t in tool_texts),
        "packed_preview": json.dumps(packed[2:6], ensure_ascii=False, default=str)[:800],
    }


def _payload_cases() -> list[dict[str, Any]]:
    return [
        {
            "name": "structured json",
            "hint": "payload.json",
            "text": _json_payload(),
            "expected_tokens": ("json smart crush", "$.items[]", "root_keys"),
        },
        {
            "name": "python module",
            "hint": "module.py",
            "text": _python_payload(),
            "expected_tokens": ("python structure", "class Worker", "def run"),
        },
        {
            "name": "service log",
            "hint": "service.log",
            "text": _log_payload(),
            "expected_tokens": ("log pattern groups", "x", "first=", "last="),
        },
        {
            "name": "markdown notes",
            "hint": "notes.md",
            "text": _text_payload(),
            "expected_tokens": ("text outline", "## Overview", "paragraphs="),
        },
    ]


def _json_payload() -> str:
    return json.dumps(
        {
            "build_id": "eval-2026-06-30",
            "items": [
                {
                    "id": i,
                    "name": f"artifact-{i}",
                    "status": "ready" if i % 3 == 0 else "queued",
                    "owner": {"team": "platform", "region": f"r{i % 5}"},
                    "metrics": {
                        "chars": 1200 + i,
                        "tokens": 300 + (i % 17),
                        "cacheable": i % 2 == 0,
                    },
                    "tags": ["compression", "musubi", f"batch-{i % 8}"],
                }
                for i in range(420)
            ],
        },
        indent=2,
    )


def _python_payload() -> str:
    methods = "\n".join(
        f"""
    def step_{i}(self, value: int) -> int:
        \"\"\"Large body that should not survive structural compression.\"\"\"
        total = value
        for offset in range(80):
            total += offset + {i}
        return total
"""
        for i in range(36)
    )
    return (
        "import json\nfrom pathlib import Path\n\n"
        "class Worker:\n"
        "    def __init__(self, root: Path) -> None:\n"
        "        self.root = root\n"
        f"{methods}\n"
        "def run(config: dict) -> list[int]:\n"
        "    worker = Worker(Path(config['root']))\n"
        "    return [worker.step_0(1), worker.step_1(2)]\n"
    )


def _log_payload() -> str:
    lines = []
    for i in range(800):
        lines.append(
            "2026-06-30T12:%02d:%02dZ request_id=%08d path=/srv/app/%d "
            "status=%d duration_ms=%d sha=%040x"
            % (i % 60, i % 60, i, i % 12, 200 + (i % 5), 20 + (i % 80), i)
        )
    return "\n".join(lines)


def _text_payload() -> str:
    sections = []
    for heading in ("Overview", "Compression Strategy", "Retrieval", "Evaluation"):
        paragraphs = "\n\n".join(
            (
                f"{heading} paragraph {i} explains how deterministic compression "
                "keeps model-visible input compact while verbatim source remains "
                "recoverable through retrieve markers and audit-safe storage."
            )
            for i in range(14)
        )
        sections.append(f"## {heading}\n\n{paragraphs}")
    return "# Musubi Compression Notes\n\n" + "\n\n".join(sections)


def _savings_pct(original: int, visible: int) -> float:
    if original <= 0:
        return 0.0
    return round(max(0.0, (original - visible) / original * 100), 1)
