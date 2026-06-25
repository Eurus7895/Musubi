"""`.musubi/llm.toml` — family-sectioned LLM endpoint profiles.

musubi-tier: substrate
expires-when: never — the resolution rule for how a standalone-agent user
  selects an LLM endpoint (cloud, local Ollama, or an on-prem Azure / Gen AI
  Farm OpenAI-compatible gateway). Read-only TOML via stdlib `tomllib`
  (3.11+); no dependency.

Config is **separated into LLM-family sections**. The section name is the
family (and thus the wire/client); family-level scalar keys are shared
defaults that each profile under the family inherits (profile keys win):

    default = "azure.work"

    [azure]                         # family defaults
    transport = "curl"
    api_version = "2024-06-01"
    azure_endpoint = "https://my.openai.azure.com"
    api_key_env = "AZURE_OPENAI_API_KEY"

    [azure.work]                    # profile — inherits the keys above
    deployment = "gpt-4o"

A profile is referenced as `<family>.<profile>` (e.g. `azure.work`), or by a
bare profile name when it is unique across families. With no ref, the file's
`default` is used. `build_from_profile` (in `vendors/factory.py`) turns the
resolved dict into an `LMRouter`.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

KNOWN_FAMILIES = frozenset({"openai", "azure", "genai_farm", "anthropic", "ollama"})


def find_config_path(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    """Resolve the llm.toml location.

    Order: explicit arg → $MUSUBI_LLM_CONFIG → ./.musubi/llm.toml →
    ~/.musubi/llm.toml. Returns None if none exists.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("MUSUBI_LLM_CONFIG")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.cwd() / ".musubi" / "llm.toml")
    candidates.append(Path.home() / ".musubi" / "llm.toml")
    for c in candidates:
        if c.is_file():
            return c
    return None


def load_profile(
    ref: str | None = None,
    *,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Resolve a profile to a flat settings dict.

    The returned dict carries the merged family-defaults + profile overrides,
    plus two injected keys: `family` and `profile` (the resolved names).

    Raises FileNotFoundError if no config file exists, and ValueError for an
    unknown/ambiguous ref or a malformed file.
    """
    cfg_path = find_config_path(path)
    if cfg_path is None:
        raise FileNotFoundError(
            "no .musubi/llm.toml found (looked at $MUSUBI_LLM_CONFIG, "
            "./.musubi/llm.toml, ~/.musubi/llm.toml)"
        )
    with cfg_path.open("rb") as fh:
        raw = tomllib.load(fh)

    resolved_ref = ref or raw.get("default")
    if not resolved_ref:
        raise ValueError(
            f"{cfg_path}: no profile given and no top-level `default` set"
        )

    family, profile = _split_ref(resolved_ref, raw)
    fam_table = raw.get(family)
    if not isinstance(fam_table, dict):
        raise ValueError(f"{cfg_path}: no [{family}] family section")

    defaults = {k: v for k, v in fam_table.items() if not isinstance(v, dict)}
    profiles = {k: v for k, v in fam_table.items() if isinstance(v, dict)}
    if profile not in profiles:
        raise ValueError(
            f"{cfg_path}: profile '{family}.{profile}' not found "
            f"(available: {sorted(profiles) or 'none'})"
        )

    merged: dict[str, Any] = {**defaults, **profiles[profile]}
    merged["family"] = family
    merged["profile"] = profile
    return merged


def _split_ref(ref: str, raw: dict[str, Any]) -> tuple[str, str]:
    """Resolve `<family>.<profile>` or a bare unique profile name."""
    if "." in ref:
        family, profile = ref.split(".", 1)
        if family not in KNOWN_FAMILIES:
            raise ValueError(
                f"unknown family '{family}' in ref '{ref}' "
                f"(known: {sorted(KNOWN_FAMILIES)})"
            )
        return family, profile

    # Bare name: search every family for a profile of that name.
    matches: list[tuple[str, str]] = []
    for family in KNOWN_FAMILIES:
        fam_table = raw.get(family)
        if isinstance(fam_table, dict) and isinstance(fam_table.get(ref), dict):
            matches.append((family, ref))
    if not matches:
        raise ValueError(f"profile '{ref}' not found in any family section")
    if len(matches) > 1:
        fams = ", ".join(f"{f}.{p}" for f, p in matches)
        raise ValueError(
            f"profile name '{ref}' is ambiguous across families ({fams}); "
            f"use the fully-qualified <family>.<profile> form"
        )
    return matches[0]


def resolve_api_key(profile: dict[str, Any]) -> str | None:
    """Resolve the api-key from `api_key_env` (preferred) or inline `api_key`.

    Inline keys are honoured but discouraged (they sit in a file); the caller
    may warn. Returns None when neither is set (e.g. Ollama).
    """
    env_name = profile.get("api_key_env")
    if env_name:
        return os.environ.get(env_name)
    return profile.get("api_key")


def resolve_proxy_user(profile: dict[str, Any]) -> str | None:
    """Resolve the `user:password` for an authenticated curl proxy.

    Preference order mirrors `resolve_api_key`: `proxy_user_env` (the NAME of
    an env var holding `user:password`) over an inline `proxy_user`. Returns
    None when neither is set (no proxy auth, or an unauthenticated proxy).
    """
    env_name = profile.get("proxy_user_env")
    if env_name:
        return os.environ.get(env_name)
    return profile.get("proxy_user")
