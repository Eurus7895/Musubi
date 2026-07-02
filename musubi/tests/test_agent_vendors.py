"""Tests for the agent vendor abstraction — factory + wire converters.

musubi-tier: substrate test — every vendor router must round-trip
through the same content_blocks shape so the loop is vendor-agnostic.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.vendors import LMResponse, LMRouter
from agent.vendors.curl_router import CurlChatRouter, _auth_header_line, _resolve_url
from agent.vendors.factory import build_from_profile, build_vendor
from agent.vendors.openai_router import (
    finish_reason_to_stop,
    openai_message_to_blocks,
    to_openai_messages,
    token_budget_field,
    usage_to_dict,
)

# ── Factory env detection ──────────────────────────────────────────────────


def test_factory_explicit_anthropic_requires_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the user explicitly asks for anthropic but the SDK isn't
    available, the factory raises with a clear install hint."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    # If anthropic is installed in this env, this test is a no-op; skip.
    try:
        import anthropic  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="anthropic SDK not installed"):
            build_vendor("anthropic")


def test_factory_rejects_unknown_vendor() -> None:
    with pytest.raises(ValueError, match="Unknown agent vendor"):
        build_vendor("cohere")


def test_factory_errors_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No API key"):
        build_vendor()


def test_factory_prefers_anthropic_when_both_keys_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Documented precedence: anthropic wins the env race."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "y")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "z")
    try:
        import anthropic  # noqa: F401
    except ImportError:
        pytest.skip("anthropic SDK not installed; precedence test n/a")
    vendor = build_vendor()
    assert vendor.name == "anthropic"


# ── LMResponse / LMRouter contract ─────────────────────────────────────────


def test_lmresponse_minimal_construction() -> None:
    resp = LMResponse(stop_reason="end_turn", content=[{"type": "text", "text": "hi"}])
    assert resp.stop_reason == "end_turn"
    assert resp.content[0]["text"] == "hi"
    assert resp.usage is None


def test_lmrouter_is_abstract() -> None:
    """A subclass that forgets `call` must not instantiate."""
    with pytest.raises(TypeError):
        LMRouter()  # type: ignore[abstract]


# ── OpenAI wire converters: messages out (Anthropic → OpenAI) ──────────────


def test_openai_messages_str_user_passthrough() -> None:
    messages = [{"role": "user", "content": "hello"}]
    assert to_openai_messages(messages) == [{"role": "user", "content": "hello"}]


def test_openai_messages_system_passthrough() -> None:
    messages = [{"role": "system", "content": "stay concise"}]
    assert to_openai_messages(messages) == [
        {"role": "system", "content": "stay concise"},
    ]


def test_openai_messages_assistant_text_plus_tool_use() -> None:
    """Anthropic assistant turn with text + tool_use becomes one OpenAI
    assistant message with `tool_calls`."""
    messages = [{
        "role": "assistant",
        "content": [
            {"type": "text", "text": "thinking..."},
            {"type": "tool_use", "id": "t1", "name": "lookup", "input": {"q": "x"}},
        ],
    }]
    out = to_openai_messages(messages)
    assert len(out) == 1
    msg = out[0]
    assert msg["role"] == "assistant"
    assert msg["content"] == "thinking..."
    assert msg["tool_calls"][0]["id"] == "t1"
    assert msg["tool_calls"][0]["function"]["name"] == "lookup"


def test_openai_messages_tool_results_fan_out() -> None:
    """A user message containing N tool_results MUST become N
    role:'tool' messages — that's the OpenAI wire format. The pre-fix
    converter only emitted the first one."""
    messages = [{
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "A"},
            {"type": "tool_result", "tool_use_id": "t2", "content": "B"},
            {"type": "tool_result", "tool_use_id": "t3", "content": "C"},
        ],
    }]
    out = to_openai_messages(messages)
    assert len(out) == 3
    assert [m["tool_call_id"] for m in out] == ["t1", "t2", "t3"]
    assert [m["role"] for m in out] == ["tool", "tool", "tool"]
    assert [m["content"] for m in out] == ["A", "B", "C"]


def test_openai_messages_tool_result_content_coerced_to_string() -> None:
    messages = [{
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": {"k": "v"}},
        ],
    }]
    out = to_openai_messages(messages)
    assert out[0]["content"] == '{"k": "v"}'


# ── OpenAI wire converters: response in (OpenAI → Anthropic blocks) ────────


def test_openai_blocks_text_only() -> None:
    msg = SimpleNamespace(content="hello world", tool_calls=None)
    assert openai_message_to_blocks(msg) == [{"type": "text", "text": "hello world"}]


def test_openai_blocks_tool_call_only() -> None:
    """The response with no `content` field still yields the tool_use block."""
    call = SimpleNamespace(
        id="t1",
        function=SimpleNamespace(name="lookup", arguments='{"q":"x"}'),
    )
    msg = SimpleNamespace(content=None, tool_calls=[call])
    blocks = openai_message_to_blocks(msg)
    assert blocks == [{"type": "tool_use", "id": "t1", "name": "lookup", "input": {"q": "x"}}]


def test_openai_blocks_malformed_tool_args_become_empty_dict() -> None:
    """A vendor occasionally returns invalid JSON in arguments — the
    block must still parse with an empty input dict, never raise."""
    call = SimpleNamespace(
        id="t1",
        function=SimpleNamespace(name="lookup", arguments="{not valid json}"),
    )
    msg = SimpleNamespace(content="", tool_calls=[call])
    blocks = openai_message_to_blocks(msg)
    assert blocks == [{"type": "tool_use", "id": "t1", "name": "lookup", "input": {}}]


def test_openai_blocks_mixed_text_and_tool_use() -> None:
    call = SimpleNamespace(
        id="t1",
        function=SimpleNamespace(name="fn", arguments="{}"),
    )
    msg = SimpleNamespace(content="here goes", tool_calls=[call])
    blocks = openai_message_to_blocks(msg)
    assert [b["type"] for b in blocks] == ["text", "tool_use"]


def test_openai_blocks_from_wire_dict() -> None:
    """The curl transport hands a parsed JSON dict, not an SDK object — the
    same converter must handle both."""
    message = {
        "content": "hi",
        "tool_calls": [{"id": "t1", "function": {"name": "fn", "arguments": '{"a":1}'}}],
    }
    blocks = openai_message_to_blocks(message)
    assert blocks == [
        {"type": "text", "text": "hi"},
        {"type": "tool_use", "id": "t1", "name": "fn", "input": {"a": 1}},
    ]


def test_finish_reason_length_maps_to_max_tokens_even_with_tool_calls() -> None:
    assert finish_reason_to_stop("length") == "max_tokens"


def test_finish_reason_tool_calls_maps_to_tool_use() -> None:
    assert finish_reason_to_stop("tool_calls") == "tool_use"


def test_openai_usage_dict_normalizes_cached_prompt_tokens_from_sdk() -> None:
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        prompt_tokens_details=SimpleNamespace(cached_tokens=77),
    )
    assert usage_to_dict(usage) == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "cache_read_input_tokens": 77,
    }


def test_openai_usage_dict_normalizes_cached_prompt_tokens_from_wire_dict() -> None:
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 77},
    }
    assert usage_to_dict(usage) == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "cache_read_input_tokens": 77,
    }


# ── Token-budget field selection (max_tokens vs max_completion_tokens) ──────


@pytest.mark.parametrize(
    "model",
    ["o1", "o1-mini", "o3", "o3-mini", "o4-mini", "gpt-5", "gpt-5-nano", "GPT-5-Nano"],
)
def test_token_budget_field_new_families_use_max_completion_tokens(model: str) -> None:
    """o-series and gpt-5+ reject `max_tokens`; they need
    `max_completion_tokens` (the error this fix addresses)."""
    assert token_budget_field(model) == "max_completion_tokens"


@pytest.mark.parametrize(
    "model",
    ["gpt-4o", "gpt-4o-mini", "gpt-4", "gpt-3.5-turbo", "llama3.1", ""],
)
def test_token_budget_field_legacy_families_keep_max_tokens(model: str) -> None:
    assert token_budget_field(model) == "max_tokens"


def test_curl_router_gpt5_emits_max_completion_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gpt-5 deployment must send `max_completion_tokens` in the body, not
    the legacy `max_tokens` the API now rejects."""
    body = _capture_curl_body(monkeypatch, model="gpt-5-nano")
    assert "max_completion_tokens" in body
    assert "max_tokens" not in body


