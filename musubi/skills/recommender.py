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

#: Divisor applied to a skill's context-only score before it is added to the
#: request score. Conversation history is evidence about the PROJECT, not about
#: what is being asked now, so it may break a tie between two skills the
#: request already matched — never outvote the request itself.
_CONTEXT_WEIGHT_DIVISOR = 4


def recommend_skills(
    task: str,
    skills: list[SkillMeta],
    *,
    context_summary: str = "",
    tools_used: list[str] | None = None,
    limit: int = 5,
) -> list[SkillRecommendation]:
    """Rank matching skills using deterministic text and tool-use signals.

    The REQUEST elects; the conversation may only break ties.

    Both used to be concatenated into one bag of text and scored together, so a
    skill that matched nothing in the request could still be returned at
    maximum confidence on the strength of the history behind it. Observed: on
    turn 3 of a chat that had built an HTML weather dashboard, "change the
    language of the application" matched no skill at all, while the 272-char
    context summary hit five `web-ui` triggers (html, css, dashboard, chart,
    responsive) for a score of 200 — capped to `confidence: 0.99`. The root
    asked which skill fitted, was told `web-ui` with near-certainty, and
    pushed it into a coder that was there to change some strings. The longer
    the conversation, the more confidently wrong the answer got.

    So a skill must earn a signal from the request (or the tools this turn
    actually used) to be a candidate at all. Context is then worth a quarter
    weight as a tiebreaker, and `confidence` is computed from the request score
    alone — a number that stops saturating and starts meaning "how much of
    what was asked for does this skill cover".
    """
    tools = [tool for tool in (tools_used or []) if tool]
    request_text = _normalize(" ".join([task or "", " ".join(tools)]))
    context_text = _normalize(context_summary or "")
    scored: list[tuple[int, int, SkillRecommendation]] = []

    for index, meta in enumerate(skills):
        request_score, reasons = _score_skill(meta, request_text, tools)
        if request_score <= 0:
            # Context cannot elect a skill the request never asked for.
            continue
        context_score, context_reasons = (
            _score_skill(meta, context_text, []) if context_text else (0, [])
        )
        score = request_score + context_score // _CONTEXT_WEIGHT_DIVISOR
        if context_reasons:
            reasons = reasons + [
                f"{reason} (from conversation context)"
                for reason in context_reasons
            ]
        confidence = min(0.99, round(request_score / 100, 2))
        scored.append((
            score,
            index,
            SkillRecommendation(
                skill_id=meta.skill_id,
                title=meta.title,
                confidence=confidence,
                reasons=reasons[:4],
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
