# Musubi System Atlas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-contained interactive maintainer-level HTML atlas that explains Musubi's current architecture, causal rationale, execution flows, economics, evolution, evidence, and includes a scored quiz.

**Architecture:** Treat `artifacts/musubi-system-atlas.html` as the only runtime artifact: all content, SVG, CSS, JavaScript, quiz data, and fallbacks live in one file. A Python stdlib structural test validates the offline/evidence contract, while browser verification exercises interaction and responsive behavior. Content records separate trust zone from durability and label every claim as current fact, rationale, historical interpretation, open question, or stale contradiction.

**Tech Stack:** HTML5, embedded CSS, embedded vanilla JavaScript, inline SVG, Python 3.11 stdlib `html.parser`/`unittest`, browser automation for visual and interaction QA.

## Global Constraints

- Output exactly one portable runtime file at `artifacts/musubi-system-atlas.html`; no CDN, external font, package, image, stylesheet, script, server, or network dependency.
- Record source snapshot commit `49c58d3` and generation date `2026-07-16` in the document header.
- The first screen must teach “The driver reasons. Musubi controls the environment.” and distinguish model, driver, zero-LLM substrate, read projection, and external system.
- Trust zone and durability are independent axes; durable driver code must never be classified as zero-LLM substrate.
- Current facts require source/test/schema evidence; rationale, history, open questions, and stale contradictions require distinct visible labels.
- Whole pipelines are currently launched explicitly through CLI `--pipeline` or Orchestrator Pipeline mode; model-visible root/child surfaces do not expose `musubi_spawn_pipeline`.
- Primary pipeline stages are sequential; only same-turn sibling spawns are represented as parallel.
- External MCP calls are outside the Musubi-owned policy/audit boundary.
- Never embed secrets, environment values, live database rows, user prompts, or prior generated artifacts.
- Quiz contains at least 24 multiple-choice questions, exactly one correct answer each, causal explanations, section links, versioned `localStorage`, in-memory fallback, reset, and a complete `<noscript>` answer key.
- Core prose, diagrams, evidence, quiz questions, and answer key remain readable without JavaScript.
- Respect keyboard navigation, visible focus, semantic landmarks, text labels in addition to color, and `prefers-reduced-motion`.
- Preserve unrelated untracked files, including `vietnam-weather.html`.

---

### Task 1: Structural and evidence contract

**Files:**
- Create: `musubi/tests/test_system_atlas.py`
- Create: `artifacts/musubi-system-atlas.html`

**Interfaces:**
- Produces: `ATLAS_PATH = ROOT / "artifacts" / "musubi-system-atlas.html"` test fixture.
- Produces: HTML landmarks `orientation`, `system-map`, `components`, `traces`, `invariants`, `economics`, `evolution`, and `quiz`.
- Produces: declarative records using `data-component`, `data-trust-zone`, `data-durability`, `data-evidence-kind`, `data-scenario`, and `data-question-id` attributes.

- [ ] **Step 1: Write the failing structural test**

Create `musubi/tests/test_system_atlas.py` with stdlib-only parsing:

```python
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ATLAS_PATH = ROOT / "artifacts" / "musubi-system-atlas.html"


class AtlasParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.attrs: list[dict[str, str]] = []
        self.external_refs: list[str] = []
        self.noscript_depth = 0
        self.noscript_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        self.attrs.append(values)
        if values.get("id"):
            self.ids.add(values["id"])
        for key in ("src", "href"):
            value = values.get(key, "")
            if re.match(r"^(?:https?:)?//", value):
                self.external_refs.append(value)
        if tag == "noscript":
            self.noscript_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "noscript":
            self.noscript_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.noscript_depth:
            self.noscript_text.append(data)


def parsed_atlas() -> tuple[str, AtlasParser]:
    html = ATLAS_PATH.read_text(encoding="utf-8")
    parser = AtlasParser()
    parser.feed(html)
    return html, parser


def test_atlas_is_self_contained_and_has_required_landmarks() -> None:
    html, parser = parsed_atlas()
    assert parser.external_refs == []
    assert not re.search(r"<(?:link|script)[^>]+(?:src|href)=", html, re.I)
    assert {
        "orientation", "system-map", "components", "traces", "invariants",
        "economics", "evolution", "quiz",
    } <= parser.ids
    assert "49c58d3" in html
    assert "2026-07-16" in html


def test_atlas_records_have_complete_classification_and_quiz_contract() -> None:
    html, parser = parsed_atlas()
    components = [a for a in parser.attrs if "data-component" in a]
    questions = [a for a in parser.attrs if "data-question-id" in a]
    scenarios = [a for a in parser.attrs if "data-scenario" in a]
    assert len(components) >= 24
    assert all(a.get("data-trust-zone") and a.get("data-durability") for a in components)
    assert len(scenarios) >= 13
    assert len(questions) >= 24
    assert len({a["data-question-id"] for a in questions}) == len(questions)
    assert "noscript" in html.lower()
    assert len(" ".join(parser.noscript_text)) > 500


def test_every_embedded_source_path_exists() -> None:
    html, _ = parsed_atlas()
    paths = set(re.findall(r'data-source="([^"]+)"', html))
    assert len(paths) >= 18
    missing = sorted(path for path in paths if not (ROOT / path.split(":", 1)[0]).exists())
    assert missing == []
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_system_atlas.py -q
```

Expected: FAIL because `artifacts/musubi-system-atlas.html` does not exist.

- [ ] **Step 3: Add the minimal valid HTML shell**

Create the artifact with all required semantic landmarks, snapshot metadata,
inline `<style>`, inline `<script>`, and `<noscript>` elements. Use this exact
document-level shape:

```html
<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Musubi System Atlas — Maintainer Edition</title>
  <style>/* all styles remain inline */</style>
</head>
<body>
  <header id="orientation" data-snapshot="49c58d3" data-generated="2026-07-16"></header>
  <nav aria-label="Mục lục"></nav>
  <main>
    <section id="system-map"></section>
    <section id="components"></section>
    <section id="traces"></section>
    <section id="invariants"></section>
    <section id="economics"></section>
    <section id="evolution"></section>
    <section id="quiz"></section>
  </main>
  <aside id="evidence-drawer"></aside>
  <noscript><section aria-label="Đáp án quiz khi JavaScript bị tắt"></section></noscript>
  <script>/* all behavior remains inline */</script>
</body>
</html>
```

Add enough placeholder-free component/scenario/question records for the parser
contract; their finished content is supplied in Tasks 2 and 3.

- [ ] **Step 4: Run the structural test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_system_atlas.py -q
```

Expected: PASS with 3 tests.

- [ ] **Step 5: Commit**

```powershell
git add musubi/tests/test_system_atlas.py artifacts/musubi-system-atlas.html
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "test(atlas): define standalone guide contract"
```

### Task 2: Maintainer content and evidence-backed architecture

**Files:**
- Modify: `artifacts/musubi-system-atlas.html`
- Modify: `musubi/tests/test_system_atlas.py`

**Interfaces:**
- Consumes: Task 1 landmarks and data attributes.
- Produces: at least 24 complete component cards.
- Produces: inline SVG system map nodes whose `data-map-component` values match component IDs.
- Produces: claim badges `verified`, `rationale`, `historical`, `open`, and `stale`.
- Produces: complete execution data for 13 scenarios in `const TRACE_SCENARIOS`.

- [ ] **Step 1: Extend tests for content completeness**

Add these assertions:

```python
def test_component_cards_have_required_maintainer_fields() -> None:
    html, parser = parsed_atlas()
    required = {
        "data-responsibility", "data-why", "data-inputs", "data-outputs",
        "data-called-by", "data-depends-on", "data-enforces",
        "data-failure-modes", "data-economics", "data-source",
        "data-trust-zone", "data-durability",
    }
    components = [a for a in parser.attrs if "data-component" in a]
    assert all(required <= a.keys() for a in components)
    for badge in ("verified", "rationale", "historical", "open", "stale"):
        assert f'data-evidence-kind="{badge}"' in html