def test_curl_router_gpt4o_keeps_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _capture_curl_body(monkeypatch, model="gpt-4o")
    assert "max_tokens" in body
    assert "max_completion_tokens" not in body


def _capture_curl_body(monkeypatch: pytest.MonkeyPatch, *, model: str) -> dict:
    """Run a CurlChatRouter call and return the JSON request body it built.

    The body lives in a temp file that `_post` deletes in its `finally`, so we
    read it from the `data-binary` path *during* the faked curl invocation,
    before the cleanup runs.
    """
    captured: dict = {}

    def fake_run(cmd, input=None, capture_output=None, timeout=None, **kwargs):  # noqa: ANN001
        for line in (input or "").splitlines():
            if line.startswith("data-binary"):
                path = line.split("@", 1)[1].rstrip('"')
                captured["body"] = json.loads(Path(path).read_text(encoding="utf-8"))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "choices": [{"message": {"content": "ok", "tool_calls": None},
                             "finish_reason": "stop"}],
            }),
            stderr="",
        )

    monkeypatch.setattr("agent.vendors.curl_router.subprocess.run", fake_run)
    router = CurlChatRouter(base_url="https://gw.local/v1", model=model, api_key="K")
    router.call([{"role": "user", "content": "hi"}], [])
    return captured["body"]


