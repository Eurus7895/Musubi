"""Tests for the `.musubi/llm.json` family-keyed profile loader.

musubi-tier: substrate test — pins the endpoint-selection contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent.config import (
    find_config_path,
    load_profile,
    resolve_api_key,
    resolve_model_output_override,
    resolve_proxy_user,
)

_SAMPLE: dict[str, Any] = {
    "default": "azure.work",
    "azure": {
        "transport": "curl",
        "api_version": "2024-06-01",
        "azure_endpoint": "https://my.openai.azure.com",
        "api_key_env": "AZ_KEY",
        "work": {"deployment": "gpt-4o"},
        "work-mini": {"deployment": "gpt-4o-mini"},
    },
    "openai": {
        "cloud": {"model": "gpt-5-mini", "api_key_env": "OPENAI_API_KEY"},
    },
    "ollama": {
        "local": {"model": "llama3.1"},
    },
}


def _write(tmp_path: Path, obj: dict[str, Any] | None = None) -> Path:
    cfg = tmp_path / "llm.json"
    cfg.write_text(json.dumps(obj if obj is not None else _SAMPLE), encoding="utf-8")
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
    obj = json.loads(json.dumps(_SAMPLE))  # deep copy
    obj["openai"]["work"] = {"model": "gpt-4o"}
    cfg = _write(tmp_path, obj)
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
    with pytest.raises(FileNotFoundError, match="no .musubi/llm.json"):
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


def test_resolve_model_output_override_returns_positive_operator_cap() -> None:
    assert resolve_model_output_override({"max_output_tokens": 8192}) == 8192


def test_resolve_model_output_override_returns_none_when_absent_or_invalid() -> None:
    assert resolve_model_output_override({"model": "x"}) is None
    assert resolve_model_output_override({"max_output_tokens": 0}) is None
    assert resolve_model_output_override({"max_output_tokens": "8192"}) is None


def test_resolve_proxy_user_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FARM_PROXY", "user:pass")
    assert resolve_proxy_user({"proxy_user_env": "FARM_PROXY"}) == "user:pass"


def test_resolve_proxy_user_inline() -> None:
    assert resolve_proxy_user({"proxy_user": "u:p"}) == "u:p"


def test_resolve_proxy_user_none_when_absent() -> None:
    assert resolve_proxy_user({"model": "x"}) is None


def test_genai_farm_family_ref_resolves(tmp_path: Path) -> None:
    obj = json.loads(json.dumps(_SAMPLE))  # deep copy
    obj["genai_farm"] = {
        "default": {"base_url": "https://farm.internal/v1", "model": "gpt-5-nano"},
    }
    cfg = _write(tmp_path, obj)
    prof = load_profile("genai_farm.default", path=cfg)
    assert prof["family"] == "genai_farm"
    assert prof["base_url"] == "https://farm.internal/v1"


def test_deepseek_family_ref_resolves(tmp_path: Path) -> None:
    obj = json.loads(json.dumps(_SAMPLE))  # deep copy
    obj["deepseek"] = {
        "cloud": {
            "model": "deepseek-v4-flash",
            "api_key_env": "DEEPSEEK_API_KEY",
        },
    }
    cfg = _write(tmp_path, obj)

    prof = load_profile("deepseek.cloud", path=cfg)

    assert prof["family"] == "deepseek"
    assert prof["profile"] == "cloud"
    assert prof["model"] == "deepseek-v4-flash"
    assert prof["api_key_env"] == "DEEPSEEK_API_KEY"
