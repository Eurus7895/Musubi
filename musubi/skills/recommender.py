"""Deterministic skill recommendations for the standalone agent.

musubi-tier: substrate
expires-when: never - skill selection is catalog routing, not model logic.

Pure scoring over already-visible skill metadata. No file I/O, no LLM calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from skills.skill_loader import SkillMeta


@dataclass(frozen=True)
class SkillRecommendation:
    skill_id: str
    title: str
    confidence: float
    reasons: list[str]


_WORD_RE = re.compile(r"[a-z0-9_./:-]+")


def recommend_skills(
    task: str,
    skills: list[SkillMeta],
    *,
    context_summary: str = "",
    tools_used: list[str] | None = None,
    limit: int = 5,
) -> list[SkillRecommendation]:
    """Rank matching skills using deterministic text and tool-use signals."""
    tools = [tool for tool in (tools_used or []) if tool]
    text = _normalize(" ".join([task or "", context_summary or "", " ".join(tools)]))
    scored: list[tuple[int, int, SkillRecommendation]] = []

    for index, meta in enumerate(skills):
        score, reasons = _score_skill(meta, text, tools)
        if score <= 0:
            continue
        confidence = min(0.99, round(score / 100, 2))
        scored.append((
            score,
            index,
            SkillRecommendation(
                skill_id=meta.skill_id,
                title=meta.title,
                confidence=confidence,
                reasons=reasons,
            ),
        ))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored[:max(0, limit)]]


def _score_skill(
    meta: SkillMeta,
    text: str,
    tools_used: list[str],
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    for trigger in meta.triggers:
        needle = _normalize(trigger)
        if needle and needle in text:
            score += 40
            reasons.append(f"trigger matched: {trigger}")

    used = {tool.lower() for tool in tools_used}
    for tool in meta.tools:
        clean = tool.lower()
        if clean in used:
            score += 35
            reasons.append(f"tool used: {tool}")
        elif clean and clean in text:
            score += 25
            reasons.append(f"tool mentioned: {tool}")

    tokens = set(_WORD_RE.findall(text))
    for token in _identity_tokens(meta):
        if token in tokens:
            score += 8
            reasons.append(f"skill identity matched: {token}")
            break

    return score, reasons[:4]


def _identity_tokens(meta: SkillMeta) -> set[str]:
    values = {meta.skill_id.replace("-", " ").lower(), meta.title.lower()}
    tokens: set[str] = set()
    for value in values:
        tokens.update(_WORD_RE.findall(value))
    return {token for token in tokens if len(token) >= 4}


def _normalize(value: str) -> str:
    return " ".join(str(value).lower().split())
