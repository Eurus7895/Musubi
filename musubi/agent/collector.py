"""Bounded preparation of model-visible context; never chooses the next action.

musubi-tier: substrate
expires-when: never - context bounds and provenance remain execution requirements
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.context import fit_context, fit_model_input


@dataclass(frozen=True)
class ContextBundle:
    """Prepared input and its identity, not a claim of sufficient knowledge."""

    messages: list[dict[str, Any]]
    input_hash: str
    serialized_chars: int


def collect_context(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    budget_chars: int | None,
    compression_db_path: Path | None,
) -> ContextBundle:
    """Reuse observations already obtained by governed tools; do not auto-scan.

    An explicit cap includes tool schemas. With no override, retain the current
    configured conversation fitting policy. Tool observations and retrieval
    still enter through the guarded execution path; this component never gains
    filesystem/network authority by preparing context.
    """
    if budget_chars is None:
        prepared = fit_context(messages, compression_db_path=compression_db_path)
    else:
        prepared = fit_model_input(
            messages, tools, budget_chars=budget_chars,
            compression_db_path=compression_db_path,
        )
    payload = json.dumps(
        {"messages": prepared, "tools": tools},
        ensure_ascii=False, sort_keys=True, default=str,
    )
    return ContextBundle(
        messages=prepared,
        input_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        serialized_chars=len(payload),
    )
