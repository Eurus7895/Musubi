"""Tests for the deterministic compression eval gate.

musubi-tier: substrate test - eval defaults must stay zero-LLM and CI-safe.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from compression.eval import run_compression_eval


def test_compression_eval_passes_bundled_cases(tmp_path: Path) -> None:
    report = run_compression_eval(db_path=tmp_path / "compression_eval.db")

    assert report["status"] == "ok"
    assert report["summary"]["payload_cases"] == 4
    assert report["summary"]["retrieve_ok"] == 4
    assert report["summary"]["retrieve_failures"] == 0
    assert report["summary"]["payload_savings_pct"] >= 85.0
    assert report["summary"]["context_savings_pct"] >= 80.0
    assert report["summary"]["retrieve_marker_count"] >= 4
    assert report["summary"]["quality_proxy_ok"] is True


def test_compression_eval_records_context_shape(tmp_path: Path) -> None:
    report = run_compression_eval(db_path=tmp_path / "compression_eval.db")
    context = report["context_packing"]

    assert context["compressed_tool_results"] >= 1
    assert context["trimmed_tool_results"] >= 1
    assert context["recent_turn_preserved"] is True
    assert context["packed_context_chars"] < context["original_context_chars"]


def test_compression_eval_payloads_are_json_serializable(tmp_path: Path) -> None:
    report = run_compression_eval(db_path=tmp_path / "compression_eval.db")

    encoded = json.dumps(report, sort_keys=True)

    assert "payloads" in encoded


def test_real_lm_eval_is_explicitly_skipped_by_default() -> None:
    pytest.importorskip("agent.compression_eval")
    from agent.compression_eval import real_lm_eval_enabled

    assert real_lm_eval_enabled(False) is False
