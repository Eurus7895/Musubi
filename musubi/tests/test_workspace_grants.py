from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspace.grants import FolderGrant, RootRegistry, derive_alias


def test_registry_keeps_musubi_fixed_and_resolves_external_relative_paths(
    tmp_path: Path,
) -> None:
    musubi = tmp_path / "musubi"
    web = tmp_path / "web-app"
    musubi.mkdir()
    web.mkdir()
    registry = RootRegistry.build(
        musubi,
        [FolderGrant("g-web", "web", web)],
    )

    assert registry.root("musubi").path == musubi.resolve()
    assert registry.resolve("web", "src/App.tsx") == (
        web / "src" / "App.tsx"
    ).resolve()
    assert registry.resolve("musubi", "CLAUDE.md") == (
        musubi / "CLAUDE.md"
    ).resolve()


def test_registry_rejects_unknown_absolute_traversal_duplicate_and_nested(
    tmp_path: Path,
) -> None:
    musubi = tmp_path / "musubi"
    external = tmp_path / "external"
    nested = external / "nested"
    for path in (musubi, external, nested):
        path.mkdir()

    registry = RootRegistry.build(
        musubi,
        [FolderGrant("g-ext", "external", external)],
    )
    with pytest.raises(PermissionError, match="unknown root"):
        registry.resolve("missing", "file.txt")
    with pytest.raises(PermissionError, match="relative"):
        registry.resolve("external", str((external / "file.txt").resolve()))
    with pytest.raises(PermissionError, match="escapes"):
        registry.resolve("external", "../outside.txt")

    with pytest.raises(ValueError, match="duplicate alias"):
        RootRegistry.build(
            musubi,
            [
                FolderGrant("g-1", "same", external),
                FolderGrant("g-2", "same", tmp_path),
            ],
        )
    with pytest.raises(ValueError, match="overlaps"):
        RootRegistry.build(
            musubi,
            [
                FolderGrant("g-1", "external", external),
                FolderGrant("g-2", "nested", nested),
            ],
        )


def test_registry_json_round_trip_is_bounded_and_fail_closed(tmp_path: Path) -> None:
    musubi = tmp_path / "musubi"
    musubi.mkdir()
    externals: list[FolderGrant] = []
    for index in range(16):
        root = tmp_path / f"root-{index}"
        root.mkdir()
        externals.append(FolderGrant(f"g-{index}", f"root-{index}", root))
    registry = RootRegistry.build(musubi, externals)

    restored = RootRegistry.from_json(registry.to_json(), musubi)
    assert [grant.alias for grant in restored.grants] == [
        "musubi",
        *[f"root-{index}" for index in range(16)],
    ]

    overflow = json.loads(registry.to_json())
    overflow.append(
        {
            "grantId": "overflow",
            "alias": "overflow",
            "canonicalPath": str(tmp_path),
        }
    )
    with pytest.raises(ValueError, match="at most 16"):
        RootRegistry.from_json(json.dumps(overflow), musubi)
    with pytest.raises(ValueError, match="invalid folder-grant manifest"):
        RootRegistry.from_json("{", musubi)


def test_registry_rejects_missing_and_duplicate_grant_ids(tmp_path: Path) -> None:
    musubi = tmp_path / "musubi"
    first = tmp_path / "first"
    second = tmp_path / "second"
    musubi.mkdir()
    first.mkdir()
    second.mkdir()

    with pytest.raises(ValueError, match="ID must be non-empty"):
        RootRegistry.build(musubi, [FolderGrant("", "first", first)])
    with pytest.raises(ValueError, match="duplicate folder grant ID"):
        RootRegistry.build(
            musubi,
            [
                FolderGrant("same", "first", first),
                FolderGrant("same", "second", second),
            ],
        )


def test_registry_rejects_a_root_deleted_after_snapshot(tmp_path: Path) -> None:
    musubi = tmp_path / "musubi"
    external = tmp_path / "external"
    musubi.mkdir()
    external.mkdir()
    registry = RootRegistry.build(
        musubi,
        [FolderGrant("g-external", "external", external)],
    )
    external.rmdir()

    with pytest.raises(PermissionError, match="unavailable"):
        registry.resolve("external", "new.txt")
    assert not external.exists()


def test_registry_rejects_root_rebound_to_symlink(tmp_path: Path) -> None:
    musubi = tmp_path / "musubi"
    external = tmp_path / "external"
    outside = tmp_path / "outside"
    musubi.mkdir()
    external.mkdir()
    outside.mkdir()
    registry = RootRegistry.build(
        musubi,
        [FolderGrant("g-external", "external", external)],
    )
    external.rmdir()
    try:
        external.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(PermissionError, match="captured path"):
        registry.resolve("external", "secret.txt")


def test_registry_detects_rebound_root_without_reresolving_snapshot_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    musubi = tmp_path / "musubi"
    external = tmp_path / "external"
    outside = tmp_path / "outside"
    musubi.mkdir()
    external.mkdir()
    outside.mkdir()
    registry = RootRegistry.build(
        musubi,
        [FolderGrant("g-external", "external", external)],
    )
    original_resolve = Path.resolve

    def rebound_resolve(path: Path, *args, **kwargs) -> Path:
        if path == external:
            return outside
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", rebound_resolve)
    with pytest.raises(PermissionError, match="captured path"):
        registry.resolve("external", "secret.txt")


def test_manifest_rejects_root_rebound_before_registry_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    musubi = tmp_path / "musubi"
    external = tmp_path / "external"
    outside = tmp_path / "outside"
    musubi.mkdir()
    external.mkdir()
    outside.mkdir()
    raw = RootRegistry.build(
        musubi,
        [FolderGrant("g-external", "external", external)],
    ).to_json()
    original_resolve = Path.resolve

    def rebound_resolve(path: Path, *args, **kwargs) -> Path:
        if path == external:
            return outside
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", rebound_resolve)
    with pytest.raises(ValueError, match="no longer matches"):
        RootRegistry.from_json(raw, musubi)


def test_derive_alias_is_stable_and_avoids_reserved_or_used_names() -> None:
    assert derive_alias(Path("D:/work/web-app"), set()) == "web-app"
    assert derive_alias(Path("D:/work/Musubi"), set()) == "musubi-2"
    assert derive_alias(Path("D:/work/API"), {"api"}) == "api-2"
