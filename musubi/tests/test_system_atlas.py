from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ATLAS_PATH = ROOT / "artifacts" / "musubi-system-atlas.html"


class AtlasParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.attrs: list[dict[str, str]] = []
        self.external_refs: list[str] = []
        self.noscript_depth = 0
        self.noscript_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        self.attrs.append(values)
        if values.get("id"):
            self.ids.add(values["id"])
        for key in ("src", "href"):
            value = values.get(key, "")
            if re.match(r"^(?:https?:)?//", value):
                self.external_refs.append(value)
        if tag == "noscript":
            self.noscript_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "noscript":
            self.noscript_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.noscript_depth:
            self.noscript_text.append(data)


def parsed_atlas() -> tuple[str, AtlasParser]:
    html = ATLAS_PATH.read_text(encoding="utf-8")
    parser = AtlasParser()
    parser.feed(html)
    return html, parser


class SystemAtlasContractTests(unittest.TestCase):
    def test_atlas_is_self_contained_and_has_required_landmarks(self) -> None:
        html, parser = parsed_atlas()
        self.assertEqual(parser.external_refs, [])
        self.assertIsNone(re.search(r"<(?:link|script)[^>]+(?:src|href)=", html, re.I))
        self.assertLessEqual(
            {
                "orientation",
                "system-map",
                "components",
                "traces",
                "invariants",
                "economics",
                "evolution",
                "quiz",
            },
            parser.ids,
        )
        self.assertIn("49c58d3", html)
        self.assertIn("2026-07-16", html)

    def test_atlas_records_have_complete_classification_and_quiz_contract(self) -> None:
        html, parser = parsed_atlas()
        components = [a for a in parser.attrs if "data-component" in a]
        questions = [a for a in parser.attrs if "data-question-id" in a]
        scenarios = [a for a in parser.attrs if "data-scenario" in a]
        self.assertGreaterEqual(len(components), 24)
        self.assertTrue(
            all(a.get("data-trust-zone") and a.get("data-durability") for a in components)
        )
        self.assertGreaterEqual(len(scenarios), 13)
        self.assertGreaterEqual(len(questions), 24)
        self.assertEqual(
            len({a["data-question-id"] for a in questions}),
            len(questions),
        )
        self.assertIn("noscript", html.lower())
        self.assertGreater(len(" ".join(parser.noscript_text)), 500)

    def test_every_embedded_source_path_exists(self) -> None:
        html, _ = parsed_atlas()
        paths = set(re.findall(r'data-source="([^"]+)"', html))
        self.assertGreaterEqual(len(paths), 18)
        missing = sorted(
            path for path in paths if not (ROOT / path.split(":", 1)[0]).exists()
        )
        self.assertEqual(missing, [])

    def test_component_cards_have_required_maintainer_fields(self) -> None:
        html, parser = parsed_atlas()
        required = {
            "data-responsibility",
            "data-why",
            "data-inputs",
            "data-outputs",
            "data-called-by",
            "data-depends-on",
            "data-enforces",
            "data-failure-modes",
            "data-economics",
            "data-source",
            "data-trust-zone",
            "data-durability",
        }
        components = [a for a in parser.attrs if "data-component" in a]
        self.assertTrue(all(required <= a.keys() for a in components))
        for badge in ("verified", "rationale", "historical", "open", "stale"):
            self.assertIn(f'data-evidence-kind="{badge}"', html)

    def test_current_routing_and_boundary_corrections_are_explicit(self) -> None:
        html, _ = parsed_atlas()
        self.assertIn(
            "model-visible root và child không thấy musubi_spawn_pipeline", html
        )
        self.assertIn("same-turn", html)
        self.assertIn("external MCP", html)
        self.assertIn("ngoài Musubi-owned policy/audit boundary", html)
        self.assertIn("durable driver", html)
        self.assertIn("zero-LLM substrate", html)

    def test_map_nodes_and_trace_scenarios_are_complete(self) -> None:
        html, parser = parsed_atlas()
        expected_map_nodes = {
            "cli", "console", "goal-state", "worker-loop", "pipeline-runner",
            "lm-router", "model-provider", "mcp-server", "policy",
            "evaluator-firewall", "skills", "memory", "compression", "state-db",
            "audit-db", "rust-projection", "react-view-model", "external-mcp",
        }
        mapped_nodes = {
            attrs["data-map-component"]
            for attrs in parser.attrs
            if "data-map-component" in attrs
        }
        self.assertEqual(mapped_nodes, expected_map_nodes)
        component_ids = {
            attrs["data-component"]
            for attrs in parser.attrs
            if "data-component" in attrs
        }
        self.assertLessEqual(mapped_nodes, component_ids)

        expected_scenarios = {
            "direct-worker", "pushed-skill", "parallel-workers", "nested-helper",
            "operator-pipeline", "pipeline-helpers", "policy-denial",
            "evaluator-denial", "budget-limit", "context-elision",
            "console-projection", "historical-console", "external-mcp",
        }
        scenario_ids = set(re.findall(r"\bid:\s*'([^']+)'", html))
        self.assertLessEqual(expected_scenarios, scenario_ids)
        trace_data = html.split("const TRACE_SCENARIOS = [", 1)[1].split(
            "const TRACE_STEP_FIELDS", 1
        )[0]
        step_fields = {
            "component", "title", "input", "output", "decision", "lmCall",
            "economics", "evidence", "failure",
        }
        step_records = re.findall(
            r"\{\s*component:\s*'[^']+'(.*?)\}\s*,?", trace_data, re.S
        )
        self.assertGreaterEqual(len(step_records), 13)
        for record in step_records:
            present = set(re.findall(r"\b(\w+):", "component:" + record))
            self.assertLessEqual(step_fields, present)

    def test_governance_economics_and_evolution_are_maintainer_complete(self) -> None:
        html, _ = parsed_atlas()
        for invariant in ("HI #1", "HI #2", "HI #3", "HI #5", "HI #7", "HI #8", "HI #9"):
            self.assertIn(invariant, html)
        for owner in (
            "Per-call output tokens",
            "Worker turns/cycles",
            "Model-input characters",
            "Parent ChildTokenBudget allocation",
            "Root routing/goal-state controller",
            "Each actual router call",
            "Append-only persistence boundaries",
        ):
            self.assertIn(owner, html)
        for residue in (
            "worker-summoned whole pipelines",
            "one-level leaf wording",
            "max_credits/warn_at compatibility fields",
            "extension-side runner comments",
            "legacy Pipeline Studio chat/session fields",
            "root prompt maxTurns",
        ):
            self.assertIn(residue, html)


if __name__ == "__main__":
    unittest.main()
