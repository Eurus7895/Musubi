from __future__ import annotations

import sys
from pathlib import Path

import pytest

import cli
import server


@pytest.fixture(autouse=True)
def restore_server_tools() -> None:
    original = dict(server.mcp._tool_manager._tools)
    try:
        yield
    finally:
        server.mcp._tool_manager._tools = original


def test_server_apply_agent_surface_hides_driver_only_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[set[str]] = []

    def fake_run(*, transport: str) -> None:
        seen.append(set(server.mcp._tool_manager._tools))

    monkeypatch.setattr(server.mcp, "run", fake_run)

    server.serve(surface="agent")

    assert seen
    assert "musubi_read_file" in seen[0]
    assert "musubi_write_stage" not in seen[0]
    assert "musubi_read_stage" not in seen[0]


def test_server_full_surface_keeps_all_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[set[str]] = []

    def fake_run(*, transport: str) -> None:
        seen.append(set(server.mcp._tool_manager._tools))

    monkeypatch.setattr(server.mcp, "run", fake_run)

    server.serve(surface="full")

    assert "musubi_write_stage" in seen[0]
    assert "musubi_read_stage" in seen[0]


def test_cli_serve_passes_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    musubi_dir = str(Path(__file__).resolve().parent.parent)

    def fake_serve(surface: str = "full") -> None:
        called.append(surface)

    monkeypatch.setattr("server.serve", fake_serve)
    monkeypatch.setattr(sys, "argv", ["musubi", "serve", "--surface", "agent"])
    monkeypatch.syspath_prepend(musubi_dir)

    cli.main()

    assert called == ["agent"]
