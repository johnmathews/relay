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

from relay.harness.skills import bundled_skill_dir


def _default_pi_skill_paths() -> list[Path]:
    """The single bundled engineering-team skill, by default.

    A missing bundle (broken install) degrades silently to an empty list
    so that misconfiguration in test/dev harnesses doesn't crash startup;
    the agent will then exit with `agent_end_no_signal` and the dashboard
    failure banner explains it.
    """
    try:
        return [bundled_skill_dir()]
    except FileNotFoundError:
        return []


class Settings(BaseSettings):
    """Runtime configuration. Field name ``foo`` ⇄ env var ``RELAY_FOO``."""

    model_config = SettingsConfigDict(env_prefix="RELAY_", env_file=None)

    # Per-project data directory. Default: <cwd>/.relay (spec.md §3.3, §11).
    data_dir: Path = Field(default_factory=lambda: Path.cwd() / ".relay")

    pi_bin: str = "pi"
    pi_model: str = "claude-opus-4-7"
    pi_provider: str = "anthropic"
    # Colon-separated (POSIX path-list) directories or `SKILL.md` files
    # passed to pi via `--skill` on every spawn (pi accepts the flag
    # repeatedly). The env var name is ``RELAY_PI_SKILLS``. The empty
    # string disables explicit injection entirely (the agent then only
    # sees pi's auto-discovered skills under `<cwd>/.pi/skills/` and
    # `~/.pi/agent/skills/`, which stay on by default — explicit injection
    # is additive, not exclusive). Unset env → the bundled engineering-team
    # skill is injected via ``pi_skill_paths`` (see :attr:`pi_skill_paths`).
    pi_skills: str | None = None
    # Known-good pi pin (OQ-5). Mirrors `.tool-versions`; a mismatch is
    # logged (non-fatal) at first spawn, never enforced.
    pi_expected_version: str = "0.74.0"
    # asyncio StreamReader buffer size for pi's stdout. Pi emits one JSON
    # object per line; large tool results (file reads, verbose Bash,
    # agent_end.messages with long content) can exceed asyncio's 64 KiB
    # default and crash the read with LimitOverrunError. 8 MiB is generous
    # and bounded; raise via RELAY_PI_STDOUT_LIMIT if needed.
    pi_stdout_limit: int = 8 * 1024 * 1024

    max_iters: int = 12
    iter_timeout: int = 1800  # seconds

    # Chat mode (W1, ADR-NN). Safety cap on a chat's turn count; high
    # enough that no real chat hits it. Each user turn = one iter; chat
    # runs use pause-for-input between turns (ADR-40), so this caps the
    # whole conversation length, not a single sub-loop.
    chat_max_iters: int = 200

    # Fanout/join (Phase 9, ADR-35).
    # max_fanout_depth: maximum parent→child recursion depth.
    #   Default 2, hard cap 4 (proposal §recursion-bounds).
    # max_fanout_concurrent: semaphore pool size for concurrent child-run
    #   tasks across all active parents (Option A, ADR-35).
    max_fanout_depth: int = 2
    max_fanout_concurrent: int = 4
    # max_fanout_width: maximum FanoutPayload.children list length per
    #   closing fanout sentinel (soft, operator-tunable cap). Default 8;
    #   the hard cap (32) is enforced inside FanoutPayload's validator
    #   so a malformed agent emission is rejected at parse time
    #   regardless of config. Env var RELAY_MAX_FANOUT_WIDTH.
    max_fanout_width: int = 8

    otel_export: str = "none"  # "langfuse" | "none"
    langfuse_host: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None

    host: str = "127.0.0.1"
    port: int = 7800

    @property
    def pi_skill_paths(self) -> list[Path]:
        """Resolved list of skill paths passed to pi.

        - Env unset (``pi_skills is None``) → the bundled engineering-team
          skill (or empty list if the bundle isn't shipped — see
          :func:`_default_pi_skill_paths`).
        - Env set to an empty string → empty list (explicit opt-out;
          rely on pi's auto-discovery only).
        - Env set to a colon-separated path list → those paths verbatim
          (no merging with the default — explicit overrides bundled).
        """
        if self.pi_skills is None:
            return _default_pi_skill_paths()
        return [Path(p) for p in self.pi_skills.split(":") if p]

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
