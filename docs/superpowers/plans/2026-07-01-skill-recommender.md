# Skill Recommender Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic `musubi_recommend_skills` tool that helps the standalone agent choose relevant skills without LLM-side guessing, then add a compression-aware skill as the first high-value recommendation target.

**Architecture:** Extend the existing skill catalog metadata parser, add a pure `skills.recommender` scoring module, expose it through `server.py` as a read-only governance tool, and teach the standalone prompt to call it when procedural knowledge may be missing. The recommender must compose with the current agent allowlist and project-profile applicability router; it can rank visible skills but must never widen access.

**Tech Stack:** Python stdlib, existing YAML frontmatter parser in `musubi/skills/skill_loader.py`, existing `musubi/skills/router.py`, existing MCP server functions in `musubi/server.py`, pytest.

## Global Constraints

- Keep the substrate deterministic and zero-LLM; do not import any LLM SDK outside `musubi/agent/vendors/`.
- Keep `musubi_get_skill(skill_id, agent_name)` as the source for full skill content.
- The recommender returns a shortlist only; it does not auto-inject skill bodies.
- Respect `AGENT_SKILL_ALLOWLIST` first, then project-profile applicability.
- File content and docs copy must be English.
- Branch names must follow `<type>/<area>-<outcome>` and must not include `codex`.
- Do not use a worktree.

---

## File Structure

- Modify `musubi/skills/skill_loader.py`
  - Add optional `description`, `triggers`, and `tools` fields to `SkillMeta`.
  - Parse `description`, `triggers`, and `tools` from `SKILL.md` frontmatter.
- Create `musubi/skills/recommender.py`
  - Pure scoring/ranking logic. No file I/O, no LLM calls.
- Modify `musubi/server.py`
  - Add MCP tool `musubi_recommend_skills(task, agent_name, context_summary="", tools_used=None)`.
  - Reuse allowlist + project-profile filtering from `musubi_list_skills`.
- Modify `musubi/agent/boundary.py`
  - Mark `musubi_recommend_skills` as read-only governance.
- Modify `musubi/agent/context.py`
  - Add one short steering sentence telling the standalone agent to call `musubi_recommend_skills` when skill choice is unclear.
- Modify `musubi/validation/context_builder.py`
  - Add `compression-aware-context` to the `agent` allowlist, and optionally to `coder` and `reviewer` if tests show those roles need it.
- Create `.github/skills/compression-aware-context/SKILL.md`
  - Operational guidance for compression markers, `musubi_retrieve`, and compression stats.
- Add tests:
  - `musubi/tests/test_skill_applies_to.py`
  - `musubi/tests/test_skill_recommender.py`
  - `musubi/tests/test_skill_access.py`
  - `musubi/tests/test_agent_loop.py` or `musubi/tests/test_context.py`
- Update docs:
  - `docs/roadmap.md`
  - `docs/guide.md`

---

### Task 1: Extend Skill Metadata Parsing

**Files:**
- Modify: `musubi/skills/skill_loader.py`
- Modify: `musubi/tests/test_skill_applies_to.py`

**Interfaces:**
- Consumes: existing `SkillMeta(skill_id, title, path, applies_to=None)`.
- Produces:
  - `SkillMeta.description: str`
  - `SkillMeta.triggers: list[str]`
  - `SkillMeta.tools: list[str]`
  - `list_skills(...)` returns those fields populated from frontmatter.

- [ ] **Step 1: Write failing metadata parser tests**

Add to `musubi/tests/test_skill_applies_to.py`:

```python
def test_list_skills_parses_recommender_metadata(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skill = skills / "compression-aware-context" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        "name: compression-aware-context\n"
        "description: Use compression markers safely.\n"
        "triggers:\n"
        "  - musubi_retrieve\n"
        "  - compressed output\n"
        "tools:\n"
        "  - musubi_retrieve\n"
        "  - musubi_compression_stats\n"
        "---\n"
        "# Compression-aware Context\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    by_id = {s.skill_id: s for s in list_skills(skills_dir=skills)}

    meta = by_id["compression-aware-context"]
    assert meta.description == "Use compression markers safely."
    assert meta.triggers == ["musubi_retrieve", "compressed output"]
    assert meta.tools == ["musubi_retrieve", "musubi_compression_stats"]


def test_skill_meta_recommender_fields_default_empty() -> None:
    meta = SkillMeta(skill_id="x", title="X", path="/tmp/x/SKILL.md")

    assert meta.description == ""
    assert meta.triggers == []
    assert meta.tools == []
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_skill_applies_to.py -q -p no:cacheprovider
```

