"""Memory injection — loads Tier 1 and Tier 2 memory files into agent context.

Tier 1 — MEMORY.md (~200 tokens, always injected by harness_read_stage)
    Pointers index: what decisions were made, where Tier 2 knowledge lives.

Tier 2 — .github/memory/*.md (loaded on demand via get_tier2_entry)
    Distilled decisions and past failure patterns. Never auto-loaded in full;
    agents request specific entries via harness_get_reference("memory", name).

Zero LLM calls. Pure file I/O.

Public API:
    get_tier1_index(repo_root?) → str | None
    get_tier2_entry(name, repo_root?) → str | None
    list_tier2_entries(repo_root?) → list[str]
    get_memory_context(repo_root?) → dict
"""

from pathlib import Path

# memory_loader.py lives in copilot-harness/ → repo root is one level up
_DEFAULT_REPO_ROOT = Path(__file__).parent.parent

_MEMORY_DIR = ".github/memory"
_TIER1_FILE = "MEMORY.md"


def _memory_dir(repo_root: Path | None = None) -> Path:
    return (repo_root or _DEFAULT_REPO_ROOT) / _MEMORY_DIR


def get_tier1_index(repo_root: Path | None = None) -> str | None:
    """Return MEMORY.md content (Tier 1 index), or None if not found."""
    path = _memory_dir(repo_root) / _TIER1_FILE
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def get_tier2_entry(name: str, repo_root: Path | None = None) -> str | None:
    """Return a named Tier 2 memory file, or None if not found.

    name must be a plain filename (no path separators). Returns None for
    path-traversal attempts or missing files.
    """
    if "/" in name or "\\" in name or ".." in name:
        return None
    path = _memory_dir(repo_root) / name
    if not path.exists() or not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def list_tier2_entries(repo_root: Path | None = None) -> list[str]:
    """Return filenames of all Tier 2 memory files (excludes MEMORY.md)."""
    mem_dir = _memory_dir(repo_root)
    if not mem_dir.exists():
        return []
    return sorted(
        p.name
        for p in mem_dir.iterdir()
        if p.is_file() and p.name != _TIER1_FILE and p.suffix == ".md"
    )


def get_memory_context(repo_root: Path | None = None) -> dict:
    """Return a dict with Tier 1 index ready for injection into harness_read_stage.

    If MEMORY.md does not exist, returns an empty dict (no memory injected).

    Shape:
        { "tier1_index": "<MEMORY.md content>",
          "tier2_available": ["architecture.md", "failure-patterns.md"] }
    """
    tier1 = get_tier1_index(repo_root)
    if not tier1:
        return {}
    return {
        "tier1_index": tier1,
        "tier2_available": list_tier2_entries(repo_root),
    }
