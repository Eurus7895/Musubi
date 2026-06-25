"""`musubi setup` — guided onboarding wizard.

musubi-tier: substrate
expires-when: never — onboarding a fresh install (deps, LLM endpoint config,
  VS Code MCP wiring) is durable regardless of any pipeline-shape churn.

Full-onboarding flow, invoked as `musubi setup`:

    1. doctor      — Python / core deps / curl checklist
    2. LLM endpoint — interactively build a `.musubi/llm.toml` profile
    3. connection  — optional live ping of the chosen endpoint
    4. mcp.json    — generate/merge `.vscode/mcp.json` for the extension
    5. summary     — next steps

Design: the pure helpers (doctor, profile/toml/mcp renderers, connection test)
carry the logic and are unit-tested without a TTY; `run_interactive` is the
thin shell with injectable `prompt`/`out`/`root` so tests can script answers.

No secret is ever written — only `api_key_env` (the env-var *name*), matching
`agent/config.py::resolve_api_key`. Writing TOML uses `json.dumps` for value
encoding: TOML basic strings, string arrays, and bools are JSON-compatible for
this constrained schema, so no TOML-writer dependency is needed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KNOWN_FAMILIES: tuple[str, ...] = ("azure", "genai_farm", "openai", "anthropic", "ollama")

_DEFAULT_KEY_ENV: dict[str, str] = {
    "azure": "AZURE_OPENAI_API_KEY",
    "genai_farm": "GENAI_FARM_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "ollama": "",  # local, no key
}
_DEFAULT_PROFILE: dict[str, str] = {
    "azure": "work", "genai_farm": "default", "openai": "cloud",
    "anthropic": "cloud", "ollama": "local",
}
_DEFAULT_MODEL: dict[str, str] = {
    "genai_farm": "gpt-5-nano", "openai": "gpt-5-mini",
    "anthropic": "claude-haiku-4-5", "ollama": "llama3.1",
}

Prompt = Callable[[str], str]
Out = Callable[[str], None]


# ── Doctor ──────────────────────────────────────────────────────────────────


@dataclass
class Check:
    name: str
    ok: bool
    hint: str = ""


def run_doctor() -> list[Check]:
    """Environment checklist. Non-fatal — the wizard warns and continues."""
    checks: list[Check] = []

    py_ok = sys.version_info >= (3, 11)
    checks.append(Check(
        "Python >= 3.11", py_ok,
        "" if py_ok else f"found {sys.version.split()[0]}; install Python 3.11+",
    ))
    for mod, hint in (
        ("mcp", "pip install -e ."),
        ("yaml", "pip install -e .  (pyyaml)"),
        ("starlette", "pip install -e ."),
    ):
        ok = importlib.util.find_spec(mod) is not None
        checks.append(Check(f"import {mod}", ok, "" if ok else hint))

    curl_ok = shutil.which("curl") is not None
    checks.append(Check(
        "curl on PATH", curl_ok,
        "" if curl_ok else "needed for azure/on-prem endpoints; install curl",
    ))
    return checks


def family_requirement(family: str) -> Check:
    """The extra a chosen family needs (SDK import, or curl for azure)."""
    if family == "azure":
        ok = shutil.which("curl") is not None
        return Check("curl on PATH", ok, "" if ok else "install curl for the azure transport")
    if family == "anthropic":
        ok = importlib.util.find_spec("anthropic") is not None
        return Check("anthropic SDK", ok, "" if ok else "pip install -e .[anthropic]")
    # openai + ollama + genai_farm all ride the openai SDK on the default
    # (sdk) transport; genai_farm's curl fallback additionally needs curl.
    ok = importlib.util.find_spec("openai") is not None
    return Check("openai SDK", ok, "" if ok else "pip install -e .[openai]")


# ── Profile section builder ─────────────────────────────────────────────────


def build_profile_section(family: str, answers: dict[str, Any]) -> dict[str, Any]:
    """Family answers → a flat profile-settings dict (no profile name/family)."""
    a = answers
    if family == "azure":
        section: dict[str, Any] = {
            "transport": "curl",
            "azure_endpoint": a["azure_endpoint"],
            "api_version": a["api_version"],
            "deployment": a["deployment"],
            "auth_header": "api-key",
        }
        if a.get("api_key_env"):
            section["api_key_env"] = a["api_key_env"]
        if a.get("curl_extra_args"):
            section["curl_extra_args"] = a["curl_extra_args"]
        return section

    if family == "genai_farm":
        # On-prem gateway with the Azure deployment-in-path URL + Bearer auth.
        # SDK transport by default; a configured proxy implies the curl fallback
        # (the only transport that rides an authenticated proxy / custom CA / mTLS).
        section = {
            "endpoint": a["endpoint"],
            "api_version": a["api_version"],
            "deployment": a["deployment"],
        }
        if a.get("api_key_env"):
            section["api_key_env"] = a["api_key_env"]
        if a.get("proxy"):
            section["transport"] = "curl"
            section["proxy"] = a["proxy"]
            if a.get("proxy_user_env"):
                section["proxy_user_env"] = a["proxy_user_env"]
            if a.get("curl_extra_args"):
                section["curl_extra_args"] = a["curl_extra_args"]
        return section

    if family in ("openai", "anthropic"):
        section = {"model": a["model"]}
        if a.get("base_url"):
            section["base_url"] = a["base_url"]
        if a.get("api_key_env"):
            section["api_key_env"] = a["api_key_env"]
        return section

    if family == "ollama":
        section = {"model": a["model"]}
        if a.get("base_url"):
            section["base_url"] = a["base_url"]
        return section

    raise ValueError(f"unknown family {family!r}")


# ── TOML render / upsert ────────────────────────────────────────────────────


def parse_existing(path: Path) -> dict[str, Any]:
    """Read an existing llm.toml as a raw nested dict; {} if absent/empty."""
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return dict(tomllib.load(fh))


def upsert(
    raw: dict[str, Any],
    family: str,
    profile: str,
    section: dict[str, Any],
    *,
    set_default: bool,
) -> dict[str, Any]:
    """Insert/replace `[family.profile]` in the raw config; return it."""
    fam = dict(raw.get(family) or {})
    fam[profile] = section
    raw[family] = fam
    if set_default:
        raw["default"] = f"{family}.{profile}"
    return raw


def render_llm_toml(raw: dict[str, Any]) -> str:
    """Render the raw nested config back to TOML text.

    Values are encoded with `json.dumps` — valid TOML for the strings, string
    arrays and bools this schema uses. Handles both family-level defaults
    (scalar keys under `[family]`) and `[family.profile]` sub-tables.
    """
    lines: list[str] = []
    if raw.get("default"):
        lines.append(f"default = {json.dumps(raw['default'])}")
        lines.append("")

    for family in KNOWN_FAMILIES:
        fam = raw.get(family)
        if not isinstance(fam, dict):
            continue
        scalars = {k: v for k, v in fam.items() if not isinstance(v, dict)}
        profiles = {k: v for k, v in fam.items() if isinstance(v, dict)}
        if scalars:
            lines.append(f"[{family}]")
            lines.extend(f"{k} = {json.dumps(v)}" for k, v in scalars.items())
            lines.append("")
        for pname, psec in profiles.items():
            lines.append(f"[{family}.{pname}]")
            lines.extend(f"{k} = {json.dumps(v)}" for k, v in psec.items())
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ── VS Code mcp.json ────────────────────────────────────────────────────────


def detect_server_arg(root: Path) -> str:
    """Workspace-relative arg pointing the MCP server at server.py."""
    if (root / "musubi" / "server.py").is_file():
        return "${workspaceFolder}/musubi/server.py"
    return "${workspaceFolder}/server.py"


def merge_mcp_json(existing: dict[str, Any] | None, server_arg: str) -> dict[str, Any]:
    """Add/replace the `musubi` stdio server, preserving any others."""
    data = dict(existing) if existing else {}
    servers = dict(data.get("servers") or {})
    servers["musubi"] = {
        "type": "stdio",
        "command": "python",
        "args": [server_arg],
    }
    data["servers"] = servers
    return data


# ── Connection test ─────────────────────────────────────────────────────────


def test_connection(profile: dict[str, Any]) -> tuple[bool, str]:
    """Build the router for `profile` and issue one tiny request.

    `profile` is the section dict plus a `family` key (build_from_profile
    resolves the api-key from `api_key_env` via the environment). Returns
    (ok, message); never raises.
    """
    try:
        from agent.vendors.factory import build_from_profile

        router = build_from_profile(profile)
    except Exception as exc:  # noqa: BLE001 — surface as a message
        return False, f"could not build vendor: {exc}"
    try:
        resp = router.call([{"role": "user", "content": "ping"}], [])
    except Exception as exc:  # noqa: BLE001 — network/SDK/curl error
        return False, f"{type(exc).__name__}: {exc}"
    text = "".join(b.get("text", "") for b in resp.content if b.get("type") == "text")
    return True, (text[:80].strip() or f"(stop_reason={resp.stop_reason})")


# ── Interactive shell ───────────────────────────────────────────────────────


def run_interactive(
    *,
    prompt: Prompt = input,
    out: Out = print,
    root: Path | None = None,
) -> int:
    root = root or Path.cwd()
    out("Musubi setup\n============\n")

    out("Environment:")
    for c in run_doctor():
        out(f"  [{'OK' if c.ok else '!!'}] {c.name}"
            + (f"  -> {c.hint}" if c.hint and not c.ok else ""))
    out("")

    family = _ask_choice(prompt, out, "LLM family", KNOWN_FAMILIES, default="azure")
    req = family_requirement(family)
    out(f"  [{'OK' if req.ok else '!!'}] {req.name}"
        + (f"  -> {req.hint}" if req.hint and not req.ok else ""))

    answers = _ask_family_fields(prompt, out, family)
    section = build_profile_section(family, answers)
    profile = answers["profile"]

    env_name = section.get("api_key_env")
    if env_name and not os.environ.get(env_name):
        out(f"  note: ${env_name} is not set in this environment yet.")

    if _ask_yes_no(prompt, "Test the connection now?", default=False):
        ok, msg = test_connection({**section, "family": family})
        out(f"  connection: {'OK' if ok else 'FAILED'} — {msg}")

    cfg_path = root / ".musubi" / "llm.toml"
    raw = upsert(parse_existing(cfg_path), family, profile, section, set_default=True)
    _write(cfg_path, render_llm_toml(raw))
    out(f"  wrote {cfg_path}")

    if _ask_yes_no(prompt, "Generate .vscode/mcp.json for the VS Code extension?", default=True):
        mcp_path = root / ".vscode" / "mcp.json"
        existing = json.loads(mcp_path.read_text(encoding="utf-8")) if mcp_path.is_file() else None
        merged = merge_mcp_json(existing, detect_server_arg(root))
        _write(mcp_path, json.dumps(merged, indent=4) + "\n")
        out(f"  wrote {mcp_path}")

    out("\nNext steps:")
    if env_name:
        out(f"  export {env_name}=<your key>")
    out(f'  agent "add a /health endpoint and a test" --profile {family}.{profile}')
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_interactive()


# ── prompt helpers ──────────────────────────────────────────────────────────


def _ask_family_fields(prompt: Prompt, out: Out, family: str) -> dict[str, Any]:
    a: dict[str, Any] = {}
    if family == "azure":
        a["azure_endpoint"] = _ask(prompt, "Azure endpoint", "https://my-resource.openai.azure.com")
        a["api_version"] = _ask(prompt, "API version", "2024-06-01")
        a["deployment"] = _ask(prompt, "Deployment name", "gpt-4o")
        extra = _ask(prompt, "Extra curl args (space-separated, optional)", "")
        if extra.strip():
            a["curl_extra_args"] = extra.split()
    elif family == "genai_farm":
        a["endpoint"] = _ask(prompt, "Gateway endpoint host", "https://genai-farm.internal")
        a["api_version"] = _ask(prompt, "API version", "2024-06-01")
        a["deployment"] = _ask(prompt, "Deployment / model name", _DEFAULT_MODEL[family])
        proxy = _ask(prompt, "Proxy URL for the curl fallback (optional, blank = SDK)", "")
        if proxy.strip():
            a["proxy"] = proxy.strip()
            a["proxy_user_env"] = _ask(prompt, "Env var holding proxy 'user:password' (optional)", "")
            extra = _ask(prompt, "Extra curl args (space-separated, optional)", "")
            if extra.strip():
                a["curl_extra_args"] = extra.split()
    elif family in ("openai", "anthropic"):
        a["model"] = _ask(prompt, "Model id", _DEFAULT_MODEL[family])
        base = _ask(prompt, "Base URL (optional, for a gateway)", "")
        if base.strip():
            a["base_url"] = base.strip()
    elif family == "ollama":
        a["model"] = _ask(prompt, "Model id", _DEFAULT_MODEL[family])
        base = _ask(prompt, "Ollama base URL (optional)", "")
        if base.strip():
            a["base_url"] = base.strip()

    if family != "ollama":
        a["api_key_env"] = _ask(prompt, "Env var holding the API key", _DEFAULT_KEY_ENV[family])
    a["profile"] = _ask(prompt, "Profile name", _DEFAULT_PROFILE[family])
    return a


def _ask(prompt: Prompt, label: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    raw = prompt(f"{label}{suffix}: ").strip()
    return raw or default


def _ask_choice(
    prompt: Prompt, out: Out, label: str, choices: tuple[str, ...], *, default: str
) -> str:
    out(f"{label}: {', '.join(choices)}")
    while True:
        raw = prompt(f"  choose [{default}]: ").strip().lower()
        value = raw or default
        if value in choices:
            return value
        out(f"  '{value}' is not one of {choices}")


def _ask_yes_no(prompt: Prompt, question: str, *, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = prompt(f"{question} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
