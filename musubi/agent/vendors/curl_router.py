"""Curl transport — OpenAI/Azure chat-completions over a `curl` subprocess.

musubi-tier: substrate
expires-when: never — the LM-call boundary for on-prem endpoints that must be
  reached through `curl` (corporate proxy, custom CA bundle, mTLS already
  configured for the system curl). No LLM SDK is imported here, so Hard
  Invariant #1 holds: this is driver-side glue, not substrate.

Wire format is identical to `openai_router` (reuses `openai_wire`); only the
transport differs. The api-key and the request body are passed to curl via
`--config -` on stdin so the secret never appears in the process argument list.

URL forms:
    Azure:   {azure_endpoint}/openai/deployments/{deployment}/chat/completions?api-version={v}
    generic: {base_url}/chat/completions   (any OpenAI-compatible host)
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from agent.vendors.base import LMResponse, LMRouter
from agent.vendors.openai_wire import (
    openai_message_to_blocks,
    to_openai_messages,
    token_budget_field,
    tool_to_openai,
    usage_to_dict,
)

_DEFAULT_TIMEOUT_S = 120


class CurlChatRouter(LMRouter):
    """OpenAI-wire chat completions issued through the system `curl` binary."""

    def __init__(
        self,
        model: str | None = None,
        *,
        url: str | None = None,
        azure_endpoint: str | None = None,
        api_version: str | None = None,
        deployment: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        auth_header: str = "api-key",
        proxy: str | None = None,
        proxy_user: str | None = None,
        curl_extra_args: list[str] | None = None,
        timeout_s: int = _DEFAULT_TIMEOUT_S,
        name: str = "azure",
    ) -> None:
        # For Azure the deployment name is the model id that lives in the URL
        # path; for a generic host the `model` field carries it.
        self.model = deployment or model or ""
        self.name = name
        self._api_key = api_key
        self._auth_header = auth_header
        self._proxy = proxy
        self._proxy_user = proxy_user
        self._extra = list(curl_extra_args or [])
        self._timeout_s = timeout_s
        self._url = _resolve_url(
            url=url,
            azure_endpoint=azure_endpoint,
            api_version=api_version,
            deployment=self.model,
            base_url=base_url,
        )

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
    ) -> LMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            token_budget_field(self.model): max_tokens,
            "messages": to_openai_messages(messages),
        }
        oa_tools = [tool_to_openai(t) for t in tools]
        if oa_tools:
            body["tools"] = oa_tools

        raw = self._post(json.dumps(body))
        data = _parse_json(raw, self._url)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(
                f"no choices in response from {self._url}: {raw[:500]}"
            )
        choice = choices[0]
        blocks = openai_message_to_blocks(choice.get("message") or {})
        stop = "tool_use" if choice.get("finish_reason") == "tool_calls" else "end_turn"
        return LMResponse(
            stop_reason=stop,
            content=blocks,
            usage=usage_to_dict(data.get("usage")),
        )

    # ── curl invocation ─────────────────────────────────────────────────────

    def _post(self, body_json: str) -> str:
        """POST `body_json` via curl; return the response body as text.

        The url, headers (incl. the api-key) and a pointer to the body file
        are written to a curl config consumed on stdin (`--config -`) so the
        secret is never visible in argv. Non-secret tuning (proxy, --cacert,
        mTLS) rides `curl_extra_args` on the command line.
        """
        fd, body_path = tempfile.mkstemp(suffix=".json", prefix="musubi-llm-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body_json)
            # Forward slashes: curl config double-quoted values treat "\" as an
            # escape, so a Windows temp path with backslashes must be posix-ified.
            posix_body = Path(body_path).as_posix()
            config = "\n".join(self._config_lines(posix_body)) + "\n"

            cmd = ["curl", "-sS", "--config", "-", *self._extra]
            try:
                proc = subprocess.run(
                    cmd,
                    input=config,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_s,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "curl binary not found on PATH; the curl transport needs it."
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"curl timed out after {self._timeout_s}s calling {self._url}"
                ) from exc

            if proc.returncode != 0:
                detail = proc.stderr.strip() or proc.stdout.strip()
                raise RuntimeError(
                    f"curl exited {proc.returncode} calling {self._url}: {detail}"
                )
            return proc.stdout
        finally:
            try:
                os.unlink(body_path)
            except OSError:
                pass

    def _config_lines(self, posix_body_path: str) -> list[str]:
        lines = [
            f'url = "{self._url}"',
            'request = "POST"',
            'header = "Content-Type: application/json"',
        ]
        if self._api_key:
            lines.append(f'header = "{_auth_header_line(self._auth_header, self._api_key)}"')
        # Proxy + proxy auth ride the stdin config (not argv) so the proxy
        # password is never visible in the process argument list — same reason
        # the api-key is here rather than on the command line.
        if self._proxy:
            lines.append(f'proxy = "{self._proxy}"')
        if self._proxy_user:
            lines.append(f'proxy-user = "{self._proxy_user}"')
        lines.append(f'data-binary = "@{posix_body_path}"')
        return lines


# ── helpers (module-level for unit testing) ─────────────────────────────────


def _resolve_url(
    *,
    url: str | None,
    azure_endpoint: str | None,
    api_version: str | None,
    deployment: str | None,
    base_url: str | None,
) -> str:
    """Build the chat-completions URL from the configured endpoint form."""
    if url:
        return url
    if azure_endpoint and deployment:
        endpoint = azure_endpoint.rstrip("/")
        suffix = f"?api-version={api_version}" if api_version else ""
        return f"{endpoint}/openai/deployments/{deployment}/chat/completions{suffix}"
    if base_url:
        return base_url.rstrip("/") + "/chat/completions"
    raise ValueError(
        "curl transport needs one of: explicit `url`, "
        "`azure_endpoint`+`deployment`, or `base_url`."
    )


def _auth_header_line(auth_header: str, api_key: str) -> str:
    """Render the auth header line.

    "api-key"              → "api-key: <key>"            (Azure default)
    "Authorization: Bearer" → "Authorization: Bearer <key>" (standard OpenAI)
    """
    if ":" in auth_header:
        name, prefix = auth_header.split(":", 1)
        value = f"{prefix.strip()} {api_key}".strip()
        return f"{name.strip()}: {value}"
    return f"{auth_header}: {api_key}"


def _parse_json(raw: str, url: str) -> dict[str, Any]:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"non-JSON response from {url}: {raw[:500]}"
        ) from exc
    if not isinstance(obj, dict):
        raise RuntimeError(f"unexpected response shape from {url}: {raw[:500]}")
    return obj