Expected: FAIL because `SkillMeta` has no `description`, `triggers`, or `tools` fields.

- [ ] **Step 3: Implement metadata fields**

In `musubi/skills/skill_loader.py`, update the dataclass:

```python
@dataclass
class SkillMeta:
    skill_id: str
    title: str
    path: str
    applies_to: dict[str, list[str]] | None = field(default=None)
    description: str = ""
    triggers: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
```

Add helper:

```python
def _coerce_str_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, (str, int, float, bool)):
        value = str(raw).strip()
        return [value] if value else []
    return []
```

Update `list_skills(...)` construction:

```python
frontmatter = _parse_frontmatter(text)
applies_to = _coerce_applies_to(frontmatter.get("applies-to"))
description = str(frontmatter.get("description") or "").strip()
triggers = _coerce_str_list(frontmatter.get("triggers"))
tools = _coerce_str_list(frontmatter.get("tools"))
skills.append(SkillMeta(
    skill_id=skill_id,
    title=title,
    path=str(skill_path),
    applies_to=applies_to,
    description=description,
    triggers=triggers,
    tools=tools,
))
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_skill_applies_to.py -q -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add musubi/skills/skill_loader.py musubi/tests/test_skill_applies_to.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(skills): parse recommendation metadata"
```

---

### Task 2: Add Deterministic Skill Recommender Core

**Files:**
- Create: `musubi/skills/recommender.py`
- Create: `musubi/tests/test_skill_recommender.py`

**Interfaces:**
- Consumes: `SkillMeta` from `skills.skill_loader`.
- Produces:
  - `SkillRecommendation` dataclass.
  - `recommend_skills(task: str, skills: list[SkillMeta], context_summary: str = "", tools_used: list[str] | None = None, limit: int = 5) -> list[SkillRecommendation]`.

- [ ] **Step 1: Write failing recommender tests**

Create `musubi/tests/test_skill_recommender.py`:

```python
from __future__ import annotations

from skills.recommender import recommend_skills
from skills.skill_loader import SkillMeta


def _meta(
    skill_id: str,
    *,
    title: str | None = None,
    description: str = "",
    triggers: list[str] | None = None,
    tools: list[str] | None = None,
) -> SkillMeta:
    return SkillMeta(
        skill_id=skill_id,
        title=title or skill_id,
        path=f"/skills/{skill_id}/SKILL.md",
        description=description,
        triggers=triggers or [],
        tools=tools or [],
    )


def test_recommends_skill_by_trigger_text() -> None:
    skills = [
        _meta("compression-aware-context", triggers=["musubi_retrieve", "compressed output"]),
        _meta("research", triggers=["web search"]),
    ]

    out = recommend_skills(
        "Review this compressed output and decide whether to call musubi_retrieve.",
        skills,
    )

    assert [r.skill_id for r in out] == ["compression-aware-context"]
    assert out[0].confidence > 0.5
    assert "trigger" in out[0].reasons[0]


def test_recommends_skill_by_tool_used() -> None:
    skills = [
        _meta("compression-aware-context", tools=["musubi_retrieve"]),
        _meta("docs-writing", tools=[]),
    ]

    out = recommend_skills(
        "Continue the task.",
        skills,
        tools_used=["musubi_retrieve"],
    )

    assert out[0].skill_id == "compression-aware-context"
    assert any("tool" in reason for reason in out[0].reasons)


def test_returns_empty_when_no_signal_matches() -> None:
    skills = [_meta("research", triggers=["web search"])]

    assert recommend_skills("Rename this local variable.", skills) == []


def test_respects_limit_and_score_order() -> None:
    skills = [
        _meta("one", triggers=["alpha"], tools=["musubi_read_file"]),
        _meta("two", triggers=["alpha"]),
        _meta("three", triggers=["alpha"]),
    ]

    out = recommend_skills(
        "alpha",
        skills,
        tools_used=["musubi_read_file"],
        limit=2,
    )

    assert [r.skill_id for r in out] == ["one", "two"]
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_skill_recommender.py -q -p no:cacheprovider
```