def test_current_routing_and_boundary_corrections_are_explicit() -> None:
    html, _ = parsed_atlas()
    assert "model-visible root và child không thấy musubi_spawn_pipeline" in html
    assert "same-turn" in html
    assert "external MCP" in html
    assert "ngoài Musubi-owned policy/audit boundary" in html
    assert "durable driver" in html
    assert "zero-LLM substrate" in html
```

- [ ] **Step 2: Run the tests and verify the new assertions fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_system_atlas.py -q
```

Expected: FAIL because the shell lacks complete component fields and corrected
boundary explanations.

- [ ] **Step 3: Implement the orientation and system map**

Write Vietnamese maintainer-level prose and an inline SVG with these node IDs:

```javascript
const MAP_NODES = [
  ['cli', 'CLI', 'surface'],
  ['console', 'Console', 'projection'],
  ['goal-state', 'GoalState root controller', 'driver'],
  ['worker-loop', 'run_unit worker loop', 'driver'],
  ['pipeline-runner', 'Deterministic pipeline runner', 'driver'],
  ['lm-router', 'LMRouter', 'driver'],
  ['model-provider', 'Model provider', 'external'],
  ['mcp-server', 'Musubi MCP server', 'substrate'],
  ['policy', 'Fail-closed policy', 'substrate'],
  ['evaluator-firewall', 'Evaluator firewall', 'substrate'],
  ['skills', 'Skills', 'capability'],
  ['memory', 'Memory', 'capability'],
  ['compression', 'Compression/context fit', 'capability'],
  ['state-db', 'musubi.db', 'storage'],
  ['audit-db', 'audit.db', 'storage'],
  ['rust-projection', 'Rust safe evidence boundary', 'projection'],
  ['react-view-model', 'React view model', 'projection'],
  ['external-mcp', 'External MCP federation', 'external'],
]
```

Every SVG edge has a visible verb label and a text equivalent in the evidence
drawer. Use shape to identify trust zone and pattern/border treatment to
identify durability.

- [ ] **Step 4: Implement the component atlas**

Create complete cards for all inventory entries in the design spec. Populate
source evidence from current files and representative tests. At minimum use
these sources:

```text
musubi/agent/run.py
musubi/agent/goal_state.py
musubi/agent/subagent.py
musubi/agent/pipeline_runner.py
musubi/composer.py
musubi/agent/context.py
musubi/agent/budget.py
musubi/agent/vendors/base.py
musubi/agent/mcp_gateway.py
musubi/tool_surface.py
musubi/agent/boundary.py
musubi/server.py
scripts/policy_engine.py
musubi/validation/context_builder.py
musubi/validation/subagent_context.py
musubi/execution/executor.py
musubi/skills/recommender.py
musubi/memory/memory_loader.py
musubi/compression/router.py
musubi/storage/schema.sql
musubi/storage/subagent_audit.py
gui/src-tauri/src/lib.rs
gui/src-tauri/musubi-data/src/lib.rs
gui/src/model/viewModel.js
gui/src/views/Orchestrator.jsx
gui/src/views/Pipeline.jsx
```

Use concise card summaries and expandable detail rather than duplicating full
source excerpts.

- [ ] **Step 5: Implement invariants, economics, and evolution**

Add causal cards for HI #1, #2, #3, #5, #7, #8, and #9. Add the one-owner
economics table from the spec. Add evolution entries for fixed pipelines,
unified execution primitive, VS Code removal, GoalState, builder-only Studio,
token-only accounting, pushed skills, and platform-neutral catalog relocation.

Add a documentation-archaeology panel that marks these as stale residue:

```text
worker-summoned whole pipelines in AGENTS.md/docs guide
one-level leaf wording in subagent.py
max_credits/warn_at compatibility fields
extension-side runner comments
legacy Pipeline Studio chat/session fields
root prompt maxTurns as if it were the enforced cap
```

