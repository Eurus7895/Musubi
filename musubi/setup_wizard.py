"""`musubi setup` — guided onboarding wizard.

musubi-tier: substrate
expires-when: never — onboarding a fresh install (deps, LLM endpoint config,
  VS Code MCP wiring) is durable regardless of any pipeline-shape churn.

Full-onboarding flow, invoked as `musubi setup`:

    1. doctor      — Python / core deps / curl checklist
    2. LLM endpoint — interactively build a `.musubi/llm.json` profile
    3. connection  — optional live ping of the chosen endpoint
    4. mcp.json    — generate/merge `.vscode/mcp.json` for the extension
    5. summary     — next steps

Design: the pure helpers (doctor, profile/json/mcp renderers, connection test)
carry the logic and are unit-tested without a TTY; `run_interactive` is the
thin shell with injectable `prompt`/`out`/`root` so tests can script answers.

No secret is ever written — only `api_key_env` (the env-var *name*), matching
`agent/config.py::resolve_api_key`. Both `.musubi/llm.json` and the VS Code
`.vscode/mcp.json` are plain `json.dumps`, so no extra writer dependency.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
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

# Proxy auth schemes the curl transport understands (mirrors
# curl_router._PROXY_AUTH_FLAGS). negotiate/ntlm use the OS login with no
# stored password; basic/digest need a `user:password`.
_PROXY_AUTH_SCHEMES: tuple[str, ...] = ("negotiate", "ntlm", "basic", "digest", "anyauth")
_INTEGRATED_PROXY_AUTH: tuple[str, ...] = ("negotiate", "ntlm")

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


# Env-var NAMES are UPPER_SNAKE by convention; anything else the user types
# into the key prompt (e.g. a pasted hex token) is the secret itself.
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def classify_key_input(value: str) -> str:
    """Decide whether `value` is an env-var NAME (`api_key_env`) or the key
    itself (inline `api_key`). Empty stays `api_key_env` (no key configured)."""
    return "api_key_env" if (not value or _ENV_NAME_RE.match(value)) else "api_key"


def _apply_api_key(section: dict[str, Any], a: dict[str, Any]) -> None:
    """Copy whichever key field the answers carry into the profile section."""
    if a.get("api_key_env"):
        section["api_key_env"] = a["api_key_env"]
    elif a.get("api_key"):
        section["api_key"] = a["api_key"]


def _apply_proxy_auth(section: dict[str, Any], a: dict[str, Any]) -> None:
    """Copy proxy-auth fields (scheme + optional credentials env) if present."""
    if a.get("proxy_auth"):
        section["proxy_auth"] = a["proxy_auth"]
    if a.get("proxy_user_env"):
        section["proxy_user_env"] = a["proxy_user_env"]


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
        _apply_api_key(section, a)
        _apply_proxy_auth(section, a)
        if a.get("curl_extra_args"):
            section["curl_extra_args"] = a["curl_extra_args"]
        return section

    if family == "genai_farm":
        # On-prem gateway with the Azure deployment-in-path URL + Bearer auth.
        # SDK transport by default; a configured proxy OR a proxy_auth scheme
        # implies the curl fallback (the only transport that rides an
        # authenticated proxy / custom CA / mTLS).
        section = {
            "endpoint": a["endpoint"],
            "api_version": a["api_version"],
            "deployment": a["deployment"],
        }
        _apply_api_key(section, a)
        if a.get("proxy") or a.get("proxy_auth"):
            section["transport"] = "curl"
            if a.get("proxy"):
                section["proxy"] = a["proxy"]
            _apply_proxy_auth(section, a)
            if a.get("curl_extra_args"):
                section["curl_extra_args"] = a["curl_extra_args"]
        return section

    if family in ("openai", "anthropic"):
        section = {"model": a["model"]}
        if a.get("base_url"):
            section["base_url"] = a["base_url"]
        _apply_api_key(section, a)
        return section

    if family == "ollama":
        section = {"model": a["model"]}
        if a.get("base_url"):
            section["base_url"] = a["base_url"]
        return section

    raise ValueError(f"unknown family {family!r}")


# ── JSON render / upsert ────────────────────────────────────────────────────


def parse_existing(path: Path) -> dict[str, Any]:
    """Read an existing llm.json as a raw nested dict; {} if absent/empty/bad."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return dict(data) if isinstance(data, dict) else {}


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


def render_llm_json(raw: dict[str, Any]) -> str:
    """Render the raw nested config to llm.json text.

    The in-memory shape is already the on-disk schema (family keys → scalar
    defaults + nested profile objects), so this just orders it for a stable
    diff — `default` first, then families in `KNOWN_FAMILIES` order — and
    dumps it. The config loader (`agent/config.py`) reads it straight back.
    """
    ordered: dict[str, Any] = {}
    if raw.get("default"):
        ordered["default"] = raw["default"]
    for family in KNOWN_FAMILIES:
        fam = raw.get(family)
        if isinstance(fam, dict):
            ordered[family] = fam
    # Preserve any family the catalog doesn't know about rather than dropping it.
    for key, val in raw.items():
        if key not in ordered and key != "default":
            ordered[key] = val
    return json.dumps(ordered, indent=2) + "\n"


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


