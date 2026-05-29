"""SQLAlchemy models — a faithful port of spec.md §3.1.

spec.md §3.1 is canonical for the schema. Where the spec's DDL has a
``REFERENCES`` clause, this module declares a ``ForeignKey``; where it
only annotates a column as a reserved FK (``user_id``), it stays a plain
column with a default, exactly as the spec's SQL is written. JSON columns
use SQLAlchemy's portable ``JSON`` type so the Postgres migration path
(ADR-11) stays clean. No ``CHECK`` constraints are added beyond what the
spec's DDL specifies — ``status`` / ``kind`` enumerations live in column
comments there and are enforced at the service layer in later phases.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all relay v2 tables."""


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    root_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.current_timestamp()
    )
    # FK reserved for multi-user (ADR-12) — plain column per spec DDL.
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str | None] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False, default="me")


class Prompt(Base):
    __tablename__ = "prompts"
    __table_args__ = (UniqueConstraint("project_id", "name", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.current_timestamp()
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (Index("idx_runs_project", "project_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    prompt_id: Mapped[int | None] = mapped_column(ForeignKey("prompts.id"))
    prompt_body: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.current_timestamp()
    )
    ended_at: Mapped[datetime | None] = mapped_column()
    max_iters: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    iter_timeout: Mapped[int] = mapped_column(Integer, nullable=False, default=1800)
    worktree_path: Mapped[str | None] = mapped_column(Text)
    branch: Mapped[str | None] = mapped_column(Text)
    parent_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))


class Iter(Base):
    __tablename__ = "iters"
    __table_args__ = (
        UniqueConstraint("run_id", "seq"),
        Index("idx_iters_run", "run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str | None] = mapped_column(Text)
    pi_session_id: Mapped[str | None] = mapped_column(Text)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    preamble: Mapped[str] = mapped_column(Text, nullable=False)
    signal_kind: Mapped[str | None] = mapped_column(Text)
    signal_args: Mapped[dict[str, object] | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.current_timestamp()
    )
    ended_at: Mapped[datetime | None] = mapped_column()
    exit_reason: Mapped[str | None] = mapped_column(Text)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq"),
        Index("idx_events_run_seq", "run_id", "seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    iter_id: Mapped[int | None] = mapped_column(ForeignKey("iters.id"))
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.current_timestamp()
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
