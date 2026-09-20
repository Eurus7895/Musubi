"""Run-local state shared by Adaptive and Pipeline adapters.

musubi-tier: substrate
expires-when: never - explicit driver and execution boundaries
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.boundary import ROOT_ROLE
from agent.decider import FailureKind, WorkerOutcome
from agent.goal_state import GoalState
from agent.planning_artifacts import persist_planning_artifacts
from agent.routes import RouteKind

DEFAULT_MAX_DEPTH = 2
DEFAULT_MAX_ROOT_WORKERS = 3
DEFAULT_MAX_ROOT_WORKERS_LARGE = 6

@dataclass
class Orchestration:
    """Context that lets a worker loop spawn further workers.

    `parent_session_id` owns the spawn parentage (always the ROOT session — the
    whole worker tree shares one session row); `parent_agent_name` is the
    firewall identity of THIS worker (the role whose `spawn_allowlist` gates what
    it may summon). `depth` is this worker's depth (0 = root). Disabled (no
    spawning) when `parent_session_id` is None.
    """

    parent_session_id: str | None
    parent_agent_name: str = ROOT_ROLE
    depth: int = 0
    max_depth: int = DEFAULT_MAX_DEPTH
    spawned_workers: int = 0
    max_root_workers: int = DEFAULT_MAX_ROOT_WORKERS
    root_recovery_analysis_cycles: int = 0
    worker_outcomes: list[WorkerOutcome] = field(default_factory=list)
    goal_state: GoalState | None = None
    pipeline_name: str | None = None
    planning_artifact_dir: Path | None = None
    work_package_controller: Any = None
    work_package_id: str | None = None
    work_package_attempt_id: str | None = None
    # The destructive gate's state deliberately does NOT live here. See
    # `_destructive_gate` below: it is run-scoped, and an Orchestration
    # describes a position in the spawn tree — the one thing the gate must be
    # blind to, since leaf workers carry no Orchestration at all.

    @property
    def enabled(self) -> bool:
        return self.parent_session_id is not None

    def child(self, role: str) -> Orchestration:
        """Orchestration for a worker this one spawns: same root session, the
        child's role as the new firewall identity, one level deeper."""
        return Orchestration(
            parent_session_id=self.parent_session_id,
            parent_agent_name=role,
            pipeline_name=self.pipeline_name,
            planning_artifact_dir=self.planning_artifact_dir,
            depth=self.depth + 1,
            max_depth=self.max_depth,
            work_package_controller=self.work_package_controller,
            work_package_id=self.work_package_id,
            work_package_attempt_id=self.work_package_attempt_id,
        )

    def stage_child(
        self, role: str, pipeline_session_id: str,
        pipeline_name: str | None = None,
    ) -> Orchestration:
        """Orchestration for one pipeline stage worker. Unlike `child`, the
        parentage moves to the PIPELINE session: the server resolves the
        pipeline from `parent_session_id` and narrows the stage's spawnable
        roles to pipeline.yaml `spawns:` ∩ firewall (fail-closed policy). Handing a stage
        the root session instead would skip that narrowing. The pipeline
        envelope itself is a sequencer, not a worker — a stage sits one level
        below the worker that summoned the pipeline."""
        return Orchestration(
            parent_session_id=pipeline_session_id,
            parent_agent_name=role,
            pipeline_name=pipeline_name,
            planning_artifact_dir=self.planning_artifact_dir,
            depth=self.depth + 1,
            max_depth=self.max_depth,
            work_package_controller=None,
        )

    @property
    def can_spawn_deeper(self) -> bool:
        """True if a worker at this depth is still allowed to nest."""
        return self.enabled and self.depth < self.max_depth

    @property
    def delivered_artifact(self) -> bool:
        """True when some worker this turn finished with files on disk.

        Persisted per turn so a LATER turn in the same conversation can see a
        run of turns that spent tokens and produced nothing.
        """
        return any(
            outcome.status == "done" and outcome.touched_files
            for outcome in self.worker_outcomes
        )

    def record_worker_outcome(
        self,
        *,
        role: str,
        status: str,
        summary: str,
        touched_files: set[str] | tuple[str, ...] | list[str],
        brief: str = "",
        failure_kind: FailureKind | None = None,
        pushed_skill_id: str | None = None,
        work_package_id: str | None = None,
        contract_hash: str | None = None,
    ) -> WorkerOutcome:
        """Retain a compact terminal record for parent-side recovery."""
        outcome = WorkerOutcome(
            role=role,
            status=status,
            summary=summary,
            touched_files=tuple(sorted(set(touched_files))),
            brief=brief,
            failure_kind=failure_kind,
            pushed_skill_id=pushed_skill_id,
            work_package_id=work_package_id,
            contract_hash=contract_hash,
        )
        self.worker_outcomes.append(outcome)
        if self.goal_state is not None:
            self.goal_state.record_outcome(
                role=role,
                status=status,
                summary=summary,
                touched_files=touched_files,
            )
            # Post-plan reclassification: a planner-led goal persists the
            # plan/manifest pair the moment it lands. The manifest verdict
            # (not the lexical guess) then owns route, scope, and the legal
            # next mutation role; a missing/invalid pair fails closed before
            # a coder can start.
            if role == "planner" and status == "done" and (
                self.goal_state.next_role == "planner"
            ):
                paths = None
                if self.planning_artifact_dir is not None:
                    try:
                        paths = persist_planning_artifacts(
                            summary,
                            self.planning_artifact_dir,
                        )
                    except OSError as exc:
                        self.goal_state.reject_planning_artifacts(
                            "The planner produced a valid plan, but Musubi "
                            f"could not persist it: {type(exc).__name__}. "
                            "Resolve the workspace write error before retrying."
                        )
                if self.planning_artifact_dir is None:
                    # Unit-level orchestration callers may omit persistence;
                    # production run_agent always supplies the directory.
                    self.goal_state.apply_planner_manifest(summary)
                elif paths is None and self.goal_state.pending_clarification is None:
                    self.goal_state.reject_planning_artifacts(
                        "The planner must produce both a non-empty <plan> "
                        "block and one valid <change_manifest> block before "
                        "implementation can start."
                    )
                elif paths is not None:
                    self.goal_state.planning_artifacts = tuple(
                        str(path) for path in paths
                    )
                    self.goal_state.apply_planner_manifest(summary)
                if self.goal_state.role_chain or (
                    self.goal_state.route == RouteKind.PLAN_DESIGN_WORKFLOW
                ):
                    # A large change owes designer → coder → reviewer after the
                    # planner. That is four workers, one more than the default
                    # ceiling, so the chain would be refused on its last step.
                    # Raise the ceiling to fit the chain plus headroom for one
                    # recovery replacement.
                    self.max_root_workers = max(
                        self.max_root_workers, DEFAULT_MAX_ROOT_WORKERS_LARGE,
                    )
        return outcome

    def latest_failed_outcome(self, role: str) -> WorkerOutcome | None:
        """Return the latest same-role failure, unless a later run recovered."""
        for outcome in reversed(self.worker_outcomes):
            if outcome.role == role:
                if outcome.status in {"failed", "escalated"}:
                    return outcome
                return None
        return None

    def latest_unrecovered_failure(self) -> WorkerOutcome | None:
        """Return the newest failure not superseded by a same-role success."""
        seen_roles: set[str] = set()
        for outcome in reversed(self.worker_outcomes):
            if outcome.role in seen_roles:
                continue
            seen_roles.add(outcome.role)
            if outcome.status in {"failed", "escalated"}:
                return outcome
        return None


@dataclass
class AgentRunStats:
    """Cumulative telemetry for one CLI turn across root and workers."""

    cycles: int = 0
    lm_ms: int = 0
    tokens_in_estimate: int = 0
    tokens_out_estimate: int = 0

    def record_cycle(
        self,
        *,
        lm_ms: int,
        tokens_in: int,
        tokens_out: int,
    ) -> None:
        self.cycles += 1
        self.lm_ms += lm_ms
        self.tokens_in_estimate += tokens_in
        self.tokens_out_estimate += tokens_out
