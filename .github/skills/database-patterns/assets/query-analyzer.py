#!/usr/bin/env python3
"""Analyze a SQL query for correctness, parameterization, and index usage hints."""

import json
import re
import sys


INJECTION_PATTERNS = [
    r"%[sf]",                   # %-formatting in SQL
    r"\bformat\b.*\bSELECT\b",  # .format() near SQL
    r"f['\"].*SELECT",          # f-string SQL
]

INDEX_HINT_COLUMNS = {
    "session_id", "stage", "agent", "issue_type", "status", "created_at"
}


def check_parameterization(query: str) -> list[str]:
    issues = []
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            issues.append(f"possible string formatting in SQL (pattern: {pattern!r})")
    if "?" not in query and any(
        kw in query.upper() for kw in ("WHERE", "VALUES", "SET")
    ):
        issues.append("query has WHERE/VALUES/SET but no '?' placeholders — verify parameterization")
    return issues


def check_indexes(query: str) -> list[str]:
    hints = []
    upper = query.upper()
    if "WHERE" in upper:
        where_clause = upper.split("WHERE", 1)[1].split("ORDER")[0].split("GROUP")[0]
        for col in INDEX_HINT_COLUMNS:
            if col.upper() in where_clause:
                hints.append(f"ensure index exists on column: {col}")
    if "ORDER BY" in upper:
        hints.append("ORDER BY present — verify the sort column is indexed")
    if "GROUP BY" in upper:
        hints.append("GROUP BY present — verify grouped column is indexed and query uses COUNT/aggregate correctly")
    return hints


def check_joins(query: str) -> list[str]:
    join_count = query.upper().count(" JOIN ")
    warnings = []
    if join_count > 2:
        warnings.append(f"{join_count} JOINs detected — verify query does not cause N+1 or cartesian product")
    return warnings


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
        query = payload.get("query", "").strip()
    except (json.JSONDecodeError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)

    if not query:
        print(json.dumps({"ok": False, "error": "no query provided"}))
        sys.exit(1)

    result = {
        "ok": True,
        "parameterization_issues": check_parameterization(query),
        "index_hints": check_indexes(query),
        "join_warnings": check_joins(query),
    }
    result["summary"] = (
        "no issues found"
        if not any([result["parameterization_issues"], result["index_hints"], result["join_warnings"]])
        else f"{len(result['parameterization_issues'])} parameterization issue(s), "
             f"{len(result['index_hints'])} index hint(s), "
             f"{len(result['join_warnings'])} join warning(s)"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
