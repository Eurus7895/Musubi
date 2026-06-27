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


def test_build_genai_farm_section_integrated_proxy_auth() -> None:
    """A proxy_auth scheme alone flips the section to the curl transport (no
    proxy URL needed — curl reads $HTTPS_PROXY) and stores no credentials."""
    sec = sw.build_profile_section("genai_farm", {
        "endpoint": "https://genai-farm.internal",
        "api_version": "2024-06-01",
        "deployment": "gpt-5-nano",
        "api_key_env": "GENAI_FARM_API_KEY",
        "proxy_auth": "negotiate",
    })
    assert sec["transport"] == "curl"
    assert sec["proxy_auth"] == "negotiate"
    assert "proxy" not in sec
    assert "proxy_user_env" not in sec  # integrated auth → no stored secret


def test_build_azure_section_with_proxy_auth() -> None:
    sec = sw.build_profile_section("azure", {
        "azure_endpoint": "https://x.openai.azure.com",
        "api_version": "2024-06-01",
        "deployment": "gpt-4o",
        "api_key_env": "AZURE_OPENAI_API_KEY",
        "proxy_auth": "ntlm",
    })
    assert sec["proxy_auth"] == "ntlm"


def test_interactive_genai_farm_integrated_proxy(tmp_path: Path) -> None:
    """A genai_farm run choosing negotiate proxy auth writes a curl profile
    with proxy_auth and no stored credential."""
    script = Script([
        "genai_farm",                 # family
        "https://farm.internal",      # endpoint
        "2024-06-01",                 # api version
        "gpt-5-nano",                 # deployment
        "",                           # proxy URL (blank → use $HTTPS_PROXY)
        "negotiate",                  # proxy auth scheme
        "",                           # extra curl args
        "GENAI_FARM_API_KEY",         # api key env
        "work",                       # profile name
        "n",                          # test connection?
        "n",                          # generate mcp.json?
    ])
    rc = sw.run_interactive(prompt=script, out=_silent, root=tmp_path)
    assert rc == 0
    prof = load_profile("genai_farm.work", path=tmp_path / ".musubi" / "llm.json")
    assert prof["transport"] == "curl"
    assert prof["proxy_auth"] == "negotiate"
    assert "proxy_user_env" not in prof


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


def test_install_console_gui_runs_npm_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gui_dir = tmp_path / "gui"
    gui_dir.mkdir()
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (gui_dir / "package.json").write_text("{}", encoding="utf-8")
    calls: list[tuple[list[str], Path]] = []

    def fake_run(cmd: list[str], cwd: Path) -> int:
        calls.append((cmd, cwd))
        return 0

    monkeypatch.setattr(
        sw.shutil,
        "which",
        lambda name: f"/bin/{name}" if name in ("npm", "cargo", "link.exe") else None,
    )

    ok, message = sw.install_console_gui(tmp_path, run=fake_run)

    assert ok is True
    assert "console GUI dependencies installed" in message
    assert calls == [(["/bin/npm", "install"], tmp_path)]


def test_install_console_gui_reports_missing_npm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gui_dir = tmp_path / "gui"
    gui_dir.mkdir()
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (gui_dir / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sw.shutil, "which", lambda _name: None)

    ok, message = sw.install_console_gui(tmp_path)

    assert ok is False
    assert "npm was not found" in message


def test_install_console_gui_reports_missing_cargo_after_npm_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gui_dir = tmp_path / "gui"
    gui_dir.mkdir()
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (gui_dir / "package.json").write_text("{}", encoding="utf-8")
    calls: list[tuple[list[str], Path]] = []

    def fake_run(cmd: list[str], cwd: Path) -> int:
        calls.append((cmd, cwd))
        return 0

    monkeypatch.setattr(
        sw.shutil, "which", lambda name: "/bin/npm" if name == "npm" else None
    )

    ok, message = sw.install_console_gui(tmp_path, run=fake_run)

    assert ok is False
    assert calls == [(["/bin/npm", "install"], tmp_path)]
    assert "cargo was not found" in message
    assert "Rust toolchain" in message


# ── connection test ─────────────────────────────────────────────────────────