def test_curl_router_decodes_response_as_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    """curl's stdout MUST be decoded as UTF-8, not the OS locale (cp1252 on
    Windows), or a non-ASCII byte in a model reply crashes the reader thread."""
    captured = _fake_curl(monkeypatch, body={
        "choices": [{"message": {"content": "ok", "tool_calls": None},
                     "finish_reason": "stop"}],
    })
    router = CurlChatRouter(base_url="https://gw.local/v1", model="m", api_key="K")
    router.call([{"role": "user", "content": "hi"}], [])
    assert captured["kwargs"].get("encoding") == "utf-8"
    assert captured["kwargs"].get("errors") == "replace"


# ── URL + auth-header construction ──────────────────────────────────────────


def test_resolve_url_azure_deployment_in_path() -> None:
    url = _resolve_url(
        url=None,
        azure_endpoint="https://my.openai.azure.com/",
        api_version="2024-06-01",
        deployment="gpt-4o",
        base_url=None,
    )
    assert url == (
        "https://my.openai.azure.com/openai/deployments/gpt-4o/chat/completions"
        "?api-version=2024-06-01"
    )


def test_resolve_url_generic_base_url() -> None:
    assert _resolve_url(
        url=None, azure_endpoint=None, api_version=None,
        deployment=None, base_url="https://gw.local/v1/",
    ) == "https://gw.local/v1/chat/completions"


def test_resolve_url_requires_an_endpoint() -> None:
    with pytest.raises(ValueError, match="curl transport needs"):
        _resolve_url(url=None, azure_endpoint=None, api_version=None,
                     deployment=None, base_url=None)


def test_auth_header_azure_default() -> None:
    assert _auth_header_line("api-key", "SECRET") == "api-key: SECRET"


def test_auth_header_bearer() -> None:
    assert _auth_header_line("Authorization: Bearer", "SECRET") == "Authorization: Bearer SECRET"


# ── CurlChatRouter: subprocess invocation ───────────────────────────────────


def _fake_curl(monkeypatch: pytest.MonkeyPatch, *, body: dict, returncode: int = 0,
               stderr: str = "") -> dict:
    """Patch curl_router.subprocess.run; return a dict that captures the call."""
    captured: dict = {}

    def fake_run(cmd, input=None, capture_output=None, timeout=None, **kwargs):  # noqa: ANN001
        captured["cmd"] = cmd
        captured["input"] = input
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=returncode, stdout=json.dumps(body), stderr=stderr)

    monkeypatch.setattr("agent.vendors.curl_router.subprocess.run", fake_run)
    return captured


