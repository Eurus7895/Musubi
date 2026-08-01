"""Tests for driver-side context controls.

musubi-tier: substrate test - pins the zero-LLM token-economy transforms.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

from agent.context import (
    DEFAULT_EFFORT_CEILING,
    DEFAULT_EFFORT_FLOOR,
    build_system_prompt,
    context_budget,
    effort_floor,
    fit_context,
    resolve_effort_bounds,
)


def test_system_prompt_carries_terse_instruction() -> None:
    prompt = build_system_prompt()
    assert "concise" in prompt.lower()
    assert "do not restate" in prompt.lower()
    assert "call tools directly" in prompt.lower()
    assert "final answer" in prompt.lower()


def test_system_prompt_mentions_skill_recommendations() -> None:
    prompt = build_system_prompt()

    assert "musubi_list_skills" in prompt
    # Option 3: the root recommends a skill for a worker and pushes it via the
    # spawn, rather than reading skill bodies into its own context.
    assert "pushed_skill_id" in prompt
    assert "for_role" in prompt


def test_system_prompt_routes_mutating_work_to_workers() -> None:
    prompt = build_system_prompt()

    assert "musubi_spawn_subagent" in prompt
    assert "coder" in prompt
    assert "investigator" in prompt
    assert "write" in prompt.lower()


def test_system_prompt_requires_platform_native_validation_commands() -> None:
    prompt = build_system_prompt().lower()

    assert "platform-native" in prompt
    assert "windows" in prompt
    assert "powershell" in prompt
    assert "wc" in prompt
    assert "tail" in prompt
    assert "printing an entire artifact" in prompt


def test_system_prompt_appends_extra_after_steering() -> None:
    prompt = build_system_prompt("task-specific note")
    assert prompt.endswith("task-specific note")
    assert "concise" in prompt.lower()


def test_effort_floor_default(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("MUSUBI_EFFORT_TOKENS", raising=False)
    assert effort_floor() == DEFAULT_EFFORT_FLOOR


def test_effort_floor_env_override(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MUSUBI_EFFORT_TOKENS", "512")
    assert effort_floor() == 512


def test_effort_floor_ignores_garbage(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MUSUBI_EFFORT_TOKENS", "lots")
    assert effort_floor() == DEFAULT_EFFORT_FLOOR


def test_mutate_effort_opens_at_shared_ceiling() -> None:
    assert resolve_effort_bounds(
        can_mutate=True,
        worker_max_output=None,
        model_output_override=None,
    ) == (DEFAULT_EFFORT_CEILING, DEFAULT_EFFORT_CEILING)


def test_read_only_effort_keeps_floor_and_shared_ceiling(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("MUSUBI_EFFORT_TOKENS", raising=False)
    assert resolve_effort_bounds(
        can_mutate=False,
        worker_max_output=None,
        model_output_override=None,
    ) == (DEFAULT_EFFORT_FLOOR, DEFAULT_EFFORT_CEILING)


def test_worker_output_budget_overrides_shared_ceiling() -> None:
    assert resolve_effort_bounds(
        can_mutate=True,
        worker_max_output=32768,
        model_output_override=None,
    ) == (32768, 32768)


def test_model_output_override_clamps_worker_ceiling() -> None:
    assert resolve_effort_bounds(
        can_mutate=True,
        worker_max_output=32768,
        model_output_override=8192,
    ) == (8192, 8192)


def _convo_with_big_results() -> list[dict]:
    big = "X" * 5000
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "the task"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "a", "name": "t", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "a", "content": big}],
        },
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "b", "name": "t", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "b", "content": big}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]


def test_fit_context_noop_when_under_budget() -> None:
    msgs = _convo_with_big_results()
    out = fit_context(msgs, budget_chars=1_000_000)
    assert out is msgs


def test_fit_context_disabled_when_budget_zero() -> None:
    msgs = _convo_with_big_results()
    assert fit_context(msgs, budget_chars=0) is msgs


def test_fit_context_elides_oldest_largest_first() -> None:
    msgs = _convo_with_big_results()
    out = fit_context(msgs, budget_chars=500, keep_last_turns=2)
    elided = out[3]["content"][0]["content"]
    assert "context-trimmed" in elided
    assert out[3]["content"][0]["type"] == "tool_result"
    assert out[3]["content"][0]["tool_use_id"] == "a"
    assert out[0] is msgs[0]
    assert out[1] is msgs[1]


def test_fit_context_keeps_recent_turns() -> None:
    msgs = _convo_with_big_results()
    out = fit_context(msgs, budget_chars=6000, keep_last_turns=2)
    assert out[5]["content"][0]["content"] == "X" * 5000


def test_fit_context_elides_large_file_tool_use_inputs_even_under_budget() -> None:
    raw = "<html>" + ("A" * 2400) + "</html>"
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "create dashboard"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "append-1",
                    "name": "musubi_append_file",
                    "input": {
                        "path": "dashboard.html",
                        "content": raw,
                        "expected_offset": 0,
                    },
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "append-1",
                    "content": (
                        '{"status":"ok","bytes_written":2413,'
                        '"total_bytes":2413}'
                    ),
                }
            ],
        },
    ]

    out = fit_context(msgs, budget_chars=1_000_000)

    payload = out[2]["content"][0]["input"]
    assert payload["path"] == "dashboard.html"
    assert payload["expected_offset"] == 0
    assert payload["content"].startswith("[musubi:elided-tool-arg")
    assert "chars=" in payload["content"]
    assert "bytes=" in payload["content"]
    assert "sha256=" in payload["content"]
    assert "DO NOT copy this marker" in payload["content"]
    assert "regenerate the original text from scratch" in payload["content"]
    assert raw not in json.dumps(out)
    assert msgs[2]["content"][0]["input"]["content"] == raw


def test_fit_context_elides_large_edit_file_strings() -> None:
    old = "old-line\n" + ("B" * 1600)
    new = "new-line\n" + ("C" * 1700)
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "edit file"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "edit-1",
                    "name": "musubi_edit_file",
                    "input": {
                        "path": "src/app.py",
                        "old_string": old,
                        "new_string": new,
                    },
                }
            ],
        },
    ]

    out = fit_context(msgs, budget_chars=1_000_000)

    payload = out[2]["content"][0]["input"]
    assert payload["old_string"].startswith("[musubi:elided-tool-arg")
    assert payload["new_string"].startswith("[musubi:elided-tool-arg")
    assert old not in json.dumps(out)
    assert new not in json.dumps(out)


def test_fit_context_keeps_small_file_tool_use_inputs() -> None:
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "small"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "write-1",
                    "name": "musubi_write_file",
                    "input": {"path": "note.txt", "content": "short"},
                }
            ],
        },
    ]

    assert fit_context(msgs, budget_chars=1_000_000) is msgs


def test_fit_context_keeps_non_file_tool_use_inputs() -> None:
    raw = "X" * 3000
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "run command"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "cmd-1",
                    "name": "musubi_run_command",
                    "input": {"command": raw},
                }
            ],
        },
    ]

    assert fit_context(msgs, budget_chars=1_000_000) is msgs


def test_fit_context_compresses_old_tool_results_before_trimming(tmp_path: Path) -> None:
    original = json.dumps(
        {
            "events": [
                {"kind": "tool", "status": "ok", "path": f"src/module_{i % 3}.py"}
                for i in range(240)
            ]
        },
        indent=2,
    )
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "task"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "a", "name": "read", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "a", "content": original}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "next"}]},
    ]

    out = fit_context(
        msgs,
        budget_chars=1800,
        keep_last_turns=1,
        compression_db_path=tmp_path / "compression.db",
    )

    packed = out[3]["content"][0]["content"]
    assert "[musubi:compressed" in packed
    assert "context-trimmed" not in packed
    assert "tool_use_id" in out[3]["content"][0]


def test_fit_context_loads_local_compressor_when_name_is_taken(
    tmp_path: Path,
    monkeypatch,
) -> None:  # noqa: ANN001
    fake = types.ModuleType("compression")
    fake.__file__ = str(tmp_path / "stdlib" / "compression" / "__init__.py")
    monkeypatch.setitem(sys.modules, "compression", fake)
    original = json.dumps({"items": [{"id": i, "value": "A" * 20} for i in range(160)]})

    out = fit_context(
        [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "task"},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "a", "content": original}
                ],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "next"}]},
        ],
        budget_chars=1200,
        keep_last_turns=1,
        compression_db_path=tmp_path / "compression.db",
    )

    packed = out[2]["content"][0]["content"]
    assert "[musubi:compressed" in packed
    assert sys.modules["compression"] is fake


def test_fit_context_skips_already_compressed_tool_results(
    monkeypatch,
) -> None:  # noqa: ANN001
    import agent.context as context_mod

    marker = (
        f"{'summary ' * 40}\n\n"
        "[musubi:compressed kind=json ref=oldref chars 5000->400; "
        'call musubi_retrieve("oldref") for the verbatim original]'
    )

    def fail_if_called(*_args, **_kwargs):  # noqa: ANN001
        raise AssertionError("already compressed content should not be repacked")

    monkeypatch.setattr(context_mod, "_compress_for_context", fail_if_called)

    out = fit_context(
        [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "task"},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "a", "content": marker}],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "next"}]},
        ],
        budget_chars=120,
        keep_last_turns=1,
    )

    stub = out[2]["content"][0]["content"]
    assert stub.startswith("[context-trimmed:")
    assert 'musubi_retrieve("oldref")' in stub


def test_fit_context_trims_compressed_result_when_budget_still_too_small(
    tmp_path: Path,
) -> None:
    original = json.dumps({"items": [{"id": i, "value": "A" * 50} for i in range(120)]})
    out = fit_context(
        [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "task"},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "a", "content": original}],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "x"}]},
        ],
        budget_chars=250,
        keep_last_turns=1,
        compression_db_path=tmp_path / "compression.db",
    )

    stub = out[2]["content"][0]["content"]
    assert "context-trimmed" in stub
    assert "musubi_retrieve(" in stub


def test_fit_context_trims_largest_remaining_block_after_compression(
    tmp_path: Path,
) -> None:
    compressible = json.dumps({"items": [{"id": i} for i in range(2_000)]}, indent=2)
    uncompressible = "Z" * 20_000
    out = fit_context(
        [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "task"},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "json", "content": compressible}
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "raw", "content": uncompressible}
                ],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "recent"}]},
        ],
        budget_chars=7_000,
        keep_last_turns=1,
        compression_db_path=tmp_path / "compression.db",
    )

    packed_json = out[2]["content"][0]["content"]
    packed_raw = out[3]["content"][0]["content"]
    assert "[musubi:compressed" in packed_json
    assert "context-trimmed" not in packed_json
    assert packed_raw.startswith("[context-trimmed:")


def test_fit_context_preserves_retrieve_marker() -> None:
    marker = 'see [musubi:compressed ref=abc; call musubi_retrieve("abc123") ok]'
    body = "Y" * 5000 + "\n" + marker
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "task"},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "a", "content": body}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "x"}]},
    ]
    out = fit_context(msgs, budget_chars=500, keep_last_turns=1)
    stub = out[2]["content"][0]["content"]
    assert 'musubi_retrieve("abc123")' in stub


def test_fit_context_does_not_mutate_input() -> None:
    msgs = _convo_with_big_results()
    before = json.dumps(msgs)
    fit_context(msgs, budget_chars=6000, keep_last_turns=2)
    assert json.dumps(msgs) == before


def test_context_budget_env_override(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("MUSUBI_CONTEXT_BUDGET", "1234")
    assert context_budget() == 1234


def test_split_system_pops_leading_system() -> None:
    from agent.vendors.anthropic_router import _split_system

    sys_text, body = _split_system([
        {"role": "system", "content": "you are x"},
        {"role": "user", "content": "hi"},
    ])
    assert sys_text == "you are x"
    assert body == [{"role": "user", "content": "hi"}]


def test_split_system_noop_without_leading_system() -> None:
    from agent.vendors.anthropic_router import _split_system

    msgs = [{"role": "user", "content": "hi"}]
    sys_text, body = _split_system(msgs)
    assert sys_text is None
    assert body is msgs


def test_cache_aligned_tools_marks_last_only() -> None:
    from agent.vendors.anthropic_router import _cache_aligned_tools

    tools = [{"name": "a"}, {"name": "b"}]
    out = _cache_aligned_tools(tools, cache=True)
    assert "cache_control" not in out[0]
    assert out[-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in tools[-1]


def test_cache_aligned_tools_passthrough_when_disabled() -> None:
    from agent.vendors.anthropic_router import _cache_aligned_tools

    tools = [{"name": "a"}]
    assert _cache_aligned_tools(tools, cache=False) is tools


def test_system_param_block_when_cached_string_when_not() -> None:
    from agent.vendors.anthropic_router import _system_param

    assert _system_param("hi", cache=False) == "hi"
    blocks = _system_param("hi", cache=True)
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert _system_param(None, cache=True) is None


def test_system_param_caches_only_stable_prompt_prefix() -> None:
    from agent.vendors.anthropic_router import _system_param

    prompt = build_system_prompt("workspace-specific note")
    blocks = _system_param(prompt, cache=True)

    assert len(blocks) == 2
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "workspace-specific note" not in blocks[0]["text"]
    assert blocks[1] == {"type": "text", "text": "workspace-specific note"}


def test_cache_enabled_default_and_optout(monkeypatch) -> None:  # noqa: ANN001
    from agent.vendors.anthropic_router import _cache_enabled

    monkeypatch.delenv("MUSUBI_PROMPT_CACHE", raising=False)
    assert _cache_enabled() is True
    monkeypatch.setenv("MUSUBI_PROMPT_CACHE", "0")
    assert _cache_enabled() is False
