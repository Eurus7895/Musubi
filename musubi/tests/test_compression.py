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


def test_smart_crush_json_summarizes_repeated_arrays_better_than_minify():
    original = json.dumps(
        {
            "items": [
                {
                    "id": i,
                    "name": f"item-{i}",
                    "active": i % 2 == 0,
                    "meta": {"score": i % 5, "tags": ["alpha", "beta"]},
                }
                for i in range(150)
            ]
        },
        indent=2,
    )

    out = compressors.smart_crush_json(original)

    assert len(out) < len(compressors.minify_json(original))
    assert "json smart crush" in out
    assert "$.items[]" in out
    assert "count=150" in out
    assert "sample[0]" in out


def test_python_code_compressor_summarizes_signatures_without_bodies():
    body = "\n".join(f"        total += {i}" for i in range(120))
    src = (
        "import os\n"
        "from pathlib import Path\n\n"
        "class Worker(Base):\n"
        "    \"\"\"Commentary that should not survive.\"\"\"\n"
        "    def run(self, item: str) -> bool:\n"
        "        # expensive loop should be omitted\n"
        "        total = 0\n"
        f"{body}\n"
        "        return bool(total and item)\n"
    )

    out = compressors.compress_python_code(src)

    assert len(out) < len(src)
    assert "python structure" in out
    assert "import os" in out
    assert "from pathlib import Path" in out
    assert "class Worker(Base)" in out
    assert "def run(self, item: str) -> bool" in out
    assert "expensive loop" not in out
    assert "total += 119" not in out


def test_python_code_compressor_keeps_falsy_default_values():
    src = "def configure(retries: int = 0, enabled: bool = False) -> None:\n    pass\n"

    out = compressors.compress_python_code(src)

    assert "def configure(retries: int=0, enabled: bool=False) -> None" in out


def test_invalid_python_code_uses_conservative_fallback():
    src = "def broken(:\n    # noisy comment\n    value = 1\n\n\n// c-style\n"

    out = compressors.compress_python_code(src)

    assert "value = 1" in out
    assert "noisy comment" not in out
    assert "c-style" not in out
    assert "\n\n\n" not in out


def test_log_pattern_grouping_keeps_first_last_examples():
    lines = [
        f"2026-06-27T10:{i:02d}:00Z INFO request id={1000 + i} path=/api/items/{i} status=200"
        for i in range(40)
    ]
    out = compressors.group_log_patterns("\n".join(lines))

    assert len(out) < len("\n".join(lines))
    assert "log pattern groups" in out
    assert "x40" in out
    assert "first=" in out
    assert "last=" in out
    assert "/api/items/<num>" in out


def test_text_outline_preserves_headings_and_bounded_snippets():
    repeated = "This paragraph contains detailed background. " * 40
    text = (
        "# Overview\n\n"
        f"{repeated}\n\n"
        "## Details\n\n"
        f"{repeated}\n\n"
        "## Result\n\n"
        f"{repeated}\n"
    )

    out = compressors.outline_text(text)

    assert len(out) < len(text)
    assert "text outline" in out
    assert "# Overview" in out
    assert "## Details" in out
    assert "## Result" in out
    assert "paragraphs=" in out
    assert repeated not in out


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


def test_compress_skips_when_marker_overhead_erases_savings(monkeypatch, tmp_path):
    import compression.router as router

    db = tmp_path / "audit.db"
    text = "x" * 820
    monkeypatch.setitem(router._COMPRESSORS, "text", lambda _text: "y" * 790)

    res = compress(text, min_chars=0, db_path=db)

    assert res.ref_id is None
    assert res.compressed == text


def test_compress_python_uses_structural_summary_and_retrieves(tmp_path):
    db = tmp_path / "audit.db"
    body = "\n".join(f"    return_value += {i}" for i in range(250))
    original = (
        "from typing import Iterable\n\n"
        "def summarize(values: Iterable[int]) -> int:\n"
        "    return_value = 0\n"
        f"{body}\n"
        "    return return_value\n"
    )

    res = compress(original, hint="worker.py", db_path=db)

    assert res.ref_id is not None
    assert res.kind == "code"
    assert "python structure" in res.compressed
    assert "def summarize(values: Iterable[int]) -> int" in res.compressed
    assert retrieve(res.ref_id, db_path=db) == original


# ── server-side gated wiring (Step 3) ────────────────────────────────────────

