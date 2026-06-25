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


# ── store size recording + stats (measurement) ───────────────────────────────

def test_put_records_sizes(tmp_path):
    import sqlite3

    from compression import store
    db = tmp_path / "audit.db"
    ref = store.put("x" * 1000, "text", compressed_chars=120, db_path=db)
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT original_chars, compressed_chars FROM compression_blobs"
        " WHERE ref_id = ?", (ref,),
    ).fetchone()
    conn.close()
    assert row == (1000, 120)


def test_stats_aggregates_totals_and_by_kind(tmp_path):
    from compression import store
    db = tmp_path / "audit.db"
    store.put("a" * 1000, "json", compressed_chars=400, db_path=db)
    store.put("b" * 500, "text", compressed_chars=200, db_path=db)
    s = store.stats(db_path=db)
    assert s["total_blobs"] == 2
    assert s["rows_without_metric"] == 0
    assert s["total_original_chars"] == 1500
    assert s["total_compressed_chars"] == 600
    kinds = {k["kind"]: k for k in s["by_kind"]}
    assert kinds["json"]["original_chars"] == 1000
    assert kinds["json"]["compressed_chars"] == 400


def test_stats_empty_db_is_zeroed(tmp_path):
    from compression import store
    s = store.stats(db_path=tmp_path / "audit.db")
    assert s["total_blobs"] == 0
    assert s["total_original_chars"] == 0
    assert s["by_kind"] == []


def test_stats_migrates_old_schema_and_counts_unmetered(tmp_path):
    """A DB whose table predates the size columns must migrate in place and
    report its legacy rows as rows_without_metric rather than crashing."""
    import sqlite3

    from compression import store
    db = tmp_path / "audit.db"
    # Old 4-column table + a legacy row with no recorded sizes.
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE compression_blobs ("
        " ref_id TEXT PRIMARY KEY, kind TEXT NOT NULL,"
        " original TEXT NOT NULL, created_at REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO compression_blobs VALUES (?, ?, ?, ?)",
        ("legacy0000000000", "text", "old", 0.0),
    )
    conn.commit()
    conn.close()
    # New write migrates the table and records sizes.
    store.put("z" * 1000, "json", compressed_chars=300, db_path=db)
    s = store.stats(db_path=db)
    assert s["total_blobs"] == 2
    assert s["rows_without_metric"] == 1  # the legacy row
    assert s["total_original_chars"] == 1000  # only the metered row counts
    assert s["total_compressed_chars"] == 300


# ── musubi_compress / musubi_compression_stats tools ─────────────────────────

def test_musubi_compress_tool_shrinks_and_returns_ref(monkeypatch, tmp_path):
    import server
    monkeypatch.setattr("storage.db.DEFAULT_DB_PATH", tmp_path / "audit.db")
    payload = json.dumps({"items": [{"id": i} for i in range(300)]}, indent=2)
    out = json.loads(server.musubi_compress(payload, "f.json"))
    assert out["status"] == "ok"
    assert out["kind"] == "json"
    assert out["ref_id"]
    assert out["ratio"] < 1.0
    assert out["compressed_chars"] < out["original_chars"]


def test_musubi_compress_tool_skips_short_input():
    import server
    out = json.loads(server.musubi_compress("tiny"))
    assert out["ref_id"] is None
    assert out["ratio"] == 1.0
    assert "note" in out


def test_musubi_compression_stats_tool_reflects_blobs(monkeypatch, tmp_path):
    import server
    monkeypatch.setattr("storage.db.DEFAULT_DB_PATH", tmp_path / "audit.db")
    payload = json.dumps({"items": [{"id": i} for i in range(300)]}, indent=2)
    server.musubi_compress(payload, "f.json")
    stats = json.loads(server.musubi_compression_stats())
    assert stats["status"] == "ok"
    assert stats["total_blobs"] == 1
    assert stats["bytes_saved"] > 0
    assert stats["overall_ratio"] < 1.0
    assert stats["savings_pct"] > 0


def test_musubi_compression_stats_tool_empty(monkeypatch, tmp_path):
    import server
    monkeypatch.setattr("storage.db.DEFAULT_DB_PATH", tmp_path / "audit.db")
    stats = json.loads(server.musubi_compression_stats())
    assert stats["total_blobs"] == 0
    assert stats["overall_ratio"] == 1.0
    assert stats["bytes_saved"] == 0