Expected: FAIL with `ModuleNotFoundError: No module named 'skills.recommender'`.

- [ ] **Step 3: Implement recommender**

Create `musubi/skills/recommender.py`:

```python
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
    tools = [t for t in (tools_used or []) if t]
    text = _normalize(" ".join([task or "", context_summary or "", " ".join(tools)]))
    scored: list[tuple[int, str, SkillRecommendation]] = []

    for meta in skills:
        score, reasons = _score_skill(meta, text, tools)
        if score <= 0:
            continue
        confidence = min(0.99, round(score / 100, 2))
        scored.append((
            score,
            meta.skill_id,
            SkillRecommendation(
                skill_id=meta.skill_id,
                title=meta.title,
                confidence=confidence,
                reasons=reasons,
            ),
        ))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored[:max(0, limit)]]


def _score_skill(meta: SkillMeta, text: str, tools_used: list[str]) -> tuple[int, list[str]]:
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
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_skill_recommender.py -q -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add musubi/skills/recommender.py musubi/tests/test_skill_recommender.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(skills): add deterministic recommender"
```

---

### Task 3: Expose `musubi_recommend_skills` MCP Tool

**Files:**
- Modify: `musubi/server.py`
- Modify: `musubi/agent/boundary.py`
- Modify: `musubi/tests/test_skill_recommender.py`

**Interfaces:**
- Consumes:
  - `recommend_skills(...)` from Task 2.
  - `AGENT_SKILL_ALLOWLIST`.
  - `skill_router.applicable_skills(...)`.
- Produces:
  - `musubi_recommend_skills(task: str, agent_name: str, context_summary: str = "", tools_used: list[str] | None = None, limit: int = 5) -> str`.
  - JSON string shape:
    ```json
    {
      "agent_name": "agent",
      "recommended": [
        {
          "skill_id": "compression-aware-context",
          "title": "Compression-aware Context",
          "confidence": 0.75,
          "reasons": ["trigger matched: musubi_retrieve"]
        }
      ],
      "filtered_by_profile": false
    }
    ```

- [ ] **Step 1: Write failing server integration tests**

Append to `musubi/tests/test_skill_recommender.py`:

```python
import json

import server
from validation.context_builder import AGENT_SKILL_ALLOWLIST


def test_server_recommend_skills_respects_agent_allowlist(
    monkeypatch,
) -> None:
    metas = [
        _meta(
            "compression-aware-context",
            title="Compression-aware Context",
            triggers=["musubi_retrieve"],
            tools=["musubi_retrieve"],
        ),
        _meta("code-review", title="Code Review", triggers=["security"]),
    ]
    monkeypatch.setattr(server.skill_loader, "list_skills", lambda: metas)
    monkeypatch.setattr(server, "_load_project_profile", lambda: None)
    monkeypatch.setitem(
        AGENT_SKILL_ALLOWLIST,
        "agent",
        {"compression-aware-context"},
    )

    payload = json.loads(server.musubi_recommend_skills(
        task="The output contains musubi_retrieve markers.",
        agent_name="agent",
    ))

    assert payload["agent_name"] == "agent"
    assert payload["filtered_by_profile"] is False
    assert [item["skill_id"] for item in payload["recommended"]] == [
        "compression-aware-context",
    ]


def test_server_recommend_skills_applies_project_profile(monkeypatch) -> None:
    metas = [
        _meta(
            "python",
            title="Python",
            triggers=["pytest"],
            tools=[],
        ),
        SkillMeta(
            skill_id="rust-only",
            title="Rust Only",
            path="/skills/rust-only/SKILL.md",
            applies_to={"languages": ["rust"]},
            triggers=["pytest"],
        ),
    ]
    monkeypatch.setattr(server.skill_loader, "list_skills", lambda: metas)
    monkeypatch.setattr(
        server,
        "_load_project_profile",
        lambda: {"language": "python", "secondary_languages": []},
    )
    monkeypatch.setitem(AGENT_SKILL_ALLOWLIST, "agent", {"python", "rust-only"})

    payload = json.loads(server.musubi_recommend_skills(
        task="pytest is failing",
        agent_name="agent",
    ))

    assert payload["filtered_by_profile"] is True
    assert [item["skill_id"] for item in payload["recommended"]] == ["python"]
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_skill_recommender.py -q -p no:cacheprovider
```