def test_curl_router_azure_hides_key_in_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _fake_curl(monkeypatch, body={
        "choices": [{"message": {"content": "hi", "tool_calls": None}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    })
    router = CurlChatRouter(
        azure_endpoint="https://x.openai.azure.com",
        api_version="2024-06-01",
        deployment="gpt-4o",
        api_key="SECRET",
        auth_header="api-key",
        curl_extra_args=["--cacert", "/etc/ssl/ca.pem"],
    )
    resp = router.call([{"role": "user", "content": "hello"}], [])

    assert resp.stop_reason == "end_turn"
    assert resp.content == [{"type": "text", "text": "hi"}]
    assert resp.usage["total_tokens"] == 3

    cmd = captured["cmd"]
    assert "SECRET" not in " ".join(cmd), "api-key must never appear in argv"
    assert "--config" in cmd and "-" in cmd
    assert "--cacert" in cmd and "/etc/ssl/ca.pem" in cmd

    cfg = captured["input"]
    assert (
        'url = "https://x.openai.azure.com/openai/deployments/gpt-4o/chat/completions'
        '?api-version=2024-06-01"'
    ) in cfg
    assert "header = \"api-key: SECRET\"" in cfg
    assert "data-binary" in cfg


def test_curl_router_special_char_secrets_are_escaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """api-key and proxy password with special characters (@ # : and the
    config-breaking " and \\) must survive into the curl config intact."""
    captured = _fake_curl(monkeypatch, body={
        "choices": [{"message": {"content": "ok", "tool_calls": None},
                     "finish_reason": "stop"}],
    })
    router = CurlChatRouter(
        base_url="https://gw.local/v1",
        model="m",
        api_key='p@ss#1:2"x\\y',
        auth_header="Authorization: Bearer",
        proxy="http://proxy:8080",
        proxy_user='user:p@ss#word"\\z',
    )
    router.call([{"role": "user", "content": "hi"}], [])
    cfg = captured["input"]
    # @ # : pass through literally inside the quotes; " and \ are escaped.
    assert 'header = "Authorization: Bearer p@ss#1:2\\"x\\\\y"' in cfg
    assert 'proxy-user = "user:p@ss#word\\"\\\\z"' in cfg


def test_curl_router_proxy_auth_negotiate_defaults_empty_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`proxy_auth: negotiate` selects the scheme and, with no explicit
    credentials, uses the empty `:` so curl authenticates as the OS login."""
    captured = _fake_curl(monkeypatch, body={
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
    })
    router = CurlChatRouter(
        base_url="https://gw.local/v1", model="m", api_key="K",
        proxy_auth="negotiate",
    )
    router.call([{"role": "user", "content": "hi"}], [])
    cfg = captured["input"]
    assert "proxy-negotiate" in cfg
    assert 'proxy-user = ":"' in cfg


def test_curl_router_proxy_auth_keeps_explicit_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit proxy_user overrides the integrated-scheme `:` default."""
    captured = _fake_curl(monkeypatch, body={
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
    })
    router = CurlChatRouter(
        base_url="https://gw.local/v1", model="m", api_key="K",
        proxy_auth="ntlm", proxy_user="DOM\\u:p",
    )
    router.call([{"role": "user", "content": "hi"}], [])
    cfg = captured["input"]
    assert "proxy-ntlm" in cfg
    assert 'proxy-user = "DOM\\\\u:p"' in cfg
    assert 'proxy-user = ":"' not in cfg


def test_curl_router_basic_proxy_auth_has_no_default_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Basic isn't an integrated scheme, so no empty `:` is invented."""
    captured = _fake_curl(monkeypatch, body={
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
    })
    router = CurlChatRouter(
        base_url="https://gw.local/v1", model="m", api_key="K",
        proxy_auth="basic",
    )
    router.call([{"role": "user", "content": "hi"}], [])
    cfg = captured["input"]
    assert "proxy-basic" in cfg
    assert "proxy-user" not in cfg


def test_curl_router_unknown_proxy_auth_raises() -> None:
    with pytest.raises(ValueError, match="unknown proxy_auth"):
        CurlChatRouter(base_url="https://gw.local/v1", model="m", proxy_auth="kerb")


def test_curl_router_tool_call_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_curl(monkeypatch, body={
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{"id": "c1", "function": {"name": "look", "arguments": "{}"}}],
            },
            "finish_reason": "tool_calls",
        }],
    })
    router = CurlChatRouter(base_url="https://gw.local/v1", model="m", api_key="K")
    resp = router.call([{"role": "user", "content": "go"}], [
        {"name": "look", "description": "", "input_schema": {"type": "object"}},
    ])
    assert resp.stop_reason == "tool_use"
    assert resp.content[0]["type"] == "tool_use"
    assert resp.content[0]["name"] == "look"


