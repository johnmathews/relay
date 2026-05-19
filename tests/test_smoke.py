"""Phase 0 smoke tests — the verification criteria from docs/plan.md.

Covers: app boots and ``/health`` returns 200; first serve materialises
``.relay/relay.db`` with every spec.md §3.1 table; the version constant
and ``relay --version`` agree.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from relay_v2 import __version__
from relay_v2.app import create_app
from relay_v2.config import Settings
from relay_v2.db import make_engine

SPEC_TABLES = {"projects", "users", "prompts", "runs", "iters", "events"}


@pytest.fixture
def settings(tmp_path: object) -> Settings:
    # Hermetic: explicit data_dir wins over any RELAY_DATA_DIR in the env.
    return Settings(data_dir=tmp_path / ".relay")  # type: ignore[operator]


def test_health_returns_ok(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_first_serve_creates_db_with_schema(settings: Settings) -> None:
    assert not settings.db_path.exists()
    with TestClient(create_app(settings)) as client:
        client.get("/health")
    assert settings.db_path.exists()

    tables = set(inspect(make_engine(settings.db_url)).get_table_names())
    assert SPEC_TABLES <= tables


def test_version_constant_is_semver_ish() -> None:
    assert __version__.count(".") >= 2


def test_cli_version_matches_constant(capsys: pytest.CaptureFixture[str]) -> None:
    from relay_v2.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out
