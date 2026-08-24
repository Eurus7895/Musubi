"""Tests for the /code-review pipeline's declarative layer — pipeline.yaml
shape, composer derivation, firewall entries, and the evaluator-name fix
in policy_engine that PR 2b needed for synthesizer spawns to resolve.

The TS runner that actually executes the pipeline ships in PR 2c; tests
for that lived in the removed VS Code extension; the standalone runner covers it now.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import composer  # noqa: E402
import policy_engine as p  # noqa: E402
from validation.context_builder import (  # noqa: E402
    AGENT_SKILL_ALLOWLIST,
    _STAGE_PERMISSIONS,
)

_PIPELINE_YAML = (
    _REPO_ROOT / ".github" / "pipelines" / "code-review" / "pipeline.yaml"
)
_GITHUB_DIR = _REPO_ROOT / ".github"


def _load() -> dict:
    with _PIPELINE_YAML.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ── pipeline.yaml shape ─────────────────────────────────────────────────────

def test_pipeline_yaml_exists() -> None:
    assert _PIPELINE_YAML.is_file()


def test_pipeline_name_is_code_review() -> None:
    assert _load()["name"] == "code-review"


def test_level_is_two() -> None:
    assert _load()["level"] == 2


def test_flat_recipe_declares_three_stages() -> None:
    assert [entry["agent"] for entry in _load()["stages"]] == [
        "scoper", "finder", "synthesizer",
    ]


def test_last_stage_is_synthesizer() -> None:
    assert _load()["stages"][-1] == {
        "agent": "synthesizer",
        "stage": "synthesis",
        "spawns": ["reviewer-aux"],
        "max_iterations": 1,
    }


def test_each_agent_declares_stage_field() -> None:
    """Phase H.1 — every agent in code-review must declare an explicit
    `stage:` field (no relying on the canonical feature-dev fallback)."""
    for entry in _load()["stages"]:
        assert isinstance(entry.get("stage"), str) and entry["stage"]


def test_agent_paths_resolve() -> None:
    for entry in _load()["stages"]:
        path = _GITHUB_DIR / "agents" / "workers" / f"{entry['agent']}.agent.md"
        assert path.is_file(), f"agent file missing: {path}"


def test_recipe_does_not_choose_runtime_skills() -> None:
    assert all("skill" not in entry for entry in _load()["stages"])


def test_synthesizer_declares_reviewer_aux_spawn() -> None:
    assert _load()["stages"][-1].get("spawns") == ["reviewer-aux"]


# ── composer derivation from pipeline.yaml ──────────────────────────────────

def test_active_stages_for_code_review() -> None:
    assert composer.active_stages("code-review") == ["scope", "findings", "synthesis"]


def test_output_stage_for_each_agent() -> None:
    assert composer.output_stage_for_agent("code-review", "scoper") == "scope"
    assert composer.output_stage_for_agent("code-review", "finder") == "findings"
    assert composer.output_stage_for_agent("code-review", "synthesizer") == "synthesis"


def test_evaluator_input_stage_is_findings() -> None:
    """The synthesizer (evaluator) reads `findings` as its input — that's
    the prior stage in the chain. Used by the runner to know what to pass."""
    assert composer.evaluator_input_stage("code-review") == "findings"


def test_skill_injection_chain() -> None:
    # scoper has no prior stage — no skill injection on read.
    assert composer.injected_skill_ids("code-review", "scope", "scoper") == []
    # finder reads scope and is injected the per-file-review checklist.
    assert composer.injected_skill_ids("code-review", "scope", "finder") == []
    # synthesizer reads findings and is injected the code-review skill.
    assert composer.injected_skill_ids("code-review", "findings", "synthesizer") == []


# ── firewall entries ────────────────────────────────────────────────────────

def test_pipeline_policies_entry_for_code_review() -> None:
    rules = p.PIPELINE_POLICIES.get("code-review")
    assert rules is not None
    assert set(rules.keys()) == {"scoper", "finder", "synthesizer"}
    # All three roles are read-only; no Bash / Write / Edit reach the LM.
    for role, tools in rules.items():
        assert "Bash" not in tools, f"{role} should not have Bash"
        assert "Write" not in tools, f"{role} should not have Write"
        assert "Edit" not in tools, f"{role} should not have Edit"


def test_main_subagent_allowlist_synthesizer_can_spawn_reviewer_aux() -> None:
    """Firewall: the synthesizer is allowed to spawn reviewer-aux (it's the
    fan-out role for the synthesis stage). scoper + finder have no spawns."""
    assert p.MAIN_SUBAGENT_ALLOWLIST["synthesizer"] == ["reviewer-aux"]
    assert p.MAIN_SUBAGENT_ALLOWLIST["scoper"] == []
    assert p.MAIN_SUBAGENT_ALLOWLIST["finder"] == []


def test_pipeline_yaml_spawns_resolve_through_firewall() -> None:
    """End-to-end: pipeline.yaml declares synthesizer.spawns = [reviewer-aux];
    intersect with firewall (also [reviewer-aux]) → effective allowed."""
    assert p.list_subagent_roles("synthesizer", "code-review") == ["reviewer-aux"]
    assert p.check_subagent_allowed(
        "synthesizer", "reviewer-aux", "code-review",
    ) is True
    assert p.check_subagent_allowed(
        "scoper", "reviewer-aux", "code-review",
    ) is False


def test_evaluator_name_drives_firewall_lookup() -> None:
    """The PR 2b fix to _load_pipeline_spawns: evaluator.name now drives the
    role key, not a hardcoded 'reviewer'. Before this fix, code-review's
    synthesizer spawns resolved to []."""
    # Reset cache in case earlier tests populated it with the buggy state.
    p._reset_pipeline_spawns_cache()
    spawns = p._load_pipeline_spawns("code-review")
    assert "synthesizer" in spawns
    assert spawns["synthesizer"] == ["reviewer-aux"]
    # And the feature-dev side still keys under "reviewer".
    feature_dev_spawns = p._load_pipeline_spawns("feature-dev")
    assert "reviewer" in feature_dev_spawns
    assert feature_dev_spawns["reviewer"] == ["reviewer-aux"]


def test_known_agent_names_includes_code_review_roles() -> None:
    """Startup validation depends on every agent name being listed in
    _KNOWN_AGENT_NAMES; otherwise PIPELINE_POLICIES validation rejects boot."""
    assert "scoper" in p._KNOWN_AGENT_NAMES
    assert "finder" in p._KNOWN_AGENT_NAMES
    assert "synthesizer" in p._KNOWN_AGENT_NAMES


def test_validate_policy_table_passes_with_code_review_entries() -> None:
    """The same validate_policy_table that runs at server import must accept
    the new code-review entries — proving boot won't reject them."""
    errors = p.validate_policy_table()
    assert errors == [], f"validate_policy_table reported: {errors}"