def proxy_error_hint(message: str) -> str | None:
    """A targeted next step when a failed connection looks like a proxy 407.

    Returns None for any other error so the generic FAILED line stands alone.
    """
    low = message.lower()
    if "407" in low or "proxy authentication" in low or "connect tunnel failed" in low:
        return (
            "that's a proxy 407 (authentication required). Re-run setup and set a "
            "proxy auth scheme — 'negotiate' for a Windows/Kerberos proxy needs no "
            'password. Probe it first with: curl -I --proxy-negotiate -U : "<url>"'
        )
    return None


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
        if not ok:
            hint = proxy_error_hint(msg)
            if hint:
                out(f"  hint: {hint}")

    cfg_path = root / ".musubi" / "llm.json"
    raw = upsert(parse_existing(cfg_path), family, profile, section, set_default=True)
    _write(cfg_path, render_llm_json(raw))
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
    # This profile was just written as the file's `default`, so a bare run uses
    # it. `--profile` is the only endpoint switch — vendor and model live in the
    # profile, so to change them re-run `musubi setup` or edit .musubi/llm.json.
    out(f'  agent "add a /health endpoint and a test"   # uses {family}.{profile} (the default)')
    out(f'  agent "<task>" --profile {family}.{profile}   # or name a profile explicitly')
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
        _ask_proxy(prompt, out, a, ask_url=False)
        extra = _ask(prompt, "Extra curl args (space-separated, optional)", "")
        if extra.strip():
            a["curl_extra_args"] = extra.split()
    elif family == "genai_farm":
        a["endpoint"] = _ask(prompt, "Gateway endpoint host", "https://genai-farm.internal")
        a["api_version"] = _ask(prompt, "API version", "2024-06-01")
        a["deployment"] = _ask(prompt, "Deployment / model name", _DEFAULT_MODEL[family])
        _ask_proxy(prompt, out, a, ask_url=True)
        if a.get("proxy") or a.get("proxy_auth"):
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
        val = _ask(
            prompt,
            "API key, or the NAME of an env var holding it",
            _DEFAULT_KEY_ENV[family],
        ).strip()
        kind = classify_key_input(val)
        a[kind] = val
        if kind == "api_key":
            out("  note: that looks like the key itself, not an env-var name — "
                "storing it inline in .musubi/llm.json (gitignored). To keep it "
                "out of the file, export it as an env var and enter the NAME.")
    a["profile"] = _ask(prompt, "Profile name", _DEFAULT_PROFILE[family])
    return a


def _ask(prompt: Prompt, label: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    raw = prompt(f"{label}{suffix}: ").strip()
    return raw or default


def _ask_proxy(prompt: Prompt, out: Out, a: dict[str, Any], *, ask_url: bool) -> None:
    """Collect proxy settings into `a`: an optional URL (genai_farm only — azure
    rides $HTTPS_PROXY / curl_extra_args), an auth scheme, and, for the password
    schemes, the env var holding `user:password`."""
    proxy = ""
    if ask_url:
        proxy = _ask(
            prompt,
            "Proxy URL for the curl fallback (optional, blank = none/$HTTPS_PROXY)",
            "",
        ).strip()
        if proxy:
            a["proxy"] = proxy
    scheme = _ask_proxy_auth(prompt, out)
    if scheme:
        a["proxy_auth"] = scheme
    # negotiate/ntlm authenticate as the OS login (no stored secret); basic/
    # digest need credentials. Also ask when a proxy is set without a scheme
    # (a legacy basic-auth proxy).
    if scheme in ("basic", "digest") or (proxy and not scheme):
        pu = _ask(prompt, "Env var holding proxy 'user:password' (optional)", "").strip()
        if pu:
            a["proxy_user_env"] = pu


def _ask_proxy_auth(prompt: Prompt, out: Out) -> str:
    """Ask for a proxy auth scheme; '' means none. Re-prompts on an unknown
    value (fail-closed: a typo shouldn't silently disable proxy auth)."""
    while True:
        raw = _ask(
            prompt,
            "Proxy auth for a 407 proxy (negotiate/ntlm/basic/digest, blank = none)",
            "",
        ).strip().lower()
        if not raw or raw in _PROXY_AUTH_SCHEMES:
            if raw in _INTEGRATED_PROXY_AUTH:
                out("  using your OS login for the proxy — no password stored.")
            return raw
        out(f"  '{raw}' is not one of {_PROXY_AUTH_SCHEMES}")


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