def test_maybe_compress_field_ignores_legacy_disable_env(monkeypatch):
    """Tool-boundary compression cannot be disabled by an environment flag."""
    import server
    from compression.router import CompressResult

    monkeypatch.setenv("MUSUBI_COMPRESS", "0")
    monkeypatch.setattr(
        "compression.compress",
        lambda text, **kw: CompressResult("SHORT", "ref123", "text", len(text), 5),
    )
    out = server._maybe_compress_field(
        {"status": "ok", "content": "x" * 5000}, "content", "f.txt",
    )
    assert out["content"] == "SHORT"
    assert out["compressed_ref"] == "ref123"


def test_maybe_compress_field_compresses_large_payload():
    """A large compressible field is compressed automatically."""
    import json as _json

    import server
    # Indented JSON well over the 800-char floor → minify is a real win.
    payload = _json.dumps({"items": [{"id": i} for i in range(200)]}, indent=2)
    out = server._maybe_compress_field({"status": "ok", "content": payload}, "content", "f.json")
    assert out.get("compressed_ref")  # compression engaged with no env set


def test_maybe_compress_field_on_compresses_without_mutating(monkeypatch):
    import server
    from compression.router import CompressResult
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
    d = {"status": "error", "error": "nope"}
    assert server._maybe_compress_field(d, "content", None) == d


def test_musubi_read_stage_compresses_permitted_data(monkeypatch, tmp_path):
    import server
    from compression import retrieve

    monkeypatch.setattr("storage.db.DEFAULT_DB_PATH", tmp_path / "audit.db")
    monkeypatch.setattr(server._db, "get_pipeline_run", lambda sid: None)
    monkeypatch.setattr(
        server.composer, "active_stages", lambda pipeline: ["design"]
    )
    monkeypatch.setattr(
        server.composer, "output_stage_for_agent", lambda pipeline, agent: None
    )
    monkeypatch.setattr(server.composer, "injected_skill_ids", lambda *a: [])
    monkeypatch.setattr(server.memory_loader, "get_memory_context", lambda: {})

    payload = {"items": [{"id": i, "name": f"item-{i}"} for i in range(300)]}
    monkeypatch.setattr(
        server.context_builder,
        "read_stage_for_agent",
        lambda *a, **k: payload,
    )

    out = json.loads(server.musubi_read_stage("sess", "design", "coder"))
    assert out["compressed_ref"]
    assert out["compression_ratio"] < 1.0
    assert isinstance(out["data"], str)
    assert "musubi_retrieve" in out["data"]
    stored = retrieve(out["compressed_ref"], db_path=tmp_path / "audit.db")
    assert stored == json.dumps(
        payload, ensure_ascii=False, indent=2, sort_keys=True,
    )


def test_musubi_get_conversation_compresses_large_message(monkeypatch, tmp_path):
    import server
    from compression import retrieve
    from session import conversations
    from storage import db as _db

    db = tmp_path / "audit.db"
    _db.init_db(db)
    monkeypatch.setattr(_db, "DEFAULT_DB_PATH", db)
    payload = json.dumps({"items": [{"id": i} for i in range(300)]}, indent=2)
    conversations.append_message("chat-X", "tool", payload, db_path=db)

    out = json.loads(server.musubi_get_conversation("chat-X"))
    msg = out["messages"][0]
    assert msg["compressed_ref"]
    assert msg["compression_ratio"] < 1.0
    assert "musubi_retrieve" in msg["content"]
    assert retrieve(msg["compressed_ref"], db_path=db) == payload


def test_musubi_get_conversation_ignores_legacy_disable_env(
    monkeypatch, tmp_path,
):
    import server
    from compression import retrieve
    from session import conversations
    from storage import db as _db

    db = tmp_path / "audit.db"
    _db.init_db(db)
    monkeypatch.setenv("MUSUBI_COMPRESS", "0")
    monkeypatch.setattr(_db, "DEFAULT_DB_PATH", db)
    payload = json.dumps({"items": [{"id": i} for i in range(300)]}, indent=2)
    conversations.append_message("chat-X", "tool", payload, db_path=db)

    out = json.loads(server.musubi_get_conversation("chat-X"))
    msg = out["messages"][0]
    assert msg["content"] != payload
    assert msg["compressed_ref"]
    assert retrieve(msg["compressed_ref"], db_path=db) == payload


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
