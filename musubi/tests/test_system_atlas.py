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


if __name__ == "__main__":
    unittest.main()
