"""Immutable, session-request-scoped filesystem root registry.

musubi-tier: substrate
expires-when: never
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MAX_EXTERNAL_GRANTS = 16
MANIFEST_ENV = "MUSUBI_FOLDER_GRANTS_JSON"
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class FolderGrant:
    grant_id: str
    alias: str
    path: Path


def _path_key(path: Path) -> str:
    text = os.path.abspath(os.path.normpath(str(path)))
    return os.path.normcase(text) if os.name == "nt" else text


def _contains(parent: Path, child: Path) -> bool:
    parent_key = _path_key(parent)
    child_key = _path_key(child)
    try:
        return os.path.commonpath((parent_key, child_key)) == parent_key
    except ValueError:
        return False


def _normalize_alias(raw: str) -> str:
    alias = raw.strip().lower()
    if not _ALIAS_RE.fullmatch(alias):
        raise ValueError(
            f"invalid root alias {raw!r}; expected [a-z][a-z0-9_-]{{0,31}}"
        )
    return alias


def derive_alias(path: Path, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_-]+", "-", path.name.lower()).strip("-_")
    if not base or not base[0].isalpha():
        base = f"folder-{base}".rstrip("-")
    base = base[:32]
    candidate = base
    suffix = 2
    reserved = {"musubi", *{item.lower() for item in used}}
    while candidate in reserved:
        tail = f"-{suffix}"
        candidate = f"{base[: 32 - len(tail)]}{tail}"
        suffix += 1
    return candidate


@dataclass(frozen=True)
class RootRegistry:
    grants: tuple[FolderGrant, ...]

    @classmethod
    def build(
        cls,
        musubi_root: Path,
        external: Iterable[FolderGrant] = (),
        *,
        _paths_are_canonical: bool = False,
    ) -> "RootRegistry":
        root = musubi_root if _paths_are_canonical else musubi_root.resolve()
        if not root.is_dir():
            raise ValueError(f"Musubi root is not an existing directory: {root}")
        if _paths_are_canonical and (
            not root.is_absolute() or _path_key(root.resolve()) != _path_key(root)
        ):
            raise ValueError("Musubi root no longer matches its snapshot")
        external_list = list(external)
        if len(external_list) > MAX_EXTERNAL_GRANTS:
            raise ValueError(
                f"a request may attach at most {MAX_EXTERNAL_GRANTS} folders"
            )

        grants = [FolderGrant("musubi", "musubi", root)]
        grant_ids = {"musubi"}
        aliases = {"musubi"}
        paths = {_path_key(root)}
        for raw in external_list:
            grant_id = raw.grant_id.strip()
            if not grant_id:
                raise ValueError("folder grant ID must be non-empty")
            if grant_id in grant_ids:
                raise ValueError(f"duplicate folder grant ID: {grant_id}")
            alias = _normalize_alias(raw.alias)
            if alias in aliases:
                raise ValueError(f"duplicate alias: {alias}")
            path = raw.path if _paths_are_canonical else raw.path.resolve()
            if not path.is_dir():
                raise ValueError(f"folder grant is not an existing directory: {path}")
            if _paths_are_canonical and (
                not path.is_absolute() or _path_key(path.resolve()) != _path_key(path)
            ):
                raise ValueError(
                    f"folder grant no longer matches its snapshot: {path}"
                )
            key = _path_key(path)
            if key in paths:
                raise ValueError(f"duplicate folder grant: {path}")
            for existing in grants:
                if _contains(existing.path, path) or _contains(path, existing.path):
                    raise ValueError(
                        f"folder grant overlaps another grant: "
                        f"{existing.path} and {path}"
                    )
            aliases.add(alias)
            grant_ids.add(grant_id)
            paths.add(key)
            grants.append(FolderGrant(grant_id, alias, path))
        return cls(tuple(grants))

    @classmethod
    def from_json(cls, raw: str, musubi_root: Path) -> "RootRegistry":
        try:
            payload = json.loads(raw)
            if not isinstance(payload, list):
                raise TypeError("manifest must be a list")
            external: list[FolderGrant] = []
            saw_musubi = False
            for item in payload:
                if not isinstance(item, dict):
                    raise TypeError("manifest entries must be objects")
                grant_id = str(item["grantId"])
                alias = str(item["alias"])
                path = Path(str(item["canonicalPath"]))
                if alias == "musubi":
                    if grant_id != "musubi" or _path_key(path) != _path_key(musubi_root):
                        raise ValueError("manifest Musubi root does not match MUSUBI_ROOT")
                    saw_musubi = True
                else:
                    if not path.is_absolute():
                        raise ValueError(
                            f"manifest root {alias!r} is not an absolute snapshot"
                        )
                    if not path.is_dir() or _path_key(path.resolve()) != _path_key(path):
                        raise ValueError(
                            f"manifest root {alias!r} no longer matches its snapshot"
                        )
                    external.append(FolderGrant(grant_id, alias, path))
            if not saw_musubi:
                raise ValueError("manifest is missing the Musubi root")
            return cls.build(
                musubi_root,
                external,
                _paths_are_canonical=True,
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid folder-grant manifest: {exc}") from exc

    def to_json(self) -> str:
        return json.dumps(
            [
                {
                    "grantId": grant.grant_id,
                    "alias": grant.alias,
                    "canonicalPath": str(grant.path),
                }
                for grant in self.grants
            ],
            separators=(",", ":"),
        )

    def root(self, alias: str = "musubi") -> FolderGrant:
        normalized = alias.strip().lower()
        for grant in self.grants:
            if grant.alias == normalized:
                return grant
        raise PermissionError(f"unknown root {alias!r}")

    def resolve(self, alias: str, relative_path: str) -> Path:
        if not relative_path:
            raise ValueError("path must be a non-empty string")
        path = Path(relative_path)
        if path.is_absolute() or path.anchor:
            raise PermissionError("path must be relative to the selected root")
        grant = self.root(alias)
        if not grant.path.is_dir():
            raise PermissionError(
                f"root {grant.alias!r} is unavailable: {grant.path}"
            )
        if _path_key(grant.path.resolve()) != _path_key(grant.path):
            raise PermissionError(
                f"root {grant.alias!r} no longer resolves to its captured path"
            )
        target = (grant.path / path).resolve()
        if not _contains(grant.path, target):
            raise PermissionError(
                f"path {relative_path!r} escapes root {grant.alias!r}"
            )
        return target

    def prompt_block(self) -> str:
        lines = ["Available roots:"]
        for grant in self.grants:
            suffix = " (fixed harness root)" if grant.alias == "musubi" else ""
            lines.append(f"- {grant.alias}{suffix}: {grant.path}")
        lines.extend(
            (
                "",
                "Use the root argument for every operation outside musubi.",
                "Paths must be relative to the selected root.",
            )
        )
        return "\n".join(lines)
