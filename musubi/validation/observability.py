"""Phase G.3 — observability stats helpers.

musubi-tier: substrate
expires-when: never — Substrate-side observability writes.


Pure-Python aggregation over the `pipeline_runs` rows produced by the
runner. The harness never streams these; they're computed lazily when
`musubi_pipeline_stats` is called. Decisions:

  - Median + p90 in Python (Q3 default) — SQLite's MEDIAN support is
    inconsistent and our N stays in the hundreds at most.
  - Only TERMINAL rows (ended_at IS NOT NULL) feed aggregates so an
    in-flight session doesn't skew the medians.
  - "success_rate" counts final_status='success' / total_terminal.
  - "escalate_rate" counts escalated=1 / total_terminal.
  - "chunked_run_pct" counts chunked=1 / total_terminal.

Empty input returns an all-zeros stats object so callers don't have
to special-case "no data yet."
"""

from __future__ import annotations

import statistics
from typing import Any


def _percentile(values: list[float], pct: float) -> float:
    """Quick-and-dirty percentile via interpolation. Falls back to 0
    when the input list is empty."""
    if not values:
        return 0.0
    if pct <= 0:
        return float(min(values))
    if pct >= 100:
        return float(max(values))
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    d = k - f
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * d)


def aggregate_pipeline_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate a list of TERMINAL `pipeline_runs` rows into the
    summary shape the `musubi_pipeline_stats` MCP tool returns.

    Empty input ⇒ zero-valued summary so callers never crash on a
    "haven't run anything yet" UX path.
    """
    if not rows:
        return {
            "count": 0,
            "success_rate": 0.0,
            "escalate_rate": 0.0,
            "median_tokens": 0,
            "p90_tokens": 0,
            "median_wall_clock_ms": 0,
            "median_correction_attempts": 0,
            "chunked_run_pct": 0.0,
        }
    count = len(rows)
    successes = sum(1 for r in rows if r.get("final_status") == "success")
    escalations = sum(1 for r in rows if int(r.get("escalated") or 0) == 1)
    chunked = sum(1 for r in rows if int(r.get("chunked") or 0) == 1)

    tokens = [int(r.get("total_tokens_estimate") or 0) for r in rows]
    corrections = [int(r.get("correction_attempts") or 0) for r in rows]
    wall_clocks_s = [
        float((r.get("ended_at") or 0) - (r.get("started_at") or 0))
        for r in rows
        if r.get("ended_at") and r.get("started_at")
    ]
    wall_clock_ms = [int(s * 1000) for s in wall_clocks_s if s >= 0]

    return {
        "count": count,
        "success_rate":  successes  / count,
        "escalate_rate": escalations / count,
        "median_tokens": int(statistics.median(tokens)) if tokens else 0,
        "p90_tokens":    int(_percentile([float(t) for t in tokens], 90.0)) if tokens else 0,
        "median_wall_clock_ms":
            int(statistics.median(wall_clock_ms)) if wall_clock_ms else 0,
        "median_correction_attempts":
            int(statistics.median(corrections)) if corrections else 0,
        "chunked_run_pct": chunked / count,
    }