- [ ] **Step 6: Implement all 13 trace scenario datasets**

Each scenario record has this exact shape:

```javascript
{
  id: 'direct-worker',
  title: 'Direct request → one worker',
  summary: 'Root selects a bounded worker; the worker performs mutation.',
  steps: [{
    component: 'goal-state',
    title: 'Preserve exact intent',
    input: 'User request',
    output: 'GoalState',
    decision: 'Select route and permitted root tools',
    lmCall: false,
    economics: '0 provider tokens; local CPU only',
    evidence: 'musubi/agent/goal_state.py:95',
    failure: 'Malformed scope fails before worker mutation',
  }],
}
```

Populate every required step with current source evidence and synthetic data.

- [ ] **Step 7: Run content tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_system_atlas.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add artifacts/musubi-system-atlas.html musubi/tests/test_system_atlas.py
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "docs(atlas): explain Musubi architecture and evolution"
```

### Task 3: Interaction, accessibility, and scored quiz

**Files:**
- Modify: `artifacts/musubi-system-atlas.html`
- Modify: `musubi/tests/test_system_atlas.py`

**Interfaces:**
- Consumes: Task 2 component IDs, map nodes, scenarios, and section IDs.
- Produces: `AtlasApp` namespace with `selectComponent`, `setMapMode`, `setFilters`, `selectScenario`, `selectTraceStep`, `answerQuestion`, and `resetQuiz`.
- Produces: versioned storage key `musubi-system-atlas.quiz.v1`.

- [ ] **Step 1: Add static interaction and quiz integrity tests**

Append:

```python
def test_interaction_contract_and_accessibility_markers_exist() -> None:
    html, _ = parsed_atlas()
    for name in (
        "selectComponent", "setMapMode", "setFilters", "selectScenario",
        "selectTraceStep", "answerQuestion", "resetQuiz",
    ):
        assert name in html
    assert "musubi-system-atlas.quiz.v1" in html
    assert "prefers-reduced-motion" in html
    assert 'aria-live="polite"' in html
    assert ':focus-visible' in html


def test_quiz_has_one_answer_and_explanation_per_question() -> None:
    html, _ = parsed_atlas()
    blocks = re.findall(r"const QUIZ_QUESTIONS = (\[.*?\]);\s*const", html, re.S)
    assert len(blocks) == 1
    import json
    questions = json.loads(blocks[0])
    assert len(questions) >= 24
    assert all(len(q["options"]) in (3, 4) for q in questions)
    assert all(isinstance(q["answer"], int) and 0 <= q["answer"] < len(q["options"]) for q in questions)
    assert all(q["explanation"] and q["section"] for q in questions)
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_system_atlas.py -q
```

Expected: FAIL because the shell lacks the complete interaction namespace and
JSON quiz bank.

- [ ] **Step 3: Implement component search, filters, and relationship focus**

Use an `AtlasApp` IIFE and event delegation. Search indexes component title,
responsibility, source, invariant, and failure mode. Trust-zone and durability
filters combine. Selecting a map node or card highlights the same component,
its incoming/outgoing edges, and textual relationships in the evidence drawer.

- [ ] **Step 4: Implement trace navigation**

Render scenario select, previous, next, restart, and direct step buttons. Each
step updates active component, LM-call badge, economics, evidence, failure, and
progress. Ensure `aria-current="step"` moves with selection.

- [ ] **Step 5: Implement the 24-question quiz**

Declare the question bank as valid JSON so Python can parse it:

```javascript
const QUIZ_QUESTIONS = [{
  "id": "boundary-01",
  "chapter": "orientation",
  "difficulty": "boundary",
  "prompt": "Component nào là điểm duy nhất được phép gọi model provider?",
  "options": ["MCP server", "LMRouter phía driver", "Policy engine", "Rust projection"],
  "answer": 1,
  "explanation": "HI #1 đặt model call ở driver qua LMRouter; substrate không import SDK và không gọi model.",
  "section": "orientation"
}];
const QUIZ_STORAGE_KEY = 'musubi-system-atlas.quiz.v1';
```

Cover all 12 causal chains from the spec with at least two questions per major
chapter. Lock an answered question, display the causal explanation, update
chapter/total score, and link back to `section`.

- [ ] **Step 6: Implement resilient persistence and no-JS answer key**

Wrap `localStorage` reads/writes in `try/catch`; keep the same state in memory
when storage is unavailable or payload validation fails. Reset removes the key
and restores unanswered state. Mirror all question prompts, correct option
text, explanations, and section links inside `<noscript>`.

- [ ] **Step 7: Implement responsive and reduced-motion CSS**

At `max-width: 1100px`, collapse the evidence drawer; at `max-width: 760px`,
turn both sidebars into drawers and keep SVG/trace areas horizontally
scrollable. Add visible focus, 44px touch targets where practical, minimum
16px body text, high-contrast labels, and disable nonessential transitions
under `prefers-reduced-motion: reduce`.

- [ ] **Step 8: Run the complete atlas test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_system_atlas.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add artifacts/musubi-system-atlas.html musubi/tests/test_system_atlas.py
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "feat(atlas): add interactive traces and scored quiz"
```