Expected: FAIL because `server.musubi_recommend_skills` does not exist.

- [ ] **Step 3: Implement server tool**

In `musubi/server.py`, add import near existing skill imports:

```python
from skills.recommender import recommend_skills
```

Add after `musubi_list_skills(...)`:

```python
@mcp.tool()
def musubi_recommend_skills(
    task: str,
    agent_name: str,
    context_summary: str = "",
    tools_used: list[str] | None = None,
    limit: int = 5,
) -> str:
    """Return deterministic skill recommendations for the calling agent.

    This ranks only skills the caller may already load. It never injects skill
    content and never widens AGENT_SKILL_ALLOWLIST.
    """
    key = agent_name.lower().strip()
    allowed = AGENT_SKILL_ALLOWLIST.get(key, set())
    metas = [m for m in skill_loader.list_skills() if m.skill_id in allowed]
    profile = _load_project_profile()
    applicable = skill_router.applicable_skills(profile, metas)
    recommended = recommend_skills(
        task,
        applicable,
        context_summary=context_summary,
        tools_used=tools_used or [],
        limit=limit,
    )
    return json.dumps({
        "agent_name": key,
        "recommended": [
            {
                "skill_id": item.skill_id,
                "title": item.title,
                "confidence": item.confidence,
                "reasons": item.reasons,
            }
            for item in recommended
        ],
        "filtered_by_profile": profile is not None,
    })
```

In `musubi/agent/boundary.py`, add to `_READLIKE_GOVERNANCE_TOOLS`:

```python
"musubi_recommend_skills",
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_skill_recommender.py musubi\tests\test_agent_loop.py -q -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add musubi/server.py musubi/agent/boundary.py musubi/tests/test_skill_recommender.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(skills): expose skill recommendations"
```

---

### Task 4: Add Compression-Aware Skill And Prompt Steering

**Files:**
- Create: `.github/skills/compression-aware-context/SKILL.md`
- Modify: `musubi/validation/context_builder.py`
- Modify: `musubi/agent/context.py`
- Modify: `musubi/tests/test_skill_access.py`
- Modify: `musubi/tests/test_context.py`

**Interfaces:**
- Consumes:
  - `musubi_retrieve(ref_id)`
  - `musubi_compression_stats()`
  - `musubi_recommend_skills(...)`
- Produces:
  - A pullable `compression-aware-context` skill.
  - A short prompt instruction that recommends calling `musubi_recommend_skills` when procedural knowledge may be missing.

- [ ] **Step 1: Write failing access and prompt tests**

Add to `musubi/tests/test_skill_access.py`:

```python
def test_agent_can_load_compression_aware_context_skill() -> None:
    raw = server.musubi_get_skill("compression-aware-context", "agent")

    assert "Compression-aware Context" in raw
    assert "musubi_retrieve" in raw
```

Add to `musubi/tests/test_context.py`:

```python
def test_system_prompt_mentions_skill_recommendations() -> None:
    from agent.context import build_system_prompt

    prompt = build_system_prompt()

    assert "musubi_recommend_skills" in prompt
    assert "procedural knowledge" in prompt
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_skill_access.py musubi\tests\test_context.py -q -p no:cacheprovider
```

Expected: FAIL because the skill file and prompt instruction do not exist.

- [ ] **Step 3: Add compression-aware skill**

Create `.github/skills/compression-aware-context/SKILL.md`:

