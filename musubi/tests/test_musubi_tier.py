from pathlib import Path

from scripts.check_musubi_tier import check_file


def test_substrate_lifecycle_must_be_never(tmp_path: Path) -> None:
    source = tmp_path / "component.py"
    source.write_text(
        '"""musubi-tier: substrate\nexpires-when: next quarter\n"""\n',
        encoding="utf-8",
    )
    assert check_file(source) == [
        f"{source}: substrate file must declare `expires-when: never`"
    ]


def test_ephemeral_lifecycle_requires_trigger_and_cost(tmp_path: Path) -> None:
    source = tmp_path / "adapter.py"
    source.write_text(
        '"""musubi-tier: ephemeral\nexpires-when: evidence threshold passes\n"""\n',
        encoding="utf-8",
    )
    assert check_file(source) == [
        f"{source}: ephemeral file missing `cost-lever:`"
    ]
