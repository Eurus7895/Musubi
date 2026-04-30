"""Proposed patch applier — validates and applies Skill-Builder patches.

Only Behavior-Rules additions are allowed. Patches that modify tools,
input/output contracts, metadata, or non-rules sections are rejected.

Public API:
    validate_patch(patch_path) → PatchValidationResult
    apply_patch(patch_path, repo_root?) → ApplyResult
"""

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class PatchValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    target_agent: str | None = None
    addition: str | None = None


@dataclass
class ApplyResult:
    applied: bool
    patch_path: Path
    agent_path: Path | None = None
    archive_path: Path | None = None
    errors: list[str] = field(default_factory=list)


# "# Proposed Patch: coder" header
_AGENT_NAME_RE = re.compile(r"^#\s*Proposed Patch:\s*(\S+)", re.MULTILINE)

# First code block inside "## Proposed Behavior-Rules Addition"
_ADDITION_RE = re.compile(
    r"## Proposed Behavior-Rules Addition.*?```\s*\n(.*?)\n```",
    re.DOTALL,
)

# Content that signals a non-Behavior-Rules change attempt
_BLOCKED_RE = re.compile(
    r"tools\s*:|input\s+contract|output\s+contract",
    re.IGNORECASE,
)

_BEHAVIOR_RULES_RE = re.compile(
    r"^#{1,4}\s*Behavior\s+Rules\b",
    re.MULTILINE | re.IGNORECASE,
)

_PROPOSED_RE = re.compile(
    r"(^|[/\\])\.github[/\\]agents[/\\]proposed[/\\]",
    re.IGNORECASE,
)


def _is_in_proposed(path: Path) -> bool:
    return bool(_PROPOSED_RE.search(os.path.normpath(str(path))))


def validate_patch(patch_path: Path) -> PatchValidationResult:
    """Validate a proposed patch file before applying.

    Checks:
    - File exists and is inside .github/agents/proposed/
    - Contains a valid '# Proposed Patch: <agent>' header
    - Contains a '## Proposed Behavior-Rules Addition' section with a code block
    - The proposed addition does not contain non-Behavior-Rules content
    """
    if not patch_path.exists():
        return PatchValidationResult(
            valid=False, errors=[f"Patch file not found: {patch_path}"]
        )

    errors: list[str] = []

    if not _is_in_proposed(patch_path):
        errors.append("Patch file must be inside .github/agents/proposed/")

    content = patch_path.read_text(encoding="utf-8")

    name_match = _AGENT_NAME_RE.search(content)
    if not name_match:
        errors.append("Missing '# Proposed Patch: <agent_name>' header")
        target_agent = None
    else:
        target_agent = name_match.group(1).strip()

    addition_match = _ADDITION_RE.search(content)
    if not addition_match:
        errors.append(
            "Missing '## Proposed Behavior-Rules Addition' section with code block"
        )
        addition = None
    else:
        addition = addition_match.group(1).strip()

    if addition and _BLOCKED_RE.search(addition):
        errors.append(
            "Proposed addition contains non-Behavior-Rules content "
            "(tools, contracts, or metadata changes are not allowed)"
        )

    return PatchValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        target_agent=target_agent,
        addition=addition,
    )


def apply_patch(
    patch_path: Path,
    repo_root: Path | None = None,
) -> ApplyResult:
    """Validate and apply a proposed patch to the target agent file.

    On success:
    - Archives the current agent file to .github/agents/archive/
    - Appends the proposed rule(s) to the Behavior Rules section

    Returns ApplyResult with applied=False and errors if validation fails.
    """
    validation = validate_patch(patch_path)
    if not validation.valid:
        return ApplyResult(applied=False, patch_path=patch_path, errors=validation.errors)

    # proposed_patch_applier.py lives in copilot-harness/execution/, two levels below repo root
    root = repo_root or Path(__file__).parent.parent.parent
    agent_name = validation.target_agent
    addition = validation.addition

    agent_path = root / ".github" / "agents" / f"{agent_name}.agent.md"
    if not agent_path.exists():
        return ApplyResult(
            applied=False,
            patch_path=patch_path,
            errors=[f"Target agent file not found: {agent_path}"],
        )

    agent_text = agent_path.read_text(encoding="utf-8")
    if not _BEHAVIOR_RULES_RE.search(agent_text):
        return ApplyResult(
            applied=False,
            patch_path=patch_path,
            agent_path=agent_path,
            errors=["Target agent file has no 'Behavior Rules' section"],
        )

    # Archive the current version before modifying
    archive_dir = root / ".github" / "agents" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    archive_path = archive_dir / f"{agent_name}.agent.{now}.md"
    shutil.copy2(agent_path, archive_path)

    # Append the new rule(s) at the end of the Behavior Rules section (end of file)
    new_text = agent_text.rstrip("\n") + "\n" + addition + "\n"
    agent_path.write_text(new_text, encoding="utf-8")

    return ApplyResult(
        applied=True,
        patch_path=patch_path,
        agent_path=agent_path,
        archive_path=archive_path,
    )
