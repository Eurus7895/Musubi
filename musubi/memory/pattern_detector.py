"""Failure pattern detection — surfaces recurring evidence for human review.

musubi-tier: substrate
expires-when: never — Live trigger detection over memory.


Detects when the same type of failure recurs across sessions. It does not
author or apply prompts, skills, or patches.

Public API:
    record_failure(session_id, agent_name, issue, db_path?) → None
    detect_patterns(agent_name?, db_path?) → list[Pattern]
    detect_frustration(text, patterns_path?) → str | None       # Phase C.2
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from storage import db

PATTERN_THRESHOLD = 3  # occurrences across distinct sessions before triggering

# Phase C.2 — frustration regex bank. Source of truth lives in
# `.github/memory/sentiment-patterns.json` so non-engineers can edit it
# without touching Python. Loaded lazily; cached per `(path, mtime)`
# so a hot-edit is picked up next call without restart.
_DEFAULT_SENTIMENT_PATH = (
    Path(__file__).parent.parent.parent / ".github" / "memory" / "sentiment-patterns.json"
)


@lru_cache(maxsize=8)
def _compiled_patterns(path_str: str, mtime: float) -> list[tuple[str, re.Pattern[str]]]:
    """Compile and cache regex patterns from the JSON file.

    Cache key includes mtime so a hot edit invalidates the prior compilation.
    """
    del mtime  # only used for cache invalidation
    p = Path(path_str)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[tuple[str, re.Pattern[str]]] = []
    for entry in data.get("patterns", []):
        if not isinstance(entry, dict):
            continue
        label = entry.get("label")
        regex = entry.get("regex")
        if not isinstance(label, str) or not isinstance(regex, str):
            continue
        try:
            out.append((label, re.compile(regex)))
        except re.error:
            continue
    return out


def detect_frustration(
    text: str,
    patterns_path: Path | None = None,
) -> str | None:
    """Return the label of the first matching frustration pattern, or None.

    The agent runner calls this on each user message. A non-None
    return triggers the per-turn distillation trigger (Phase C.2 §6 (d));
    de-dup is the runner's responsibility.
    """
    if not text or not text.strip():
        return None
    path = patterns_path or _DEFAULT_SENTIMENT_PATH
    if not path.exists():
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    for label, pattern in _compiled_patterns(str(path), mtime):
        if pattern.search(text):
            return label
    return None


@dataclass
class Pattern:
    agent_name: str
    issue: str
    count: int
    session_ids: list[str] = field(default_factory=list)


def record_failure(
    session_id: str,
    agent_name: str,
    issue: str,
    db_path: Path | None = None,
) -> None:
    """Record an agent failure for later pattern analysis."""
    now = datetime.now(timezone.utc).isoformat()
    db.insert_fail_pattern(session_id, agent_name, issue, now, db_path)


def detect_patterns(
    agent_name: str | None = None,
    db_path: Path | None = None,
) -> list[Pattern]:
    """Return failure patterns that meet or exceed PATTERN_THRESHOLD.

    Groups fail_patterns rows by (agent_name, issue). Only returns
    groups with count >= PATTERN_THRESHOLD (default 3).
    """
    rows = db.get_fail_patterns(agent_name, db_path)
    groups: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        key = (row["agent_name"], row["issue"])
        groups.setdefault(key, []).append(row["session_id"])

    return [
        Pattern(agent_name=aname, issue=issue, count=len(sids), session_ids=sids)
        for (aname, issue), sids in groups.items()
        if len(sids) >= PATTERN_THRESHOLD
    ]