# ── agent skill allowlist entries ──────────────────────────────────────────

def test_skill_allowlist_for_code_review_roles() -> None:
    assert AGENT_SKILL_ALLOWLIST["scoper"] == {"pr-scope-detection"}
    assert AGENT_SKILL_ALLOWLIST["finder"] == {"per-file-review", "code-review"}
    assert AGENT_SKILL_ALLOWLIST["synthesizer"] == {"code-review", "per-file-review"}


# ── stage-read permissions ──────────────────────────────────────────────────

def test_stage_permissions_lock_synthesizer_to_findings() -> None:
    """Evaluator firewall: synthesizer reads ONLY findings — analogous to
    feature-dev's reviewer being locked to {'code'}. Cannot peek at request
    or scope. Pinned because regressing this would compromise the evaluator
    independence invariant from CLAUDE.md."""
    assert _STAGE_PERMISSIONS["synthesizer"] == {"findings"}
    # And the symmetry: reviewer is still locked to code.
    assert _STAGE_PERMISSIONS["reviewer"] == {"code"}


def test_finder_can_read_scope() -> None:
    assert "scope" in _STAGE_PERMISSIONS["finder"]


def test_scoper_reads_nothing() -> None:
    """scoper takes the raw request directly; no prior stage exists."""
    assert _STAGE_PERMISSIONS["scoper"] == set()


# (The /code-review slash-command definition left with the VS Code
# extension; the standalone entry point is `agent "<diff>" --pipeline
# code-review`, covered by test_code_review_standalone.py.)
