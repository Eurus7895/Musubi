"""Defensive JSON decoding for tool results.

musubi-tier: substrate
expires-when: never - a tool result is untrusted text until it parses, and
  every caller in the host needs the same fail-soft reading of it.
"""

from __future__ import annotations

import json
from typing import Any


def loads_dict(raw: str) -> dict[str, Any]:
    """Decode `raw` as a JSON object, or `{}` when it is not one.

    A tool result is text until proven otherwise: a server may answer with
    an error string, a truncated payload, or a JSON array. Every one of those
    reads as "no fields", so callers can use `.get(...)` without guarding each
    call site. Deliberately silent — the CALLER decides whether a missing
    field is fatal, because the same empty dict is routine for an optional
    lookup and fail-closed for a spawn.
    """
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}