def test_curl_router_length_finish_reason_surfaces_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_curl(monkeypatch, body={
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": "c1",
                    "function": {"name": "write", "arguments": "{\"path\":"},
                }],
            },
            "finish_reason": "length",
        }],
    })
    router = CurlChatRouter(base_url="https://gw.local/v1", model="m", api_key="K")

    resp = router.call([{"role": "user", "content": "go"}], [
        {"name": "write", "description": "", "input_schema": {"type": "object"}},
    ])

    assert resp.stop_reason == "max_tokens"
    assert resp.content[0]["type"] == "tool_use"


def test_curl_router_nonzero_exit_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_curl(monkeypatch, body={}, returncode=7, stderr="TLS handshake failed")
    router = CurlChatRouter(base_url="https://gw.local/v1", model="m", api_key="K")
    with pytest.raises(RuntimeError, match="curl exited 7"):
        router.call([{"role": "user", "content": "x"}], [])


# ── Ollama preset (fake openai SDK) ─────────────────────────────────────────


def test_ollama_router_points_at_local_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class FakeOpenAI:
        def __init__(self, base_url=None, api_key=None):  # noqa: ANN001
            captured["base_url"] = base_url
            captured["api_key"] = api_key

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.delenv("OLLAMA_HOST", raising=False)

    from agent.vendors.ollama_router import OllamaRouter

    router = OllamaRouter()
    assert router.name == "ollama"
    assert router.model == "llama3.1"
    assert captured["base_url"] == "http://localhost:11434/v1"
    assert captured["api_key"] == "ollama"


def test_deepseek_router_points_at_api_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class FakeOpenAI:
        def __init__(self, base_url=None, api_key=None, default_query=None):  # noqa: ANN001
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            captured["default_query"] = default_query

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")

    router = build_vendor("deepseek")

    assert router.name == "deepseek"
    assert router.model == "deepseek-v4-flash"
    assert captured["base_url"] == "https://api.deepseek.com"
    assert captured["api_key"] == "deepseek-key"
    assert captured["default_query"] is None


def test_factory_detects_deepseek_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class FakeOpenAI:
        def __init__(self, base_url=None, api_key=None, default_query=None):  # noqa: ANN001
            captured["base_url"] = base_url
            captured["api_key"] = api_key

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")

    router = build_vendor()

    assert router.name == "deepseek"
    assert captured["base_url"] == "https://api.deepseek.com"
    assert captured["api_key"] == "deepseek-key"


# ── factory.build_from_profile ──────────────────────────────────────────────


def test_build_from_profile_azure_is_curl() -> None:
    router = build_from_profile({
        "family": "azure",
        "azure_endpoint": "https://x.openai.azure.com",
        "api_version": "2024-06-01",
        "deployment": "gpt-4o",
        "api_key": "K",
        # transport omitted → azure defaults to curl
    })
    assert isinstance(router, CurlChatRouter)
    assert router.name == "azure"
    assert router.model == "gpt-4o"


def test_build_from_profile_azure_sdk_rejected() -> None:
    with pytest.raises(ValueError, match="only transport='curl'"):
        build_from_profile({"family": "azure", "transport": "sdk", "deployment": "d"})


