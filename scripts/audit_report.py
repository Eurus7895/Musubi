#!/usr/bin/env python3
"""Render one agent run as a report an outside reviewer can read.

musubi-tier: substrate
expires-when: never — "prove what the agent did, what it was allowed to do,
  and what was refused" is a question a stronger model makes MORE pressing,
  not less. The orchestration that produced the run is ephemeral; the record
  of it is not.

Reads the two databases the harness already writes and answers the questions
someone who was not present will ask, in their order:

    1. What was asked, when, and in which conversation?
    2. What was the system allowed to touch?
    3. What did it decide to do, and who did it delegate to?
    4. What did it actually do — including what it was refused?
    5. What did that cost?
    6. What reached disk?
    7. Is this record complete, or is something missing?

Question 7 is the one that separates a log from an audit trail, and it is
answerable here because the harness records its own delivery obligations:
an undelivered row means evidence was owed and did not arrive, which is a
finding rather than a silence.

Reads only. No model call, no network, no writes.

    python scripts/audit_report.py                     # newest run
    python scripts/audit_report.py --chat-id CHAT      # one conversation
    python scripts/audit_report.py --session SESSION   # one run
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_STATE_DB = _REPO / "musubi" / "storage" / "musubi.db"
_AUDIT_DB = _REPO / "musubi" / "storage" / "audit.db"

#: Tool calls that change the workspace. Listed here rather than imported so
#: the report stays readable without the agent package on the path.
_MUTATING = {"musubi_write_file", "musubi_append_file", "musubi_edit_file"}


def _rows(db: Path, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    """Query one database, tolerating a table this version does not have."""
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _fmt(n: Any) -> str:
    return f"{n:,}" if isinstance(n, int) else str(n if n is not None else "—")


def _head(title: str) -> None:
    print(f"\n{title}\n{'─' * len(title)}")


def _resolve_turn(chat_id: str | None, session: str | None) -> dict[str, Any] | None:
    if session:
        rows = _rows(
            _STATE_DB,
            "SELECT * FROM agent_turns WHERE parent_session_id = ? "
            "ORDER BY started_at DESC LIMIT 1",
            (session,),
        )
    elif chat_id:
        rows = _rows(
            _STATE_DB,
            "SELECT * FROM agent_turns WHERE chat_id = ? "
            "ORDER BY started_at DESC LIMIT 1",
            (chat_id,),
        )
    else:
        rows = _rows(
            _STATE_DB, "SELECT * FROM agent_turns ORDER BY started_at DESC LIMIT 1",
        )
    return rows[0] if rows else None


def report(chat_id: str | None = None, session: str | None = None) -> int:
    turn = _resolve_turn(chat_id, session)
    if turn is None:
        print("No agent turn found. Has a run been recorded in musubi.db?")
        return 1
    sid = turn["parent_session_id"]
    chat = turn["chat_id"]

    # ── 1. What was asked ──────────────────────────────────────────────────
    _head("1. REQUEST")
    ses = _rows(_STATE_DB, "SELECT * FROM sessions WHERE session_id = ?", (sid,))
    print(f"  conversation   {chat}")
    print(f"  run            {sid}")
    print(f"  started        {turn['started_at']}   ended {turn['ended_at']}")
    print(f"  model family   {turn['model_family']}")
    if ses:
        print(f"  request        {ses[0]['request']!r}")
    print(f"  self-declared shape  {turn['root_triage'] or '— not declared —'}")

    # Prior turns in the same conversation: a reviewer asking "was this the
    # first attempt" must not have to infer it.
    prior = _rows(
        _STATE_DB,
        "SELECT started_at, cycles, tokens_in_estimate, tokens_out_estimate,"
        " delivered_artifact FROM agent_turns WHERE chat_id = ?"
        " ORDER BY started_at",
        (chat,),
    )
    if len(prior) > 1:
        print(f"  turn           {len(prior)} of this conversation")
        spent = sum(
            (p["tokens_in_estimate"] or 0) + (p["tokens_out_estimate"] or 0)
            for p in prior
        )
        delivered = sum(1 for p in prior if p["delivered_artifact"])
        print(f"  conversation   {_fmt(spent)} tokens, "
              f"{delivered}/{len(prior)} turns delivered a file")

    # ── 2. What it was allowed to touch ────────────────────────────────────
    _head("2. AUTHORISED SCOPE")
    grants = _rows(
        _STATE_DB,
        "SELECT alias, canonical_path FROM session_folder_grants WHERE chat_id = ?"
        " ORDER BY ordinal",
        (chat,),
    )
    if grants:
        for g in grants:
            print(f"  {g['alias']:12} {g['canonical_path']}")
    else:
        print("  workspace root only (no additional folder granted)")

    # ── 3. What it decided, and who it delegated to ────────────────────────
    _head("3. DELEGATION")
    workers = _rows(
        _STATE_DB,
        "SELECT * FROM sub_sessions WHERE parent_session_id = ? ORDER BY created_at",
        (sid,),
    )
    if not workers:
        print("  no worker was spawned")
    for w in workers:
        flag = "ESCALATED" if w["escalated"] else (w["status"] or "?").upper()
        print(f"  {w['handle_id']}  {w['role']:12} {flag:10} "
              f"turns={w['turns']}/{w['max_turns']}  skill={w['pushed_skill_id'] or '—'}")
        print(f"      brief   {(w['brief'] or '')[:96]!r}")
        if w["result_summary"]:
            print(f"      result  {(w['result_summary'] or '')[:96]!r}")
        if w["turn_cap_acceptance"]:
            print(f"      cap     accepted via {w['turn_cap_acceptance']}")

    # ── 4. What it did, and what it was refused ────────────────────────────
    _head("4. ACTIONS TAKEN")
    calls = _rows(
        _AUDIT_DB,
        "SELECT agent, tool, args_json, status, COUNT(*) AS n FROM tool_audit"
        " WHERE session_id = ? GROUP BY agent, tool, status ORDER BY agent, tool",
        (sid,),
    )
    if not calls:
        print("  no tool call recorded")
    for c in calls:
        mark = "  " if c["status"] == "ok" else " !"
        print(f" {mark} {c['agent']:10} {c['tool']:28} {c['status']:8} ×{c['n']}")

    denials = _rows(
        _AUDIT_DB,
        "SELECT role, tool, reason, COUNT(*) AS n FROM policy_audit"
        " WHERE verdict = 'DENY' AND parent_session_id = ?"
        " GROUP BY role, tool, reason",
        (sid,),
    )
    print()
    if denials:
        print("  REFUSED BY POLICY")
        for d in denials:
            print(f"   ! {d['role']:10} {d['tool']:28} ×{d['n']}")
            print(f"       reason: {d['reason']}")
    else:
        print("  REFUSED BY POLICY: none")

    # ── 5. Cost ────────────────────────────────────────────────────────────
    _head("5. COST")
    cyc = _rows(
        _STATE_DB,
        "SELECT worker_id, cycle_status, COUNT(*) AS n,"
        " SUM(tokens_in) AS tin, SUM(cached_input_tokens) AS cached,"
        " SUM(tokens_out) AS tout, SUM(lm_ms) AS ms"
        " FROM agent_cycles WHERE session_id = ?"
        " GROUP BY worker_id, cycle_status ORDER BY worker_id",
        (sid,),
    )
    total_in = total_out = total_cached = 0
    for c in cyc:
        total_in += c["tin"] or 0
        total_out += c["tout"] or 0
        total_cached += c["cached"] or 0
        print(f"  {c['worker_id'][:14]:16} {c['cycle_status']:12} ×{c['n']:<3} "
              f"in={_fmt(c['tin'])} out={_fmt(c['tout'])} "
              f"cached_in={_fmt(c['cached'])} {_fmt(c['ms'])}ms")
    print(f"\n  charged to the budget   {_fmt(total_in + total_out)} tokens"
          f"  (input is charged in full; {_fmt(total_cached)} of it was cached)")

    # ── 6. What reached disk ───────────────────────────────────────────────
    _head("6. WORKSPACE EFFECT")
    writes = _rows(
        _AUDIT_DB,
        "SELECT agent, tool, args_json, status FROM tool_audit"
        " WHERE session_id = ? ORDER BY id",
        (sid,),
    )
    touched: dict[str, list[str]] = {}
    for w in writes:
        if w["tool"] not in _MUTATING:
            continue
        try:
            path = (json.loads(w["args_json"] or "{}") or {}).get("path")
        except (TypeError, ValueError):
            path = None
        if path:
            touched.setdefault(str(path), []).append(
                f"{w['tool'].removeprefix('musubi_')}({w['status']})"
            )
    if touched:
        for path, ops in touched.items():
            print(f"  {path}   {', '.join(ops)}")
    else:
        print("  no file was created or modified")

    # ── 7. Is the record complete? ─────────────────────────────────────────
    _head("7. RECORD INTEGRITY")
    findings: list[str] = []

    owed = _rows(
        _STATE_DB,
        "SELECT kind, status, COUNT(*) AS n, MAX(error) AS err"
        " FROM audit_obligations WHERE status != 'delivered' GROUP BY kind, status",
    )
    for o in owed:
        findings.append(
            f"{o['n']} {o['kind']} audit obligation(s) {o['status']}"
            + (f" — {o['err']}" if o["err"] else "")
        )

    # Every spawn owes a terminal row (HI #8). A worker still 'running' after
    # the turn ended is an unclosed record, not merely an unfinished job.
    open_workers = [w for w in workers if w["status"] == "running"]
    if open_workers:
        findings.append(
            f"{len(open_workers)} worker(s) have no terminal completion row"
        )

    # A spawned worker with no cycles never ran; a cycle with no owning worker
    # cannot be attributed. Both are gaps a reviewer must be told about.
    cycle_workers = {
        r["worker_id"] for r in _rows(
            _STATE_DB,
            "SELECT DISTINCT worker_id FROM agent_cycles WHERE session_id = ?",
            (sid,),
        )
    }
    for w in workers:
        if w["handle_id"] not in cycle_workers:
            findings.append(
                f"worker {w['handle_id']} ({w['role']}) has no recorded cycles"
            )

    if findings:
        for f in findings:
            print(f"  ! {f}")
    else:
        print("  every spawn has a terminal row, every cycle is attributed,")
        print("  and no audit obligation is outstanding.")

    print()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chat-id", help="report the newest run of one conversation")
    ap.add_argument("--session", help="report one run by its session id")
    args = ap.parse_args()
    return report(chat_id=args.chat_id, session=args.session)


if __name__ == "__main__":
    raise SystemExit(main())
