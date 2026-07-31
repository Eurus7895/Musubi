from __future__ import annotations

import json

import server
from skills.recommender import recommend_skills
from skills.skill_loader import SkillMeta
from validation.context_builder import AGENT_SKILL_ALLOWLIST


def _meta(
    skill_id: str,
    *,
    title: str | None = None,
    description: str = "",
    triggers: list[str] | None = None,
    tools: list[str] | None = None,
) -> SkillMeta:
    return SkillMeta(
        skill_id=skill_id,
        title=title or skill_id,
        path=f"/skills/{skill_id}/SKILL.md",
        description=description,
        triggers=triggers or [],
        tools=tools or [],
    )


def test_recommends_skill_by_trigger_text() -> None:
    skills = [
        _meta("compression-aware-context", triggers=["musubi_retrieve", "compressed output"]),
        _meta("research", triggers=["web search"]),
    ]

    out = recommend_skills(
        "Review this compressed output and decide whether to call musubi_retrieve.",
        skills,
    )

    assert [r.skill_id for r in out] == ["compression-aware-context"]
    assert out[0].confidence > 0.5
    assert "trigger" in out[0].reasons[0]


def test_recommends_skill_by_tool_used() -> None:
    skills = [
        _meta("compression-aware-context", tools=["musubi_retrieve"]),
        _meta("docs-writing", tools=[]),
    ]

    out = recommend_skills(
        "Continue the task.",
        skills,
        tools_used=["musubi_retrieve"],
    )

    assert out[0].skill_id == "compression-aware-context"
    assert any("tool" in reason for reason in out[0].reasons)


def test_returns_empty_when_no_signal_matches() -> None:
    skills = [_meta("research", triggers=["web search"])]

    assert recommend_skills("Rename this local variable.", skills) == []


def test_respects_limit_and_score_order() -> None:
    skills = [
        _meta("one", triggers=["alpha"], tools=["musubi_read_file"]),
        _meta("two", triggers=["alpha"]),
        _meta("three", triggers=["alpha"]),
    ]

    out = recommend_skills(
        "alpha",
        skills,
        tools_used=["musubi_read_file"],
        limit=2,
    )

    assert [r.skill_id for r in out] == ["one", "two"]


def test_server_recommend_skills_respects_agent_allowlist(monkeypatch) -> None:  # noqa: ANN001
    metas = [
        _meta(
            "compression-aware-context",
            title="Compression-aware Context",
            triggers=["musubi_retrieve"],
            tools=["musubi_retrieve"],
        ),
        _meta("code-review", title="Code Review", triggers=["security"]),
    ]
    monkeypatch.setattr(server.skill_loader, "list_skills", lambda: metas)
    monkeypatch.setattr(server, "_load_project_profile", lambda: None)
    monkeypatch.setitem(
        AGENT_SKILL_ALLOWLIST,
        "agent",
        {"compression-aware-context"},
    )

    payload = json.loads(server.musubi_recommend_skills(
        task="The output contains musubi_retrieve markers.",
        agent_name="agent",
    ))

    # The response echoes the CANONICAL role, so a caller naming itself
    # by the legacy spelling still sees one identity in the reply.
    assert payload["agent_name"] == "root"
    assert payload["filtered_by_profile"] is False
    assert [item["skill_id"] for item in payload["recommended"]] == [
        "compression-aware-context",
    ]


def test_server_recommend_skills_applies_project_profile(monkeypatch) -> None:  # noqa: ANN001
    metas = [
        _meta(
            "python",
            title="Python",
            triggers=["pytest"],
            tools=[],
        ),
        SkillMeta(
            skill_id="rust-only",
            title="Rust Only",
            path="/skills/rust-only/SKILL.md",
            applies_to={"languages": ["rust"]},
            triggers=["pytest"],
        ),
    ]
    monkeypatch.setattr(server.skill_loader, "list_skills", lambda: metas)
    monkeypatch.setattr(
        server,
        "_load_project_profile",
        lambda: {"language": "python", "secondary_languages": []},
    )
    monkeypatch.setitem(AGENT_SKILL_ALLOWLIST, "root", {"python", "rust-only"})

    payload = json.loads(server.musubi_recommend_skills(
        task="pytest is failing",
        agent_name="agent",
    ))

    assert payload["filtered_by_profile"] is True
    assert [item["skill_id"] for item in payload["recommended"]] == ["python"]


# ── the request elects; the conversation only breaks ties ──────────────────


def _web_and_py() -> list[SkillMeta]:
    return [
        _meta("web-ui", title="Web UI",
              triggers=["html", "css", "dashboard", "chart", "responsive"]),
        _meta("python", title="Python", triggers=["pytest", "traceback"]),
    ]


def test_context_alone_cannot_elect_a_skill() -> None:
    """The defect this pins: on turn 3 of a chat that had built an HTML
    dashboard, "change the language of the application" matched no skill at
    all, while the context summary hit five `web-ui` triggers for a score of
    200 — capped to confidence 0.99. The root asked which skill fitted, was
    told `web-ui` with near-certainty, and pushed it into a coder that was
    there to change some strings."""
    context = (
        "Earlier the user asked for a weather dashboard. index.html was "
        "created with a responsive css layout and a chart of temperatures."
    )
    out = recommend_skills(
        "change the language of the application",
        _web_and_py(),
        context_summary=context,
    )
    assert out == []


def test_context_still_breaks_a_tie_between_requested_skills() -> None:
    """It is not ignored — it just cannot outvote the request. Both skills
    match the request once; the context is what separates them."""
    context = "the project is an html dashboard with css and a chart"
    out = recommend_skills(
        "fix the chart traceback",
        _web_and_py(),
        context_summary=context,
    )
    assert [item.skill_id for item in out] == ["web-ui", "python"]
    assert "from conversation context" in " ".join(out[0].reasons)


def test_confidence_reflects_the_request_not_the_history() -> None:
    """It used to saturate: five context hits scored 200 and every such skill
    reported 0.99, so the number carried no information about whether the
    REQUEST matched. It is computed from the request score alone now, and a
    long history cannot inflate it."""
    skills = _web_and_py()
    long_context = " ".join(["html css dashboard chart responsive"] * 20)

    weak = recommend_skills("add a chart", skills, context_summary=long_context)
    strong = recommend_skills(
        "make the dashboard chart responsive", skills, context_summary="",
    )

    assert weak[0].skill_id == "web-ui" and strong[0].skill_id == "web-ui"
    assert weak[0].confidence < strong[0].confidence


def test_tools_used_this_turn_still_count_as_request_signal() -> None:
    out = recommend_skills(
        "keep going", _web_and_py(), tools_used=["pytest"], context_summary="",
    )
    assert [item.skill_id for item in out] == ["python"]
