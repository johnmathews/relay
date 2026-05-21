"""Env-driven configuration.

Every key maps to a ``RELAY_*`` environment variable per spec.md §11.
``Settings`` is a pydantic-settings model so types are validated and the
env-var contract is the single source of truth. Init kwargs take priority
over the environment, which keeps tests hermetic (pass an explicit
``data_dir=tmp_path``).
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Field name ``foo`` ⇄ env var ``RELAY_FOO``."""

    model_config = SettingsConfigDict(env_prefix="RELAY_", env_file=None)

    # Per-project data directory. Default: <cwd>/.relay (spec.md §3.3, §11).
    data_dir: Path = Field(default_factory=lambda: Path.cwd() / ".relay")

    pi_bin: str = "pi"
    pi_model: str = "claude-sonnet-4-6"
    pi_provider: str = "anthropic"
    # Known-good pi pin (OQ-5). Mirrors `.tool-versions`; a mismatch is
    # logged (non-fatal) at first spawn, never enforced.
    pi_expected_version: str = "0.74.0"

    max_iters: int = 12
    iter_timeout: int = 1800  # seconds

    # Fanout/join (Phase 9, ADR-35).
    # max_fanout_depth: maximum parent→child recursion depth.
    #   Default 2, hard cap 4 (proposal §recursion-bounds).
    # max_fanout_concurrent: semaphore pool size for concurrent child-run
    #   tasks across all active parents (Option A, ADR-35).
    max_fanout_depth: int = 2
    max_fanout_concurrent: int = 4

    otel_export: str = "none"  # "langfuse" | "none"
    langfuse_host: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None

    host: str = "127.0.0.1"
    port: int = 7800

    @property
    def db_path(self) -> Path:
        """Absolute path to the SQLite event store."""
        return self.data_dir / "relay.db"

    @property
    def db_url(self) -> str:
        """Sync SQLAlchemy URL — schema bootstrap only (ADR-17)."""
        return f"sqlite:///{self.db_path}"

    @property
    def async_db_url(self) -> str:
        """Async SQLAlchemy URL — orchestrator runtime I/O (ADR-21).

        The sync engine creates the schema once at startup; every
        orchestrator-driven read/write goes through this async engine so
        DB work never blocks the event loop.
        """
        return f"sqlite+aiosqlite:///{self.db_path}"


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings, read from the environment once."""
    return Settings()
