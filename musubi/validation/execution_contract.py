"""Shared canonical contract primitives for Root and Pipeline execution.

musubi-tier: substrate
expires-when: never - immutable contracts and reproducible hashes are audit data
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def canonical_json(value: Any) -> str:
    """Return the one UTF-8 JSON representation used for contract hashes."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def contract_hash(value: Any) -> str:
    """Hash a contract payload after excluding its self-referential hash."""
    if isinstance(value, Mapping):
        value = {key: item for key, item in value.items() if key != "contract_hash"}
    encoded = canonical_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def require_closed_object(
    raw: Mapping[str, Any],
    *,
    allowed: set[str] | frozenset[str],
    name: str,
) -> None:
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise ValueError(f"{name} has unknown field(s): {', '.join(unknown)}")


def require_identifier(value: Any, *, field: str, prefix: str) -> str:
    text = str(value or "").strip()
    if not text.startswith(prefix) or len(text) <= len(prefix):
        raise ValueError(f"{field} must start with {prefix!r}")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    if any(char not in allowed for char in text):
        raise ValueError(f"{field} contains unsupported characters")
    return text


def require_string_list(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    normalized = tuple(str(item).strip() for item in value)
    if any(not item for item in normalized):
        raise ValueError(f"{field} entries must be non-empty strings")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field} entries must be unique")
    return normalized


def require_positive_int(value: Any, *, field: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def as_json_list(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Copy mapping sequences into mutable JSON-shaped records."""
    return [dict(value) for value in values]
