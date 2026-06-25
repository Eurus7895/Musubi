"""Tests for the `musubi setup` onboarding wizard.

musubi-tier: substrate test — pins the wizard's pure helpers and a scripted
end-to-end run. No TTY: prompts are injected; files land in tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import setup_wizard as sw
from agent.config import load_profile

# ── scripted prompt ─────────────────────────────────────────────────────────


class Script:
    """Sequential canned-answer prompt; ignores the prompt text."""

    def __init__(self, answers: list[str]) -> None:
        self._answers = list(answers)
        self.i = 0

    def __call__(self, _text: str) -> str:
        ans = self._answers[self.i]
        self.i += 1
        return ans


def _silent(_s: str) -> None:
    return None


# ── doctor ──────────────────────────────────────────────────────────────────


def test_run_doctor_reports_python_and_core() -> None:
    checks = sw.run_doctor()
    names = {c.name for c in checks}
    assert "Python >= 3.11" in names
    assert {"import mcp", "import yaml", "import starlette", "curl on PATH"} <= names
    py = next(c for c in checks if c.name == "Python >= 3.11")
    assert py.ok  # the test suite itself runs on 3.11+


def test_family_requirement_maps_to_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    assert sw.family_requirement("azure").name == "curl on PATH"
    assert sw.family_requirement("anthropic").name == "anthropic SDK"
    assert sw.family_requirement("openai").name == "openai SDK"
    assert sw.family_requirement("ollama").name == "openai SDK"


# ── profile section builder ─────────────────────────────────────────────────


def test_build_azure_section() -> None:
    sec = sw.build_profile_section("azure", {
        "azure_endpoint": "https://x.openai.azure.com",
        "api_version": "2024-06-01",
        "deployment": "gpt-4o",
        "api_key_env": "AZURE_OPENAI_API_KEY",
        "curl_extra_args": ["--cacert", "/p"],
    })
    assert sec["transport"] == "curl"
    assert sec["auth_header"] == "api-key"
    assert sec["deployment"] == "gpt-4o"
    assert sec["curl_extra_args"] == ["--cacert", "/p"]
    assert "api_key" not in sec  # only api_key_env, never a secret


def test_classify_key_input_env_name_vs_secret() -> None:
    # UPPER_SNAKE → treated as an env-var name.
    assert sw.classify_key_input("GENAI_FARM_API_KEY") == "api_key_env"
    assert sw.classify_key_input("") == "api_key_env"  # nothing configured
    # A pasted hex token (lowercase) is the key itself, not a var name.
    assert sw.classify_key_input("79b85de87e194a6f831c0a41f691baa2") == "api_key"
    assert sw.classify_key_input("sk-abc123") == "api_key"


def test_build_genai_farm_section_inline_key() -> None:
    """A pasted key lands in `api_key` (inline), not `api_key_env`, so the
    profile still authenticates instead of looking up a bogus env var."""
    sec = sw.build_profile_section("genai_farm", {
        "endpoint": "https://genai-farm.internal",
        "api_version": "2024-06-01",
        "deployment": "gpt-5-nano",
        "api_key": "79b85de87e194a6f831c0a41f691baa2",
    })
    assert sec["api_key"] == "79b85de87e194a6f831c0a41f691baa2"
    assert "api_key_env" not in sec


def test_build_openai_and_ollama_sections() -> None:
    oai = sw.build_profile_section(
        "openai", {"model": "gpt-5-mini", "api_key_env": "OPENAI_API_KEY"})
    assert oai == {"model": "gpt-5-mini", "api_key_env": "OPENAI_API_KEY"}
    olm = sw.build_profile_section("ollama", {"model": "llama3.1"})
    assert olm == {"model": "llama3.1"}  # no key for local


# ── TOML render / upsert (round-trips through the real config loader) ────────


def test_render_round_trips_through_config_loader(tmp_path: Path) -> None:
    raw = sw.upsert({}, "azure", "work", {
        "transport": "curl",
        "azure_endpoint": "https://x.openai.azure.com",
        "api_version": "2024-06-01",
        "deployment": "gpt-4o",
        "auth_header": "api-key",
        "api_key_env": "AZURE_OPENAI_API_KEY",
    }, set_default=True)
    text = sw.render_llm_json(raw)

    # Valid JSON…
    assert json.loads(text)["default"] == "azure.work"
    # …and the rest of the system resolves it.
    cfg = tmp_path / "llm.json"
    cfg.write_text(text, encoding="utf-8")
    prof = load_profile("azure.work", path=cfg)
    assert prof["deployment"] == "gpt-4o"
    assert prof["transport"] == "curl"
    assert prof["api_key_env"] == "AZURE_OPENAI_API_KEY"
    # No secret leaked into the file.
    assert "api_key_env" in text
    assert '"api_key"' not in text


def test_upsert_preserves_existing_profile() -> None:
    raw = sw.upsert({}, "ollama", "local", {"model": "llama3.1"}, set_default=True)
    raw = sw.upsert(raw, "openai", "cloud", {"model": "gpt-5-mini"}, set_default=False)
    assert raw["default"] == "ollama.local"
    assert raw["ollama"]["local"]["model"] == "llama3.1"
    assert raw["openai"]["cloud"]["model"] == "gpt-5-mini"


def test_render_handles_family_level_defaults() -> None:
    raw = {"default": "azure.work", "azure": {
        "api_version": "2024-06-01", "work": {"deployment": "gpt-4o"},
    }}
    parsed = json.loads(sw.render_llm_json(raw))
    assert parsed["azure"]["api_version"] == "2024-06-01"
    assert parsed["azure"]["work"]["deployment"] == "gpt-4o"


# ── mcp.json ────────────────────────────────────────────────────────────────


def test_detect_server_arg(tmp_path: Path) -> None:
    (tmp_path / "musubi").mkdir()
    (tmp_path / "musubi" / "server.py").write_text("", encoding="utf-8")
    assert sw.detect_server_arg(tmp_path) == "${workspaceFolder}/musubi/server.py"
    assert sw.detect_server_arg(tmp_path / "musubi") == "${workspaceFolder}/server.py"


def test_merge_mcp_json_preserves_other_servers() -> None:
    existing = {"servers": {"other": {"type": "stdio"}}}
    merged = sw.merge_mcp_json(existing, "${workspaceFolder}/musubi/server.py")
    assert merged["servers"]["other"] == {"type": "stdio"}
    assert merged["servers"]["musubi"]["args"] == ["${workspaceFolder}/musubi/server.py"]
    assert merged["servers"]["musubi"]["command"] == "python"


# ── connection test ─────────────────────────────────────────────────────────


def test_connection_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRouter:
        def call(self, messages, tools, *, max_tokens=4096):  # noqa: ANN001
            from agent.vendors.base import LMResponse
            return LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "pong"}])

    monkeypatch.setattr("agent.vendors.factory.build_from_profile", lambda p: FakeRouter())
    ok, msg = sw.test_connection({"family": "ollama", "model": "llama3.1"})
    assert ok and "pong" in msg


def test_connection_failure_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_p: dict[str, Any]) -> Any:
        raise RuntimeError("openai SDK not installed")

    monkeypatch.setattr("agent.vendors.factory.build_from_profile", boom)
    ok, msg = sw.test_connection({"family": "openai", "model": "x"})
    assert not ok and "openai SDK not installed" in msg


# ── end-to-end interactive run (scripted) ───────────────────────────────────


def test_interactive_azure_writes_config_and_mcp(tmp_path: Path) -> None:
    script = Script([
        "azure",                              # family
        "https://x.openai.azure.com",         # endpoint
        "2024-06-01",                         # api version
        "gpt-4o",                             # deployment
        "",                                   # extra curl args
        "",                                   # api_key_env (default)
        "",                                   # profile (default 'work')
        "n",                                  # test connection?
        "y",                                  # generate mcp.json?
    ])
    rc = sw.run_interactive(prompt=script, out=_silent, root=tmp_path)
    assert rc == 0

    cfg = tmp_path / ".musubi" / "llm.json"
    prof = load_profile(None, path=cfg)  # uses the written `default`
    assert (prof["family"], prof["profile"]) == ("azure", "work")
    assert prof["deployment"] == "gpt-4o"
    assert prof["api_key_env"] == "AZURE_OPENAI_API_KEY"
    assert '"api_key"' not in cfg.read_text(encoding="utf-8")  # no secret

    mcp = json.loads((tmp_path / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
    assert mcp["servers"]["musubi"]["command"] == "python"


def test_interactive_ollama_skips_mcp(tmp_path: Path) -> None:
    script = Script([
        "ollama",   # family
        "",         # model (default llama3.1)
        "",         # base url
        "",         # profile (default 'local')
        "n",        # test connection?
        "n",        # generate mcp.json?
    ])
    rc = sw.run_interactive(prompt=script, out=_silent, root=tmp_path)
    assert rc == 0
    prof = load_profile("ollama.local", path=tmp_path / ".musubi" / "llm.json")
    assert prof["model"] == "llama3.1"
    assert not (tmp_path / ".vscode" / "mcp.json").exists()
