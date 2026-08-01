from __future__ import annotations

from pathlib import Path

from validation.stage_gate import evaluate_stage_gate, fingerprint_file
from workspace.grants import RootRegistry


def test_static_weather_table_checks_and_manifest(tmp_path: Path) -> None:
    path = tmp_path / "index.html"
    before = {"musubi:index.html": None}
    path.write_text(
        "<table><tbody>" + "".join(
            f"<tr data-testid='weather-row'><td data-testid='city-name'>{city}</td></tr>"
            for city in ["Hanoi", "Hue", "Danang", "Saigon", "Can Tho"]
        ) + "</tbody></table>", encoding="utf-8",
    )
    contract = {
        "exit_when": [
            {"type": "file_created_or_modified", "root": "musubi", "path": "index.html"},
            {"type": "dom_count", "root": "musubi", "path": "index.html", "selector": "[data-testid='weather-row']", "equals": 5},
            {"type": "dom_distinct_text", "root": "musubi", "path": "index.html", "selector": "[data-testid='city-name']", "equals": 5},
            {"type": "dom_text_set", "root": "musubi", "path": "index.html", "selector": "[data-testid='city-name']", "equals": ["Hanoi", "Hue", "Danang", "Saigon", "Can Tho"]},
        ]
    }
    result = evaluate_stage_gate(
        contract, before, [{"root": "musubi", "path": "index.html"}],
        roots=RootRegistry.build(tmp_path),
    )
    assert result.status == "pass"
    assert all(check.status == "pass" for check in result.checks)
    assert fingerprint_file(path)["size"] > 0


def test_gate_returns_all_failures_and_distinguishes_infrastructure_error(tmp_path: Path) -> None:
    roots = RootRegistry.build(tmp_path)
    failed = evaluate_stage_gate({"exit_when": [
        {"type": "file_exists", "root": "musubi", "path": "missing-a"},
        {"type": "file_exists", "root": "musubi", "path": "missing-b"},
    ]}, {}, [], roots=roots)
    assert failed.status == "fail"
    assert len(failed.checks) == 2

    errored = evaluate_stage_gate({"exit_when": [
        {"type": "dom_count", "root": "musubi", "path": "missing.html", "selector": ".x", "equals": 1},
    ]}, {}, [], roots=roots)
    assert errored.status == "gate_error"
