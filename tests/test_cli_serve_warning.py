"""W6: ``relay serve`` warns on stderr when RELAY_HOST is non-localhost.

The check belongs to the CLI dispatch (the "production-ish startup"
entry point), not to ``create_app`` — API tests construct the app
directly and may set arbitrary host values. The warning is consistent
with the existing pi-version-mismatch precedent (``harness/pi.py``:
warn, do not refuse).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from relay_v2 import __main__ as cli
from relay_v2.config import get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """``get_settings`` is ``@lru_cache``d at module scope. Clear it
    around every test so neither this file nor others observe stale
    env-driven Settings."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _run_serve(monkeypatch: pytest.MonkeyPatch, host: str) -> int:
    monkeypatch.setenv("RELAY_HOST", host)
    monkeypatch.setattr(
        "uvicorn.run",
        lambda *args, **kwargs: None,
    )
    return cli.main(["serve"])


def test_localhost_default_emits_no_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = _run_serve(monkeypatch, "127.0.0.1")
    assert rc == 0
    captured = capsys.readouterr()
    assert "WARNING" not in captured.err


def test_localhost_alias_emits_no_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = _run_serve(monkeypatch, "localhost")
    assert rc == 0
    assert "WARNING" not in capsys.readouterr().err


def test_zero_zero_zero_zero_emits_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = _run_serve(monkeypatch, "0.0.0.0")
    assert rc == 0
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "RELAY_HOST=0.0.0.0" in captured.err
    assert "/api/system/browse" in captured.err


def test_lan_address_emits_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = _run_serve(monkeypatch, "192.168.1.5")
    assert rc == 0
    assert "WARNING" in capsys.readouterr().err
