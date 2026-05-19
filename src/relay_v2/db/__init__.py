"""Database bootstrap.

Phase 0 needs exactly one thing from the DB layer: the schema exists on
first ``relay serve``. Schema management is hand-rolled ``create_all``
for the MVP (ADR-17); ``db/migrations/`` holds future versioned scripts.
A synchronous engine is intentional here — Phase 0 only creates the
schema once at startup; the async story arrives with the orchestrator
(Phase 2) and is encapsulated behind this module.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine

from relay_v2.config import Settings
from relay_v2.db.models import Base

__all__ = ["Base", "make_engine", "init_db"]


def make_engine(db_url: str) -> Engine:
    """Create a SQLite engine usable from FastAPI's worker threads."""
    return create_engine(db_url, connect_args={"check_same_thread": False})


def init_db(settings: Settings) -> Engine:
    """Ensure the data dir and the SQLite schema exist; return the engine.

    Idempotent: ``create_all`` only creates missing tables, so repeated
    ``relay serve`` invocations are safe.
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.db_url)
    Base.metadata.create_all(engine)
    return engine