```markdown
---
name: compression-aware-context
description: Use Musubi compression summaries, retrieve markers, and compression stats safely.
triggers:
  - musubi_retrieve
  - compressed output
  - retrieve marker
  - compression stats
  - token savings
tools:
  - musubi_retrieve
  - musubi_compression_stats
  - musubi_compress
---

# Compression-aware Context

Use this skill when tool output, file content, logs, JSON, code, or prose has
been compressed before reaching the model.

## Rules

- Treat compressed content as a structural summary, not as the verbatim source.
- If exact text, exact field values, exact stack frames, or exact code bodies
  matter, call `musubi_retrieve(ref_id)` before making claims.
- Never invent details hidden behind a retrieve marker.
- Use `musubi_compression_stats()` when the user asks how much compression
  helped or when reporting end-of-session savings.
- If a payload is small or not actually compressed, continue normally.

## Workflow

1. Read the compressed summary for shape and relevance.
2. Identify whether the task needs exact original detail.
3. Retrieve only the specific `ref_id` needed for correctness.
4. Continue from the retrieved original when exactness matters.
5. Report token or character savings as measured runtime data, not as a price
   or credit guarantee.
```

- [ ] **Step 4: Allow the root agent to load the skill**

In `musubi/validation/context_builder.py`, update the root agent allowlist:

```python
"agent": {
    "agent-routing",
    "compression-aware-context",
    "docs-writing",
    "research",
},
```

- [ ] **Step 5: Add prompt steering**

In `musubi/agent/context.py`, add this sentence to the stable system prompt:

```text
If procedural knowledge may be missing, call `musubi_recommend_skills` and then pull only the most relevant skill with `musubi_get_skill`.
```

- [ ] **Step 6: Run tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_skill_access.py musubi\tests\test_context.py -q -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add .github/skills/compression-aware-context/SKILL.md musubi/validation/context_builder.py musubi/agent/context.py musubi/tests/test_skill_access.py musubi/tests/test_context.py
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "feat(skills): add compression-aware context guidance"
```

---

### Task 5: Docs, Roadmap, And Full Verification

**Files:**
- Modify: `docs/guide.md`
- Modify: `docs/roadmap.md`
- Optionally modify: `README.md`

**Interfaces:**
- Consumes: public tool `musubi_recommend_skills`.
- Produces: user-facing docs describing skill recommendation as deterministic, zero-LLM, and non-injective.

- [ ] **Step 1: Update guide docs**

In `docs/guide.md`, add a short subsection under the skills or standalone agent area:

```markdown
### Skill recommendations

The standalone agent can call `musubi_recommend_skills` when it is unsure which
procedural knowledge applies. The tool is deterministic: it ranks only skills
already visible to the caller after the role allowlist and project-profile
filters. It returns a shortlist with reasons; the agent still pulls full skill
content with `musubi_get_skill`.
```

- [ ] **Step 2: Update roadmap**

In `docs/roadmap.md`, add one completed or next item under still-live substrate work:

```markdown
- **Skill recommendation router.** Deterministically ranks the already-visible
  skill catalog for the standalone agent so procedural knowledge can be pulled
  on demand without bloating the system prompt or weakening skill allowlists.
```

- [ ] **Step 3: Run full focused verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi\tests\test_skill_applies_to.py musubi\tests\test_skill_router.py musubi\tests\test_skill_access.py musubi\tests\test_skill_recommender.py musubi\tests\test_context.py musubi\tests\test_agent_loop.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe scripts\check_musubi_tier.py
git diff --check
```

Expected:

```text
all selected tests pass
[check-musubi-tier] OK
git diff --check exits 0
```

- [ ] **Step 4: Commit docs**

```powershell
git add docs/guide.md docs/roadmap.md README.md
git -c user.name='Eurus' -c user.email='t.hoang7895@gmail.com' commit -m "docs(skills): document skill recommendations"
```

---

## Self-Review

- Spec coverage: The plan covers deterministic recommendation, metadata, MCP exposure, policy boundary, prompt steering, compression-aware skill, docs, and tests.
- Placeholder scan: No placeholder markers or unspecified implementation steps remain.
- Type consistency: `SkillMeta.triggers/tools` are defined in Task 1 and consumed by `recommend_skills` in Task 2; `musubi_recommend_skills` returns JSON string like existing MCP tools.

## Execution Choice

Plan complete and saved to `docs/superpowers/plans/2026-07-01-skill-recommender.md`. Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.

Choose one before implementation begins.
