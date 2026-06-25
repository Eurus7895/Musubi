"""Tests for the `.musubi/llm.toml` family-sectioned profile loader.

musubi-tier: substrate test — pins the endpoint-selection contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import (
    find_config_path,
    load_profile,
    resolve_api_key,
    resolve_proxy_user,
)

_SAMPLE = """
default = "azure.work"

[azure]
transport = "curl"
api_version = "2024-06-01"
azure_endpoint = "https://my.openai.azure.com"
api_key_env = "AZ_KEY"

[azure.work]
deployment = "gpt-4o"

[azure.work-mini]
deployment = "gpt-4o-mini"

[openai.cloud]
model = "gpt-5-mini"
api_key_env = "OPENAI_API_KEY"

[ollama.local]
model = "llama3.1"
"""


def _write(tmp_path: Path, text: str = _SAMPLE) -> Path:
    cfg = tmp_path / "llm.toml"
    cfg.write_text(text, encoding="utf-8")
    return cfg


def test_family_defaults_inherited_and_overridden(tmp_path: Path) -> None:
    cfg = _write(tmp_path)
    prof = load_profile("azure.work", path=cfg)
    assert prof["family"] == "azure"
    assert prof["profile"] == "work"
    assert prof["deployment"] == "gpt-4o"          # profile key
    assert prof["api_version"] == "2024-06-01"     # inherited family default
    assert prof["transport"] == "curl"             # inherited
    assert prof["azure_endpoint"] == "https://my.openai.azure.com"


def test_sibling_profiles_do_not_bleed(tmp_path: Path) -> None:
    cfg = _write(tmp_path)
    assert load_profile("azure.work-mini", path=cfg)["deployment"] == "gpt-4o-mini"


def test_default_profile_used_when_no_ref(tmp_path: Path) -> None:
    cfg = _write(tmp_path)
    prof = load_profile(None, path=cfg)
    assert (prof["family"], prof["profile"]) == ("azure", "work")


def test_bare_unique_name_resolves(tmp_path: Path) -> None:
    cfg = _write(tmp_path)
    prof = load_profile("cloud", path=cfg)
    assert prof["family"] == "openai"
    assert prof["model"] == "gpt-5-mini"


def test_bare_ambiguous_name_errors(tmp_path: Path) -> None:
    text = _SAMPLE + "\n[openai.work]\nmodel = \"gpt-4o\"\n"
    cfg = _write(tmp_path, text)
    with pytest.raises(ValueError, match="ambiguous"):
        load_profile("work", path=cfg)


def test_missing_profile_errors(tmp_path: Path) -> None:
    cfg = _write(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        load_profile("azure.nope", path=cfg)


def test_unknown_family_in_ref_errors(tmp_path: Path) -> None:
    cfg = _write(tmp_path)
    with pytest.raises(ValueError, match="unknown family"):
        load_profile("cohere.x", path=cfg)


def test_no_config_file_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.find_config_path", lambda *a, **k: None)
    with pytest.raises(FileNotFoundError, match="no .musubi/llm.toml"):
        load_profile("azure.work")


def test_find_config_path_prefers_explicit(tmp_path: Path) -> None:
    cfg = _write(tmp_path)
    assert find_config_path(cfg) == cfg


# ── api-key resolution ──────────────────────────────────────────────────────


def test_resolve_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZ_KEY", "sekret")
    assert resolve_api_key({"api_key_env": "AZ_KEY"}) == "sekret"


def test_resolve_api_key_inline() -> None:
    assert resolve_api_key({"api_key": "inline"}) == "inline"


def test_resolve_api_key_none_when_absent() -> None:
    assert resolve_api_key({"model": "x"}) is None


def test_resolve_proxy_user_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FARM_PROXY", "user:pass")
    assert resolve_proxy_user({"proxy_user_env": "FARM_PROXY"}) == "user:pass"


def test_resolve_proxy_user_inline() -> None:
    assert resolve_proxy_user({"proxy_user": "u:p"}) == "u:p"


def test_resolve_proxy_user_none_when_absent() -> None:
    assert resolve_proxy_user({"model": "x"}) is None


def test_genai_farm_family_ref_resolves(tmp_path: Path) -> None:
    text = _SAMPLE + (
        "\n[genai_farm.default]\n"
        'base_url = "https://farm.internal/v1"\n'
        'model = "gpt-5-nano"\n'
    )
    cfg = _write(tmp_path, text)
    prof = load_profile("genai_farm.default", path=cfg)
    assert prof["family"] == "genai_farm"
    assert prof["base_url"] == "https://farm.internal/v1"