def test_build_from_profile_genai_farm_defaults_to_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gen AI Farm rides the openai SDK by default (curl is the fallback),
    pointed at the deployment-in-path base_url with api-version on every call."""
    captured: dict = {}

    class FakeOpenAI:
        def __init__(self, base_url=None, api_key=None, default_query=None):  # noqa: ANN001
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            captured["default_query"] = default_query

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))

    router = build_from_profile({
        "family": "genai_farm",
        "endpoint": "https://genai-farm.internal",
        "api_version": "2024-06-01",
        "deployment": "gpt-5-nano",
        "api_key": "K",
        # transport omitted → defaults to sdk
    })
    assert router.name == "openai"  # OpenAIRouter wire
    assert router.model == "gpt-5-nano"
    assert captured["base_url"] == (
        "https://genai-farm.internal/openai/deployments/gpt-5-nano"
    )
    assert captured["api_key"] == "K"
    assert captured["default_query"] == {"api-version": "2024-06-01"}


def test_build_from_profile_genai_farm_curl_fallback_with_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """transport='curl' selects the curl router on the deployment-in-path URL;
    proxy + proxy-user ride the stdin config (never argv) so the proxy password
    stays hidden."""
    monkeypatch.setenv("FARM_PROXY_USER", "user:pass")
    router = build_from_profile({
        "family": "genai_farm",
        "transport": "curl",
        "endpoint": "https://genai-farm.internal",
        "api_version": "2024-06-01",
        "deployment": "gpt-5-nano",
        "api_key": "SECRET",
        "proxy": "http://proxy:8080",
        "proxy_user_env": "FARM_PROXY_USER",
    })
    assert isinstance(router, CurlChatRouter)
    assert router.name == "genai_farm"

    captured = _fake_curl(monkeypatch, body={
        "choices": [{"message": {"content": "ok", "tool_calls": None},
                     "finish_reason": "stop"}],
    })
    router.call([{"role": "user", "content": "hi"}], [])

    cfg = captured["input"]
    assert (
        'url = "https://genai-farm.internal/openai/deployments/gpt-5-nano'
        '/chat/completions?api-version=2024-06-01"'
    ) in cfg
    assert 'header = "Authorization: Bearer SECRET"' in cfg  # Bearer, not api-key
    assert 'proxy = "http://proxy:8080"' in cfg
    assert 'proxy-user = "user:pass"' in cfg
    # Neither secret is allowed on the command line.
    argv = " ".join(captured["cmd"])
    assert "SECRET" not in argv and "user:pass" not in argv


def test_build_from_profile_deepseek_defaults_to_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class FakeOpenAI:
        def __init__(self, base_url=None, api_key=None, default_query=None):  # noqa: ANN001
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            captured["default_query"] = default_query

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))

    router = build_from_profile({
        "family": "deepseek",
        "model": "deepseek-v4-pro",
        "api_key": "K",
    })

    assert router.name == "deepseek"
    assert router.model == "deepseek-v4-pro"
    assert captured["base_url"] == "https://api.deepseek.com"
    assert captured["api_key"] == "K"
    assert captured["default_query"] is None


def test_build_from_profile_unknown_family() -> None:
    with pytest.raises(ValueError, match="unknown LLM family"):
        build_from_profile({"family": "cohere", "model": "x"})


def test_build_from_profile_missing_env_key_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A declared api_key_env that isn't exported must raise — never silently
    emit a request with no Authorization header (Hard Invariant #5)."""
    monkeypatch.delenv("GENAI_FARM_API_KEY", raising=False)
    with pytest.raises(ValueError, match=r"\$GENAI_FARM_API_KEY"):
        build_from_profile({
            "family": "genai_farm",
            "transport": "curl",
            "endpoint": "https://genai-farm.internal",
            "api_version": "2024-06-01",
            "deployment": "gpt-5-nano",
            "api_key_env": "GENAI_FARM_API_KEY",
        })


def test_factory_rejects_unknown_vendor_lists_onprem() -> None:
    with pytest.raises(ValueError, match="on-prem"):
        build_vendor("cohere")
