"""Shared fixtures for harness tests.

The pi event fixtures under ``scratch/pi_derisk_workdir/`` are committed
de-risking evidence (CLAUDE.md: ground truth). Harness unit tests run
fully offline against them — no pi invocation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "scratch" / "pi_derisk_workdir"


def load_jsonl(name: str) -> list[dict[str, Any]]:
    path = FIXTURE_DIR / name
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.fixture
def pi_fixtures_dir() -> Path:
    return FIXTURE_DIR
