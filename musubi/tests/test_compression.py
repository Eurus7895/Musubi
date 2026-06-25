"""Tests for the deterministic, reversible compression module.

musubi-tier: substrate
expires-when: never — guards the compression substrate.
"""

from __future__ import annotations

import json

import pytest

from compression import compress, detect_kind, retrieve
from compression import compressors


# ── compressors (deterministic, lossy-but-recoverable) ───────────────────────

def test_minify_json_shrinks_and_round_trips():
    pretty = json.dumps({"a": 1, "items": [{"x": 1}, {"x": 2}]}, indent=4)
    minified = compressors.minify_json(pretty)
    assert len(minified) < len(pretty)
    # Lossless: same object after compression.
    assert json.loads(minified) == json.loads(pretty)


def test_strip_code_drops_comments_and_blank_runs():
    src = (
        "def f(x):\n"
        "    # a comment\n"
        "    return x\n"
        "\n"
        "\n"
        "\n"
        "    // c-style too\n"
    )
    out = compressors.strip_code(src)
    assert "# a comment" not in out
    assert "// c-style" not in out
    assert "def f(x):" in out
    assert "return x" in out
    assert "\n\n\n" not in out


def test_collapse_text_strips_trailing_ws_and_blank_runs():
    text = "line one   \n\n\n\n\nline two\t\n"
    out = compressors.collapse_text(text)
    assert "   \n" not in out
    assert "\n\n\n" not in out
    assert "line one" in out and "line two" in out


# ── routing ──────────────────────────────────────────────────────────────────

def test_detect_kind_json_from_content():
    assert detect_kind('{"a": 1}') == "json"


def test_detect_kind_code_from_path_hint():
    assert detect_kind("whatever", hint="src/foo.py") == "code"


def test_detect_kind_defaults_text():
    assert detect_kind("just some prose") == "text"


# ── compress() contract + reversibility ──────────────────────────────────────

def test_compress_skips_short_input():
    res = compress("tiny")
    assert res.ref_id is None
    assert res.compressed == "tiny"
    assert res.ratio == 1.0


def test_compress_json_shrinks_stores_and_retrieves(tmp_path):
    db = tmp_path / "audit.db"
    original = json.dumps({"k": list(range(400))}, indent=4)
    res = compress(original, hint="json", db_path=db)
    assert res.ref_id is not None
    assert res.kind == "json"
    assert res.compressed_chars < res.original_chars
    assert res.ratio < 1.0
    assert f'musubi_retrieve("{res.ref_id}")' in res.compressed
    # Reversible: the stored original comes back verbatim.
    assert retrieve(res.ref_id, db_path=db) == original


def test_ref_id_is_deterministic_and_dedups(tmp_path):
    db = tmp_path / "audit.db"
    original = json.dumps({"k": list(range(400))}, indent=4)
    a = compress(original, hint="json", db_path=db)
    b = compress(original, hint="json", db_path=db)
    assert a.ref_id == b.ref_id  # content hash → same id, single row


def test_retrieve_unknown_ref_returns_none(tmp_path):
    db = tmp_path / "audit.db"
    assert retrieve("deadbeefdeadbeef", db_path=db) is None


def test_compress_no_win_returns_original_unstored(tmp_path):
    db = tmp_path / "audit.db"
    # Incompressible-ish text above the min threshold (no comments, no
    # blank runs, no JSON): collapse_text can't shrink it.
    text = "x" * 1000
    res = compress(text, db_path=db)
    assert res.ref_id is None
    assert res.compressed == text


# ── server-side gated wiring (Step 3) ────────────────────────────────────────

def test_maybe_compress_field_opt_out_is_noop(monkeypatch):
    """Compression is on by default; MUSUBI_COMPRESS=0 opts out."""
    import server
    monkeypatch.setenv("MUSUBI_COMPRESS", "0")
    d = {"status": "ok", "content": "x" * 5000}
    assert server._maybe_compress_field(d, "content", "f.txt") == d


def test_maybe_compress_field_on_by_default(monkeypatch):
    """With MUSUBI_COMPRESS unset, a large field IS compressed."""
    import json as _json

    import server
    monkeypatch.delenv("MUSUBI_COMPRESS", raising=False)
    # Indented JSON well over the 800-char floor → minify is a real win.
    payload = _json.dumps({"items": [{"id": i} for i in range(200)]}, indent=2)
    out = server._maybe_compress_field({"status": "ok", "content": payload}, "content", "f.json")
    assert out.get("compressed_ref")  # compression engaged with no env set


def test_maybe_compress_field_on_compresses_without_mutating(monkeypatch):
    import server
    from compression.router import CompressResult
    monkeypatch.setenv("MUSUBI_COMPRESS", "1")
    monkeypatch.setattr(
        "compression.compress",
        lambda text, **kw: CompressResult("SHORT", "ref123", "json", len(text), 5),
    )
    src = {"status": "ok", "content": "x" * 5000}
    out = server._maybe_compress_field(src, "content", "f.json")
    assert out["content"] == "SHORT"
    assert out["compressed_ref"] == "ref123"
    assert out["compression_ratio"] < 1.0
    assert src["content"] == "x" * 5000  # input not mutated


def test_maybe_compress_field_skips_error_results(monkeypatch):
    import server
    monkeypatch.setenv("MUSUBI_COMPRESS", "1")
    d = {"status": "error", "error": "nope"}
    assert server._maybe_compress_field(d, "content", None) == d