### Task 4: Browser verification and final evidence audit

**Files:**
- Modify if defects are found: `artifacts/musubi-system-atlas.html`
- Modify if the contract needs correction: `musubi/tests/test_system_atlas.py`
- Modify: `docs/roadmap.md`

**Interfaces:**
- Consumes: completed standalone atlas and structural tests.
- Produces: visually verified desktop/mobile artifact and current roadmap note.

- [ ] **Step 1: Run structural verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_system_atlas.py -q
```

Expected: PASS.

- [ ] **Step 2: Open the file in a browser and exercise interactions**

Use a local file URL or a temporary read-only static server. Verify:

```text
component search finds LMRouter, policy, and Pipeline Studio
trust-zone + durability filters combine
map Runtime flow and Control boundary overlays change relationships
map/card selection opens the matching evidence
all 13 scenarios support next/previous/restart/direct-step navigation
answering correct and incorrect quiz options updates score and explanation
reload restores quiz state
reset clears quiz state
blocked localStorage degrades to in-memory scoring
keyboard navigation reaches every control
```

- [ ] **Step 3: Capture desktop and mobile screenshots for visual QA**

Inspect at 1440×1000 and 390×844. Reject horizontal page overflow, clipped
drawers, unreadable SVG text, hidden focus, overlapping cards, or controls
below a usable size. Fix defects in the HTML and repeat the screenshots.

- [ ] **Step 4: Audit evidence and prohibited content**

Run:

```powershell
rg -n "TBD|TODO|PLACEHOLDER|max_credits|warn_at|estimated_credits|https?://|<script[^>]+src=|<link[^>]+href=" artifacts/musubi-system-atlas.html
```

Expected: no output except intentional stale-terminology teaching examples,
which must carry `data-evidence-kind="stale"` in the same card.

Sample-check at least 12 `data-source` line references against the source
snapshot and correct any drift.

- [ ] **Step 5: Update the roadmap**

Add a completed-track summary stating that the maintainer System Atlas now
provides an offline evidence-backed architecture map, execution trace lab,
evolution/dissolution map, and scored causal quiz. Link the design, plan, and
HTML artifact without duplicating implementation details.

- [ ] **Step 6: Run final verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest musubi/tests/test_system_atlas.py -q
git diff --check
```

Expected: all atlas tests pass and no whitespace errors.

- [ ] **Step 7: Commit**

```powershell
git add artifacts/musubi-system-atlas.html musubi/tests/test_system_atlas.py docs/roadmap.md docs/superpowers/plans/2026-07-16-musubi-system-atlas.md
git -c user.name=Eurus -c user.email=t.hoang7895@gmail.com commit -m "docs(atlas): publish verified Musubi system guide"
```
