"""Content-type routing + the public compress/retrieve entry points.

musubi-tier: substrate
expires-when: never — token cost is permanent.

`compress()` detects the payload kind, runs the matching deterministic
compressor, stores the verbatim original, and appends a retrieval marker
so the model knows it can call `musubi_retrieve(ref_id)` for the full
text. It is conservative: short inputs and any case where compression
fails to shrink the text are returned unchanged with no stored blob.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from compression import compressors, store

#: Inputs below this many characters are not worth compressing/storing.
DEFAULT_MIN_CHARS = 800

_CODE_HINTS = {"code", "python", "py", "js", "ts", "go", "rust", "rs",
               "java", "kotlin", "kt", "cpp", "cc", "c", "ruby", "rb", "php"}
_CODE_EXTS = (".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs",
              ".java", ".kt", ".cpp", ".cc", ".c", ".h", ".rb", ".php")
_PYTHON_HINTS = {"python", "py"}
_PYTHON_EXTS = (".py", ".pyi")

_COMPRESSORS = {
    "json": compressors.smart_crush_json,
    "code": compressors.strip_code,
    "log": compressors.group_log_patterns,
    "text": compressors.outline_text,
}


@dataclass(frozen=True)
class CompressResult:
    """Outcome of a compress() call.

    `ref_id` is None when nothing was stored (input skipped or no win);
    in that case `compressed` is the original text unchanged.
    """

    compressed: str
    ref_id: str | None
    kind: str
    original_chars: int
    compressed_chars: int

    @property
    def ratio(self) -> float:
        if self.original_chars == 0:
            return 1.0
        return self.compressed_chars / self.original_chars


def detect_kind(text: str, hint: str | None = None) -> str:
    """Return 'json' | 'code' | 'log' | 'text'.

    `hint` may be a path or a type label (e.g. a filename from a file
    read). Without a hint, only JSON is auto-detected from content; code
    is never guessed from content (too unreliable) and falls back to the
    safe text compressor.
    """
    if hint:
        h = hint.lower()
        if h == "json" or h.endswith(".json"):
            return "json"
        if h in _CODE_HINTS or h.endswith(_CODE_EXTS):
            return "code"
        if h == "log" or h.endswith(".log"):
            return "log"
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            json.loads(text)
            return "json"
        except (ValueError, TypeError):
            pass
    return "text"


def compress(
    text: str,
    *,
    hint: str | None = None,
    min_chars: int = DEFAULT_MIN_CHARS,
    db_path=None,
) -> CompressResult:
    """Compress `text` for model consumption; store the original.

    Returns a CompressResult. When `ref_id` is set, `compressed` ends with
    a marker pointing the model at `musubi_retrieve(ref_id)`.
    """
    n = len(text)
    if n < min_chars:
        return CompressResult(text, None, "skip", n, n)
    kind = detect_kind(text, hint)
    try:
        body = _select_compressor(kind, hint)(text)
    except Exception:
        # Compressors must not break the tool result — fail open.
        return CompressResult(text, None, "skip", n, n)
    if len(body) >= n:
        return CompressResult(text, None, kind, n, n)
    projected = body + _marker(kind, "0" * 16, n, len(body))
    if len(projected) >= n:
        return CompressResult(text, None, kind, n, n)
    ref_id = store.put(text, kind, compressed_chars=len(body), db_path=db_path)
    out = body + _marker(kind, ref_id, n, len(body))
    return CompressResult(out, ref_id, kind, n, len(out))


def retrieve(ref_id: str, *, db_path=None) -> str | None:
    """Return the verbatim original for `ref_id`, or None if unknown."""
    return store.get(ref_id, db_path=db_path)


def _select_compressor(kind: str, hint: str | None):
    if kind == "code" and _is_python_hint(hint):
        return compressors.compress_python_code
    return _COMPRESSORS[kind]


def _is_python_hint(hint: str | None) -> bool:
    if not hint:
        return False
    h = hint.lower()
    return h in _PYTHON_HINTS or h.endswith(_PYTHON_EXTS)


def _marker(kind: str, ref_id: str, original_chars: int, body_chars: int) -> str:
    return (
        f"\n\n[musubi:compressed kind={kind} ref={ref_id} "
        f"chars {original_chars}->{body_chars}; "
        f'call musubi_retrieve("{ref_id}") for the verbatim original]'
    )
