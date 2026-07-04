"""CLI output must not crash on non-ASCII answers under a legacy code page.

Windows consoles default to cp1252, which cannot encode emoji the model may
emit; `agent.run.main` forces UTF-8 on stdout/stderr so `print(answer)` never
raises `UnicodeEncodeError`.
"""

from __future__ import annotations

import io

import pytest

from agent import run


class _Cp1252Stream:
    """A text stream that behaves like a real Windows console: it starts on
    cp1252 (rejecting emoji) and switches encoding when reconfigured."""

    def __init__(self) -> None:
        self._buf = io.StringIO()
        self.encoding = "cp1252"
        self.errors = "strict"

    def reconfigure(self, *, encoding: str = "", errors: str = "") -> None:
        if encoding:
            self.encoding = encoding
        if errors:
            self.errors = errors

    def write(self, s: str) -> int:
        # Mimic the console: encoding to the active code page must succeed.
        s.encode(self.encoding, self.errors)
        return self._buf.write(s)

    def flush(self) -> None:  # print() calls flush on the stream
        self._buf.flush()

    def getvalue(self) -> str:
        return self._buf.getvalue()


def test_main_reconfigures_streams_to_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    out, err = _Cp1252Stream(), _Cp1252Stream()
    monkeypatch.setattr(run.sys, "stdout", out)
    monkeypatch.setattr(run.sys, "stderr", err)

    run._force_utf8_streams()

    assert out.encoding == "utf-8"
    assert out.errors == "replace"
    assert err.encoding == "utf-8"
    assert err.errors == "replace"


def test_emoji_print_crashes_before_fix_and_works_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = _Cp1252Stream()
    monkeypatch.setattr(run.sys, "stdout", out)
    monkeypatch.setattr(run.sys, "stderr", _Cp1252Stream())

    answer = "hello \U0001f44b"  # the exact character from the Windows crash

    # Reproduce the original crash: cp1252 cannot encode the emoji.
    with pytest.raises(UnicodeEncodeError):
        print(answer, file=run.sys.stdout)

    # After the fix, the same print succeeds and the emoji round-trips.
    run._force_utf8_streams()
    print(answer, file=run.sys.stdout)
    assert "\U0001f44b" in out.getvalue()


def test_force_utf8_tolerates_streams_without_reconfigure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run.sys, "stdout", io.BytesIO())
    monkeypatch.setattr(run.sys, "stderr", io.BytesIO())

    # Must not raise even though BytesIO has no `reconfigure`.
    run._force_utf8_streams()
