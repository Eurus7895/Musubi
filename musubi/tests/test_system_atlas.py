from __future__ import annotations

import json
import re
import unittest
from collections import Counter
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
        values["_tag"] = tag
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
    def test_atlas_uses_an_explicit_high_contrast_light_palette(self) -> None:
        html, _ = parsed_atlas()
        root_css = re.search(r":root\s*\{([^}]*)\}", html, re.S)
        self.assertIsNotNone(root_css)
        declarations = dict(
            re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", root_css.group(1))
        )

        self.assertRegex(root_css.group(1), r"\bcolor-scheme\s*:\s*light\s*;")
        self.assertNotIn("color-scheme: light dark", html)
        self.assertNotRegex(html, r"\bCanvas(?:Text)?\b")
        for name in (
            "--page-bg",
            "--page-fg",
            "--surface",
            "--border",
            "--trust-driver",
            "--trust-substrate",
            "--trust-projection",
            "--trust-external",
            "--state-selected",
            "--state-related",
            "--state-error",
            "--state-success",
        ):
            self.assertIn(name, declarations)
        self.assertEqual(len(set(declarations.values())), len(declarations.values()))
        self.assertRegex(html, r"body\s*\{[^}]*background:\s*var\(--page-bg\)")
        self.assertRegex(html, r"body\s*\{[^}]*color:\s*var\(--page-fg\)")
        for token in (
            "--trust-driver",
            "--trust-substrate",
            "--trust-projection",
            "--trust-external",
            "--state-selected",
            "--state-related",
            "--state-error",
            "--state-success",
        ):
            self.assertIn(f"var({token})", html)

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
        self.assertTrue(all(a.get("data-trust-zone") for a in components))
        self.assertTrue(
            all(a.get("data-durability") in {"durable", "ephemeral"} for a in components)
        )
        self.assertGreaterEqual(len(scenarios), 13)
        self.assertGreaterEqual(len(questions), 24)
        self.assertEqual(
            len({a["data-question-id"] for a in questions}),
            len(questions),
        )
        self.assertIn("noscript", html.lower())
        self.assertGreater(len(" ".join(parser.noscript_text)), 500)

    def test_interaction_contract_and_accessibility_markers_exist(self) -> None:
        html, _ = parsed_atlas()
        self.assertRegex(html, r"(?:window\.)?AtlasApp\s*=\s*\(\(\)\s*=>")
        for name in (
            "selectComponent",
            "setMapMode",
            "setFilters",
            "selectScenario",
            "selectTraceStep",
            "answerQuestion",
            "resetQuiz",
        ):
            self.assertRegex(html, rf"\b{name}\b")
        for marker in (
            "musubi-system-atlas.quiz.v1",
            "prefers-reduced-motion",
            'aria-live="polite"',
            ":focus-visible",
            "@media (max-width: 1100px)",
            "@media (max-width: 760px)",
        ):
            self.assertIn(marker, html)

    def test_component_controls_and_trace_navigation_are_complete(self) -> None:
        html, _ = parsed_atlas()
        for control_id in (
            "component-search",
            "trust-zone-filter",
            "durability-filter",
            "map-mode",
            "trace-scenario",
            "trace-previous",
            "trace-next",
            "trace-restart",
            "trace-steps",
        ):
            self.assertIn(f'id="{control_id}"', html)
        for indexed_field in (
            "data-responsibility",
            "data-source",
            "data-enforces",
            "data-failure-modes",
        ):
            self.assertIn(indexed_field, html)
        self.assertIn('aria-current="step"', html)
        self.assertIn("incoming", html)
        self.assertIn("outgoing", html)
        self.assertIn("['react-view-model','console','renders']", html)

    def test_cards_have_keyboard_selection_and_relationship_state(self) -> None:
        html, _ = parsed_atlas()
        for marker in (
            "component-select",
            'aria-pressed',
            "data-relationship-state",
            "is-related",
            "is-muted",
            "Trạng thái quan hệ",
        ):
            self.assertIn(marker, html)
        self.assertIn("card.dataset.component", html)
        self.assertIn("relatedIds.has(card.dataset.component)", html)
        for component in ("nested-dispatch", "context-fit", "budget", "subagent-audit"):
            self.assertIn(f'data-component="{component}"', html)

    def test_focus_is_preserved_after_trace_and_quiz_rerenders(self) -> None:
        html, _ = parsed_atlas()
        self.assertIn('id="trace-status"', html)
        self.assertIn('id="trace-status" aria-live="polite"', html)
        self.assertIn("focusActiveStep", html)
        self.assertIn("activeStepButton.focus()", html)
        self.assertIn('tabindex="-1" data-question-result', html)
        self.assertIn("quizResult?.focus()", html)
        self.assertIn("data-trace-step", html)
        self.assertIn("aria-current=\"step\"", html)

    def test_responsive_regions_have_operable_drawers(self) -> None:
        html, _ = parsed_atlas()
        for control_id, target_id in (
            ("nav-drawer-toggle", "atlas-nav"),
            ("evidence-drawer-toggle", "evidence-drawer"),
            ("nav-drawer-close", "atlas-nav"),
            ("evidence-drawer-close", "evidence-drawer"),
        ):
            self.assertRegex(
                html,
                rf'id="{control_id}"[^>]+aria-controls="{target_id}"',
            )
        self.assertIn('aria-expanded="false"', html)
        self.assertIn("setDrawer", html)
        self.assertIn("event.key === 'Escape'", html)
        self.assertIn("target.inert = drawerMode && !state.drawers[name]", html)
        self.assertIn("destination.setAttribute('tabindex', '-1')", html)
        self.assertIn("destination.focus()", html)
        self.assertIn('grid-template-areas: "header header header" "nav main evidence"', html)
        self.assertNotIn(":focus-within", html)

    def test_responsive_drawer_css_is_javascript_gated(self) -> None:
        html, _ = parsed_atlas()
        for selector in (
            'html[data-javascript="enabled"] #evidence-drawer',
            'html[data-javascript="enabled"] #atlas-nav',
            'html[data-javascript="enabled"] #evidence-drawer-toggle',
            'html[data-javascript="enabled"] #nav-drawer-toggle',
        ):
            self.assertIn(selector, html)
        self.assertNotRegex(
            html,
            r"(?m)^\s*#(?:evidence-drawer|atlas-nav)\s*\{[^}]*transform:",
        )

    def test_quiz_has_one_answer_and_explanation_per_question(self) -> None:
        html, _ = parsed_atlas()
        blocks = re.findall(r"const QUIZ_QUESTIONS = (\[.*?\]);\s*const", html, re.S)
        self.assertEqual(len(blocks), 1)
        questions = json.loads(blocks[0])
        self.assertGreaterEqual(len(questions), 24)
        self.assertEqual(len({question["id"] for question in questions}), len(questions))
        self.assertTrue(all(len(question["options"]) in (3, 4) for question in questions))
        self.assertTrue(
            all(
                isinstance(question["answer"], int)
                and 0 <= question["answer"] < len(question["options"])
                for question in questions
            )
        )
        self.assertTrue(all(question["explanation"] and question["section"] for question in questions))
        self.assertTrue(
            all(
                question["difficulty"]
                in {"boundary", "trace", "economics", "failure", "evolution"}
                for question in questions
            )
        )
        self.assertEqual(
            Counter(question["chapter"] for question in questions),
            Counter(
                {
                    "orientation": 4,
                    "components": 4,
                    "traces": 4,
                    "invariants": 4,
                    "economics": 4,
                    "evolution": 4,
                }
            ),
        )
        causal_corpus = " ".join(
            question["prompt"]
            + " "
            + question["options"][question["answer"]]
            + " "
            + question["explanation"]
            for question in questions
        ).casefold()
        for causal_marker in (
            "vendor neutrality",
            "root muốn sửa file",
            "same-turn",
            "evaluator",
            "pushed skill",
            "final turn",
            "childtokenbudget",
            "elision marker",
            "sanitize",
            "join musubi.db",
            "external mcp",
            "helper role",
        ):
            self.assertIn(causal_marker, causal_corpus)

    def test_atlas_app_exports_exact_public_interface(self) -> None:
        html, _ = parsed_atlas()
        public_return = re.search(r"return \{([^}]+)\};\s*\}\)\(\);", html, re.S)
        self.assertIsNotNone(public_return)
        exports = {
            name.strip() for name in public_return.group(1).split(",") if name.strip()
        }
        self.assertEqual(
            exports,
            {
                "selectComponent",
                "setMapMode",
                "setFilters",
                "selectScenario",
                "selectTraceStep",
                "answerQuestion",
                "resetQuiz",
            },
        )

    def test_map_relationship_table_matches_svg_edges(self) -> None:
        html, _ = parsed_atlas()
        block = re.search(
            r"const MAP_RELATIONSHIPS = \[(.*?)\];\s*const state",
            html,
            re.S,
        )
        self.assertIsNotNone(block)
        relationships = re.findall(
            r"\['([^']+)','([^']+)','([^']+)'\]", block.group(1)
        )
        self.assertEqual(len(relationships), len(re.findall(r'class="edge"', html)))
        self.assertEqual(
            relationships,
            [
                ("cli", "goal-state", "launches"),
                ("console", "pipeline-runner", "launches --pipeline"),
                ("goal-state", "worker-loop", "routes"),
                ("worker-loop", "pipeline-runner", "dispatches"),
                ("worker-loop", "lm-router", "calls"),
                ("lm-router", "model-provider", "requests"),
                ("worker-loop", "mcp-server", "calls tools"),
                ("mcp-server", "policy", "gates"),
                ("mcp-server", "evaluator-firewall", "limits context"),
                ("mcp-server", "skills", "injects"),
                ("mcp-server", "memory", "loads"),
                ("mcp-server", "compression", "compresses"),
                ("mcp-server", "state-db", "persists state"),
                ("mcp-server", "audit-db", "records audit"),
                ("worker-loop", "external-mcp", "routes namespaced calls"),
                ("state-db", "rust-projection", "reads state"),
                ("audit-db", "rust-projection", "reads audit"),
                ("rust-projection", "react-view-model", "projects"),
                ("react-view-model", "console", "renders"),
            ],
        )
    def test_quiz_no_script_key_mirrors_every_answer_and_section(self) -> None:
        html, parser = parsed_atlas()
        block = re.search(r"const QUIZ_QUESTIONS = (\[.*?\]);\s*const", html, re.S)
        self.assertIsNotNone(block)
        questions = json.loads(block.group(1))
        fallback = " ".join(parser.noscript_text)
        fallback_markup_match = re.search(r"<noscript>(.*?)</noscript>", html, re.S)
        self.assertIsNotNone(fallback_markup_match)
        fallback_markup = fallback_markup_match.group(1)
        for question in questions:
            self.assertIn(question["prompt"], fallback)
            self.assertIn(question["options"][question["answer"]], fallback)
            self.assertIn(question["explanation"], fallback)
            self.assertIn(f'href="#{question["section"]}"', fallback_markup)
        self.assertIn("try {", html)
        self.assertIn("localStorage.getItem", html)
        self.assertIn("localStorage.setItem", html)
        self.assertIn("localStorage.removeItem", html)

    def test_every_embedded_source_path_exists(self) -> None:
        html, _ = parsed_atlas()
        paths = set(re.findall(r'data-source="([^"]+)"', html))
        self.assertGreaterEqual(len(paths), 18)
        self.assertTrue(all(re.fullmatch(r"[^:]+:\d+", path) for path in paths))
        missing = sorted(
            path for path in paths if not (ROOT / path.split(":", 1)[0]).exists()
        )
        self.assertEqual(missing, [])
        out_of_range = []
        for source in paths:
            path, line = source.rsplit(":", 1)
            if int(line) > len((ROOT / path).read_text(encoding="utf-8").splitlines()):
                out_of_range.append(source)
        self.assertEqual(out_of_range, [])

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
        self.assertTrue(all(all(a.get(field) for field in required) for a in components))
        self.assertTrue(all(a.get("data-evidence-kind") in {"verified", "rationale"} for a in components))
        self.assertTrue(
            all(
                a.get("data-musubi-tier") in {"substrate", "ephemeral"}
                for a in components
                if a.get("data-musubi-tier")
            )
        )
        ephemeral = [a for a in components if a.get("data-durability") == "ephemeral"]
        self.assertGreaterEqual(len(ephemeral), 1)
        self.assertTrue(
            all(a.get("data-expires-when") and a.get("data-cost-lever") for a in ephemeral)
        )
        for badge in ("verified", "rationale", "historical", "open", "stale"):
            self.assertIn(f'data-evidence-kind="{badge}"', html)
        invariants = [a for a in parser.attrs if "data-invariant" in a]
        self.assertEqual(
            {a["data-invariant"] for a in invariants},
            {"HI #1", "HI #2", "HI #3", "HI #5", "HI #7", "HI #8", "HI #9"},
        )
        self.assertTrue(
            all(a.get("data-evidence-kind") == "verified" and a.get("data-source") for a in invariants)
        )
        open_questions = [
            a for a in parser.attrs
            if a.get("data-evidence-kind") == "open" and a.get("_tag") != "span"
        ]
        self.assertGreaterEqual(len(open_questions), 1)
        self.assertTrue(all(a.get("data-source") for a in open_questions))

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
        scenario_id_list = re.findall(r"\bid:\s*'([^']+)'", html)
        scenario_ids = set(scenario_id_list)
        self.assertEqual(scenario_ids, expected_scenarios)
        self.assertEqual(len(scenario_id_list), 13)
        trace_data = html.split("const TRACE_SCENARIOS = [", 1)[1].split(
            "const TRACE_STEP_FIELDS", 1
        )[0]
        step_fields = {
            "component", "title", "input", "output", "decision", "lmCall",
            "economics", "evidence", "failure",
        }
        step_records = re.findall(
            r"\{\s*component:\s*'([^']+)'(.*?)\}\s*,?", trace_data, re.S
        )
        self.assertGreaterEqual(len(step_records), 26)
        lm_call_owners = set()
        for component, record in step_records:
            present = set(re.findall(r"\b(\w+):", "component:" + record))
            self.assertLessEqual(step_fields, present)
            string_values = dict(re.findall(r"\b(\w+):\s*'([^']*)'", record))
            for field in step_fields - {"component", "lmCall"}:
                self.assertTrue(string_values.get(field, "").strip(), f"{component}:{field}")
            evidence_path, evidence_line = string_values["evidence"].rsplit(":", 1)
            source = ROOT / evidence_path
            self.assertTrue(source.exists(), string_values["evidence"])
            self.assertLessEqual(
                int(evidence_line), len(source.read_text(encoding="utf-8").splitlines())
            )
            if "lmCall: true" in record:
                lm_call_owners.add(component)
                self.assertIn(component, {"worker-loop", "lm-router", "model-provider"})
        self.assertLessEqual({"lm-router", "worker-loop"}, lm_call_owners)
        self.assertIn("musubi/agent/run.py:757", trace_data)
        self.assertIn("musubi/agent/run.py:729", trace_data)
        self.assertIn("musubi/agent/run.py:1258", trace_data)
        self.assertIn("musubi/agent/run.py:1327", trace_data)

        scenario_blocks = {
            match.group(1): match.group(2)
            for match in re.finditer(
                r"\bid:\s*'([^']+)'.*?steps:\s*\[(.*?)\]\s*\n\s*\}",
                trace_data,
                re.S,
            )
        }
        self.assertEqual(set(scenario_blocks), expected_scenarios)
        for scenario_id, block in scenario_blocks.items():
            self.assertGreaterEqual(len(re.findall(r"\bcomponent:\s*'", block)), 2, scenario_id)
        non_model_scenarios = {"console-projection", "historical-console"}
        for scenario_id in expected_scenarios - non_model_scenarios:
            self.assertIn("lmCall: true", scenario_blocks[scenario_id], scenario_id)
            self.assertIn("lmCall: false", scenario_blocks[scenario_id], scenario_id)

    def test_inventory_axes_and_evidence_cover_reviewed_ownership(self) -> None:
        html, parser = parsed_atlas()
        component_ids = {
            attrs["data-component"]
            for attrs in parser.attrs
            if "data-component" in attrs
        }
        self.assertLessEqual(
            {
                "compression-eval", "prompt-catalog", "vendor-implementations",
                "conversation-store", "stage-attempt-store", "agent-cycle-store",
                "audit-projection", "policy-projection", "models-projection",
                "skills-projection", "settings-projection",
            },
            component_ids,
        )
        for source in (
            "musubi/agent/boundary.py",
            "musubi/validation/subagent_context.py",
            "gui/src/views/Orchestrator.jsx",
            "gui/src/views/Pipeline.jsx",
            "musubi/tests/test_agent_context.py",
            "musubi/tests/test_agent_budget.py",
        ):
            self.assertRegex(html, re.escape(source) + r":\d+")

        mapped = [a for a in parser.attrs if "data-map-component" in a]
        self.assertTrue(all(a.get("data-trust-zone") and a.get("data-durability") for a in mapped))
        components_by_id = {
            a["data-component"]: a for a in parser.attrs if "data-component" in a
        }
        self.assertTrue(
            all(
                a["data-durability"]
                == components_by_id[a["data-map-component"]]["data-durability"]
                for a in mapped
            )
        )
        shape_by_trust = {
            "model-calling driver": "rect",
            "zero-LLM governance substrate": "polygon",
            "read-only operator projection": "ellipse",
            "external system": "circle",
        }
        self.assertTrue(
            all(a.get("data-map-shape") == shape_by_trust[a["data-trust-zone"]] for a in mapped)
        )
        self.assertEqual(len(set(shape_by_trust.values())), 4)
        self.assertIn('[data-trust-zone="model-calling driver"]', html)
        self.assertIn('[data-durability="ephemeral"]', html)
        self.assertIn("stroke-dasharray", html)

        historical = [
            a for a in parser.attrs
            if a.get("data-evidence-kind") == "historical" and a.get("_tag") != "span"
        ]
        stale = [
            a for a in parser.attrs
            if a.get("data-evidence-kind") == "stale" and a.get("_tag") != "span"
        ]
        self.assertGreaterEqual(len(historical), 9)
        self.assertGreaterEqual(len(stale), 6)
        self.assertTrue(all(a.get("data-source") for a in historical + stale))

    def test_component_lifecycle_matches_declared_source_metadata(self) -> None:
        html, parser = parsed_atlas()
        components = [a for a in parser.attrs if "data-component" in a]
        cli = next(a for a in components if a["data-component"] == "cli")
        self.assertEqual(cli["data-source"], "musubi/agent/run.py:1258")
        self.assertEqual(cli.get("data-related-source"), "musubi/agent/run.py:1327")
        provider = next(a for a in components if a["data-component"] == "model-provider")
        self.assertEqual(provider["data-trust-zone"], "external system")
        self.assertNotIn("data-musubi-tier", provider)
        self.assertNotIn("data-expires-when", provider)
        self.assertNotIn("data-cost-lever", provider)
        compared = 0
        for component in components:
            if component["data-trust-zone"] == "external system":
                continue
            source_path = component["data-source"].rsplit(":", 1)[0]
            source = ROOT / source_path
            header = "\n".join(source.read_text(encoding="utf-8").splitlines()[:12])
            tier_match = re.search(r"musubi-tier:\s*(substrate|ephemeral)", header)
            if not tier_match:
                continue
            compared += 1
            expires_match = re.search(r"expires-when:\s*([^\r\n]+)", header)
            cost_match = re.search(r"cost-lever:\s*([^\r\n]+)", header)
            self.assertEqual(component.get("data-musubi-tier"), tier_match.group(1), component["data-component"])
            expected_durability = "durable" if tier_match.group(1) == "substrate" else "ephemeral"
            self.assertEqual(component.get("data-durability"), expected_durability, component["data-component"])
            if expires_match:
                declared = expires_match.group(1).strip().removesuffix("*/").strip()
                expected = "never" if declared.lower().startswith("never") else declared
                self.assertEqual(component.get("data-expires-when"), expected, component["data-component"])
            if cost_match:
                expected = cost_match.group(1).strip().removesuffix("*/").strip()
                self.assertEqual(component.get("data-cost-lever"), expected, component["data-component"])
        self.assertGreaterEqual(compared, 15)
        self.assertNotRegex(html, r'data-expires-when="[^"]*\b(?:that|the|at|of|to|be|must be)"')

    def test_new_maintainer_content_is_vietnamese(self) -> None:
        html, parser = parsed_atlas()
        vietnamese = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I)
        reviewed = {
            "compression-eval", "prompt-catalog", "vendor-implementations",
            "conversation-store", "stage-attempt-store", "agent-cycle-store",
            "audit-projection", "policy-projection", "models-projection",
            "skills-projection", "settings-projection",
        }
        cards = [a for a in parser.attrs if a.get("data-component") in reviewed]
        self.assertEqual({a["data-component"] for a in cards}, reviewed)
        self.assertTrue(all(vietnamese.search(a["data-responsibility"]) for a in cards))
        self.assertTrue(all(vietnamese.search(a["data-why"]) for a in cards))
        trace_data = html.split("const TRACE_SCENARIOS = [", 1)[1].split(
            "const TRACE_STEP_FIELDS", 1
        )[0]
        scenario_headers = re.findall(r"title:\s*'([^']+)',\s*summary:\s*'([^']+)'", trace_data)
        self.assertEqual(len(scenario_headers), 13)
        self.assertTrue(all(vietnamese.search(title) and vietnamese.search(summary) for title, summary in scenario_headers))
        for field in ("title", "decision", "economics", "failure"):
            values = re.findall(rf"\b{field}:\s*'([^']+)'", trace_data)
            self.assertGreaterEqual(len(values), 35)
            self.assertTrue(all(vietnamese.search(value) for value in values), field)

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
