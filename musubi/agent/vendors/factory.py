"""Vendor selection.

musubi-tier: substrate
expires-when: never — the resolution rule for every consumer.

Two entry points:

`build_vendor(name, ...)` — ad-hoc selection for the `--vendor` flag. Resolves
    explicit `name` OR env detection (ANTHROPIC_API_KEY → "anthropic",
    OPENAI_API_KEY → "openai"). Accepts optional `base_url`/`api_key` overrides.

`build_from_profile(profile)` — builds an LMRouter from a resolved
    `.musubi/llm.toml` profile dict (see `agent.config.load_profile`). The
    profile's `family` selects the wire/client; `transport` (sdk|curl) selects
    how the HTTP call is made.

To register a new vendor, add an arm here and a router module under
`agent/vendors/`.
"""

from __future__ import annotations

import os
from typing import Any

from agent.config import resolve_api_key, resolve_proxy_user
from agent.vendors.base import LMRouter


def build_vendor(
    name: str | None = None,
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> LMRouter:
    """Construct the LMRouter for `name`, falling back to env detection."""
    resolved = (name or _detect_vendor()).lower().strip()

    if resolved == "anthropic":
        # Import lazily so users without `pip install anthropic` can still use
        # the openai/ollama vendors (and vice versa).
        from agent.vendors.anthropic_router import AnthropicRouter

        return AnthropicRouter(model=model, base_url=base_url, api_key=api_key)

    if resolved == "openai":
        from agent.vendors.openai_router import OpenAIRouter

        return OpenAIRouter(model=model, base_url=base_url, api_key=api_key)

    if resolved == "ollama":
        from agent.vendors.ollama_router import OllamaRouter

        return OllamaRouter(model=model, base_url=base_url, api_key=api_key)

    if resolved == "genai_farm":
        # On-prem OpenAI-compatible gateway, SDK transport (the default). The
        # endpoint defaults to env so the flag surface stays small; the curl
        # fallback (authenticated proxy / custom CA) is profile-only — use a
        # .musubi/llm.toml `[genai_farm]` profile with transport = "curl".
        from agent.vendors.openai_router import OpenAIRouter

        return OpenAIRouter(
            model=model,
            base_url=base_url or os.environ.get("GENAI_FARM_BASE_URL"),
            api_key=api_key or os.environ.get("GENAI_FARM_API_KEY"),
        )

    if resolved == "azure":
        # Ad-hoc Azure goes through the curl transport (the on-prem path),
        # reading the endpoint bits from env to keep the flag surface small.
        from agent.vendors.curl_router import CurlChatRouter

        return CurlChatRouter(
            deployment=model,
            azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT"),
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION"),
            base_url=base_url,
            api_key=api_key or os.environ.get("AZURE_OPENAI_API_KEY"),
            auth_header="api-key",
            name="azure",
        )

    raise ValueError(
        f"Unknown agent vendor {resolved!r}. "
        f"Supported: 'anthropic', 'openai', 'ollama', 'azure', 'genai_farm'. "
        f"For on-prem endpoints use a .musubi/llm.toml profile (--profile)."
    )


def build_from_profile(profile: dict[str, Any]) -> LMRouter:
    """Build an LMRouter from a resolved `.musubi/llm.toml` profile dict."""
    family = profile.get("family")
    model = profile.get("model") or profile.get("deployment")
    api_key = resolve_api_key(profile)
    base_url = profile.get("base_url")
    # Azure defaults to the curl transport (its endpoints typically require it);
    # everything else defaults to the SDK.
    transport = profile.get("transport") or ("curl" if family == "azure" else "sdk")

    if family == "anthropic":
        if transport != "sdk":
            raise ValueError("anthropic family supports only transport='sdk'")
        from agent.vendors.anthropic_router import AnthropicRouter

        return AnthropicRouter(model=model, base_url=base_url, api_key=api_key)

    if family == "ollama":
        from agent.vendors.ollama_router import OllamaRouter

        return OllamaRouter(model=model, base_url=base_url, api_key=api_key)

    if family == "openai":
        if transport == "curl":
            return _build_curl(profile, model, api_key, base_url, name="openai",
                               default_auth="Authorization: Bearer")
        from agent.vendors.openai_router import OpenAIRouter

        return OpenAIRouter(model=model, base_url=base_url, api_key=api_key)

    if family == "genai_farm":
        # On-prem OpenAI-compatible gateway (Bearer auth). SDK transport is the
        # default; curl is the fallback for networks that force the call through
        # an authenticated proxy / custom CA / mTLS (set transport = "curl").
        if transport == "curl":
            return _build_curl(profile, model, api_key, base_url, name="genai_farm",
                               default_auth="Authorization: Bearer")
        from agent.vendors.openai_router import OpenAIRouter

        return OpenAIRouter(model=model, base_url=base_url, api_key=api_key)

    if family == "azure":
        if transport == "sdk":
            raise ValueError(
                "azure family currently supports only transport='curl'"
            )
        return _build_curl(profile, model, api_key, base_url, name="azure",
                           default_auth="api-key")

    raise ValueError(f"unknown LLM family {family!r} in profile")


def _build_curl(
    profile: dict[str, Any],
    model: str | None,
    api_key: str | None,
    base_url: str | None,
    *,
    name: str,
    default_auth: str,
) -> LMRouter:
    from agent.vendors.curl_router import CurlChatRouter

    kwargs: dict[str, Any] = {
        "model": model,
        "deployment": profile.get("deployment"),
        "azure_endpoint": profile.get("azure_endpoint"),
        "api_version": profile.get("api_version"),
        "url": profile.get("url"),
        "base_url": base_url,
        "api_key": api_key,
        "auth_header": profile.get("auth_header", default_auth),
        "proxy": profile.get("proxy"),
        "proxy_user": resolve_proxy_user(profile),
        "curl_extra_args": profile.get("curl_extra_args"),
        "name": name,
    }
    if profile.get("timeout_s") is not None:
        kwargs["timeout_s"] = int(profile["timeout_s"])
    return CurlChatRouter(**kwargs)


def _detect_vendor() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    raise RuntimeError(
        "No API key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY, pass "
        "--vendor explicitly (anthropic|openai|ollama|azure), or configure a "
        ".musubi/llm.toml profile and pass --profile <family>.<name>."
    )