def test_install_console_gui_reports_missing_msvc_linker_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gui_dir = tmp_path / "gui"
    gui_dir.mkdir()
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (gui_dir / "package.json").write_text("{}", encoding="utf-8")
    calls: list[tuple[list[str], Path]] = []

    def fake_run(cmd: list[str], cwd: Path) -> int:
        calls.append((cmd, cwd))
        return 0

    monkeypatch.setattr(sw.os, "name", "nt")
    monkeypatch.setattr(
        sw.shutil,
        "which",
        lambda name: f"/bin/{name}" if name in ("npm", "cargo") else None,
    )

    ok, message = sw.install_console_gui(tmp_path, run=fake_run)

    assert ok is False
    assert calls == [(["/bin/npm", "install"], tmp_path)]
    assert "link.exe was not found" in message
    assert "Visual Studio Build Tools" in message
    assert "Microsoft.VisualStudio.Workload.VCTools" in message


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


def test_proxy_error_hint_detects_407() -> None:
    msg = "RuntimeError: curl exited 56 ...: curl: (56) CONNECT tunnel failed, response 407"
    hint = sw.proxy_error_hint(msg)
    assert hint is not None
    assert "proxy auth" in hint.lower()
    assert "negotiate" in hint


def test_proxy_error_hint_none_for_unrelated_error() -> None:
    assert sw.proxy_error_hint("openai SDK not installed") is None


def test_interactive_surfaces_proxy_hint_on_407(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the optional connection test fails with a 407, the wizard prints a
    proxy-specific hint, not just FAILED."""
    monkeypatch.setattr(
        sw, "test_connection",
        lambda _p: (False, "curl: (56) CONNECT tunnel failed, response 407"),
    )
    lines: list[str] = []
    script = Script([
        "genai_farm", "https://farm.internal", "2024-06-01", "gpt-5-nano",
        "",             # proxy URL
        "",             # proxy auth scheme (none — the misconfig we're catching)
        "GENAI_FARM_API_KEY",  # api key env
        "work",         # profile
        "y",            # test connection? → yes (fails with 407)
        "n",            # generate mcp.json?
    ])
    rc = sw.run_interactive(prompt=script, out=lines.append, root=tmp_path)
    assert rc == 0
    assert any("hint:" in ln and "negotiate" in ln for ln in lines)


# ── end-to-end interactive run (scripted) ───────────────────────────────────


def test_interactive_azure_writes_config_and_mcp(tmp_path: Path) -> None:
    script = Script([
        "azure",                              # family
        "https://x.openai.azure.com",         # endpoint
        "2024-06-01",                         # api version
        "gpt-4o",                             # deployment
        "",                                   # proxy auth scheme (none)
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


def test_interactive_installs_console_gui_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gui_dir = tmp_path / "gui"
    gui_dir.mkdir()
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (gui_dir / "package.json").write_text("{}", encoding="utf-8")
    calls: list[tuple[list[str], Path]] = []

    def fake_run(cmd: list[str], cwd: Path) -> int:
        calls.append((cmd, cwd))
        return 0

    monkeypatch.setattr(
        sw.shutil,
        "which",
        lambda name: f"/bin/{name}" if name in ("npm", "cargo", "link.exe") else None,
    )
    script = Script([
        "ollama",   # family
        "",         # model
        "",         # base url
        "",         # profile
        "n",        # test connection?
        "n",        # generate mcp.json?
        "",         # install console GUI dependencies? default yes
    ])
    lines: list[str] = []

    rc = sw.run_interactive(
        prompt=script,
        out=lines.append,
        root=tmp_path,
        gui_runner=fake_run,
    )

    assert rc == 0
    assert calls == [(["/bin/npm", "install"], tmp_path)]
    assert any("console GUI dependencies installed" in line for line in lines)


def test_interactive_guides_console_users_to_desktop_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gui_dir = tmp_path / "gui"
    gui_dir.mkdir()
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (gui_dir / "package.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        sw.shutil,
        "which",
        lambda name: f"/bin/{name}" if name in ("npm", "cargo") else None,
    )
    script = Script([
        "ollama",   # family
        "",         # model
        "",         # base url
        "",         # profile
        "n",        # test connection?
        "n",        # generate mcp.json?
        "n",        # install console GUI dependencies?
    ])
    lines: list[str] = []

    rc = sw.run_interactive(prompt=script, out=lines.append, root=tmp_path)

    assert rc == 0
    output = "\n".join(lines)
    assert "npm run tauri:dev" in output
    assert "cd app" not in output
    assert "npm run dev" not in output
    assert "browser mode" not in output
