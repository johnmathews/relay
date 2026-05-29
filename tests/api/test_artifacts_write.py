"""Integration tests for ``PUT /api/runs/:id/artifacts/{path}`` (14a).

Exercises :meth:`RelayCore.write_artifact` end-to-end against a real
``aiosqlite`` DB and the FastAPI route adapter. The
``test_artifacts.py`` module covers the read-side GET endpoints with
a stub core; this module is intentionally heavier — the precondition
checks (``not_paused`` / ``no_review_path`` / ``path_mismatch``) and
the event-store append are precisely what 14a contracts in, so the
strong preference is to verify them through the real service path.

14a does NOT yet parse a ``review_path`` attribute from the pause
sentinel — that's 14b. These tests therefore seed a paused Run + Iter
row directly via the sessionmaker (with ``signal_args`` carrying a
synthetic ``review_path``) to exercise the post-14b world. The 14a
production code path will return 409 ``no_review_path`` until 14b
lands; that is the documented interim behaviour (ADR-40).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from relay.api.files import MAX_FILE_BYTES
from relay.app import create_app
from relay.config import Settings
from relay.core import RelayCore
from relay.db.models import Event
from relay.db.models import Iter as IterModel
from relay.db.models import Run as RunModel
from relay.harness.protocol import Harness
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript

DONE_BLOCK = "All work complete.\n\n[[engteam:done]]"


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / ".relay")


@asynccontextmanager
async def _client_with_core(
    settings: Settings,
    harness: Harness | None = None,
) -> AsyncIterator[tuple[AsyncClient, RelayCore]]:
    """Mirror of ``tests/api/test_runs.py::_client_with_core``.

    The default scripted harness is never actually driven here — every
    test in this module seeds rows directly via the sessionmaker — but
    ``create_app`` requires *some* harness to wire ``RelayCore``.
    """
    if harness is None:
        harness = ScriptedHarness([TextScript(DONE_BLOCK)])
    app = create_app(settings, harness=harness)
    async with app.router.lifespan_context(app):
        core: RelayCore = app.state.core
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            yield ac, core


async def _seed_run(
    core: RelayCore,
    project_id: int,
    *,
    status: str,
    review_path: str | None = None,
    review_paths: list[str] | None = None,
) -> tuple[str, int | None]:
    """Insert a Run row and (when at least one of ``review_path`` /
    ``review_paths`` is set) a paired paused Iter row directly. Returns
    ``(run_id, iter_id)``.

    ``review_path`` (singular) seeds the legacy 14a–14d ``signal_args``
    key — exercises the 14f migration-fallback read path. ``review_paths``
    (plural) seeds the post-14f primary key.
    """
    run_id = core._new_run_id()  # noqa: SLF001 — test-only helper
    iter_id: int | None = None
    async with core._sm() as s:  # noqa: SLF001 — test-only helper
        s.add(
            RunModel(
                id=run_id,
                project_id=project_id,
                prompt_body="seeded",
                status=status,
                max_iters=1,
                iter_timeout=60,
            )
        )
        await s.flush()
        signal_args: dict[str, object] = {
            "next_prompt": "Proceed.",
            "question": "Approve?",
            "id": "P1",
        }
        if review_paths is not None:
            signal_args["review_paths"] = review_paths
        elif review_path is not None:
            signal_args["review_path"] = review_path
        # Only attach a paused iter when the test wants one (the
        # "no_review_path" case omits it entirely to exercise the
        # ``paused is None`` branch).
        if status == "paused":
            it = IterModel(
                run_id=run_id,
                seq=1,
                prompt="seeded",
                preamble="",
                signal_kind="pause",
                signal_args=signal_args,
                exit_reason="signal",
            )
            s.add(it)
            await s.commit()
            await s.refresh(it)
            iter_id = it.id
        else:
            await s.commit()
    return run_id, iter_id


async def _register_project(ac: AsyncClient, root: Path) -> int:
    r = await ac.post(
        "/api/projects", json={"root_path": str(root), "name": "p"}
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def _make_artifacts_dir(project_root: Path, run_id: str) -> Path:
    """Materialise ``<project_root>/.relay/runs/<run_id>/``."""
    d = project_root / ".relay" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Happy paths ────────────────────────────────────────────────────────


def test_put_artifact_edit_existing(tmp_path: Path) -> None:
    """Seed a paused run + an existing ``plan.md``; PUT new content;
    the response carries the new sha256, the file matches, and an
    ``artifact_edited`` event lands against the paused iter with both
    hashes populated (before != after)."""
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id, paused_iter_id = await _seed_run(
                core, project_id, status="paused", review_path="plan.md"
            )
            artifacts = _make_artifacts_dir(proj_root, run_id)
            (artifacts / "plan.md").write_text("# Original\n")

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/plan.md",
                json={"content": "# Edited\n"},
            )
            assert r.status_code == 200, r.text
            body_json = r.json()
            assert body_json["path"] == "plan.md"
            assert body_json["size"] == len(b"# Edited\n")
            assert isinstance(body_json["sha256"], str)
            assert len(body_json["sha256"]) == 64

            assert (artifacts / "plan.md").read_text() == "# Edited\n"

            # Event lands iter-scoped to the paused iter, both hashes set.
            async with core._sm() as sess:  # noqa: SLF001 — test-only
                rows = list(
                    await sess.scalars(
                        select(Event)
                        .where(Event.run_id == run_id,
                               Event.kind == "artifact_edited")
                    )
                )
            assert len(rows) == 1
            ev = rows[0]
            assert ev.iter_id == paused_iter_id
            assert ev.payload["path"] == "plan.md"
            assert ev.payload["size_before"] == len("# Original\n")
            assert ev.payload["size_after"] == len("# Edited\n")
            assert ev.payload["sha256_before"] is not None
            assert ev.payload["sha256_after"] == body_json["sha256"]
            assert ev.payload["sha256_before"] != ev.payload["sha256_after"]
            assert ev.payload["editor"] == "dashboard"

    asyncio.run(body())


def test_put_artifact_creates_file(tmp_path: Path) -> None:
    """File absent on disk → PUT creates it; ``sha256_before`` is null
    and ``size_before`` is 0 in the event payload."""
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id, _ = await _seed_run(
                core, project_id, status="paused", review_path="plan.md"
            )
            artifacts = _make_artifacts_dir(proj_root, run_id)
            assert not (artifacts / "plan.md").exists()

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/plan.md",
                json={"content": "# Fresh\n"},
            )
            assert r.status_code == 200, r.text
            assert (artifacts / "plan.md").read_text() == "# Fresh\n"

            async with core._sm() as sess:  # noqa: SLF001
                ev = list(
                    await sess.scalars(
                        select(Event).where(
                            Event.run_id == run_id,
                            Event.kind == "artifact_edited",
                        )
                    )
                )[0]
            assert ev.payload["size_before"] == 0
            assert ev.payload["sha256_before"] is None
            assert ev.payload["size_after"] == len("# Fresh\n")
            assert ev.payload["sha256_after"] is not None

    asyncio.run(body())


def test_put_accepts_normalised_review_path(tmp_path: Path) -> None:
    """``signal_args.review_path = "./plan.md"`` and PUT to ``plan.md``
    → 200. Equality is normalised, not byte-string-equal."""
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id, _ = await _seed_run(
                core,
                project_id,
                status="paused",
                review_path="./plan.md",
            )
            _make_artifacts_dir(proj_root, run_id)

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/plan.md",
                json={"content": "x"},
            )
            assert r.status_code == 200, r.text

    asyncio.run(body())


# ── 409 — precondition failures ────────────────────────────────────────


def test_put_409_not_paused(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id, _ = await _seed_run(
                core, project_id, status="running", review_path=None
            )
            _make_artifacts_dir(proj_root, run_id)

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/plan.md",
                json={"content": "x"},
            )
            assert r.status_code == 409, r.text
            assert "not paused" in r.json()["detail"]

    asyncio.run(body())


def test_put_409_no_review_path(tmp_path: Path) -> None:
    """A paused iter whose ``signal_args`` carries no ``review_path``
    rejects every write with 409 — the 14a interim state."""
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id, _ = await _seed_run(
                core, project_id, status="paused", review_path=None
            )
            _make_artifacts_dir(proj_root, run_id)

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/plan.md",
                json={"content": "x"},
            )
            assert r.status_code == 409, r.text
            assert "review_path" in r.json()["detail"]

    asyncio.run(body())


def test_put_409_path_mismatch(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id, _ = await _seed_run(
                core, project_id, status="paused", review_path="plan.md"
            )
            _make_artifacts_dir(proj_root, run_id)

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/evil.md",
                json={"content": "x"},
            )
            assert r.status_code == 409, r.text
            assert "review_path" in r.json()["detail"]

    asyncio.run(body())


def test_put_409_missing_parent_dir_then_succeeds(tmp_path: Path) -> None:
    """Nested ``review_path`` whose parent directory does not yet exist
    → 409. Creating the directory flips the gate to 200."""
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id, _ = await _seed_run(
                core,
                project_id,
                status="paused",
                review_path="discussions/notes.md",
            )
            artifacts = _make_artifacts_dir(proj_root, run_id)

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/discussions/notes.md",
                json={"content": "x"},
            )
            assert r.status_code == 409, r.text
            assert "parent" in r.json()["detail"].lower()

            (artifacts / "discussions").mkdir()
            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/discussions/notes.md",
                json={"content": "x"},
            )
            assert r.status_code == 200, r.text
            assert (artifacts / "discussions" / "notes.md").read_text() == "x"

    asyncio.run(body())


# ── 404 — unknown run ──────────────────────────────────────────────────


def test_put_404_unknown_run(tmp_path: Path) -> None:
    s = _settings(tmp_path)

    async def body() -> None:
        async with _client_with_core(s) as (ac, _core):
            r = await ac.put(
                "/api/runs/nope-no-such-run/artifacts/plan.md",
                json={"content": "x"},
            )
            assert r.status_code == 404, r.text

    asyncio.run(body())


# ── 400 — sandbox violations ──────────────────────────────────────────


def test_put_400_traversal(tmp_path: Path) -> None:
    """URL-encoded ``..`` in the path segment must reject — never
    returns 200 (which would be a successful traversal). Mirrors the
    pattern of ``test_files.py::test_route_url_encoded_traversal_400``:
    httpx sends the raw bytes, Starlette decodes before the handler,
    and the sandbox resolver rejects with 400. (Plain ``..`` in the
    URL is collapsed client-side and would route to a different path,
    so it is not a useful test of *this* code path.)"""
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            # Seed review_path to match the decoded request path so the
            # equality check passes and the sandbox check is reached.
            run_id, _ = await _seed_run(
                core,
                project_id,
                status="paused",
                review_path="../escape.md",
            )
            _make_artifacts_dir(proj_root, run_id)

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/%2e%2e%2Fescape.md",
                json={"content": "x"},
            )
            # 400 from the sandbox resolver; 404 if Starlette normalises
            # the path away before routing. Crucially never 200.
            assert r.status_code in (400, 404), r.text
            assert r.status_code != 200

    asyncio.run(body())


def test_put_400_absolute_path(tmp_path: Path) -> None:
    """Encoded absolute path → sandbox rejects with 400."""
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            # Starlette decodes %2F to "/" inside the path-capture, so
            # the handler receives "/etc/passwd" (with the leading
            # slash). Seed review_path to match exactly so the
            # equality check passes and the sandbox check is reached.
            run_id, _ = await _seed_run(
                core,
                project_id,
                status="paused",
                review_path="/etc/passwd",
            )
            _make_artifacts_dir(proj_root, run_id)

            # Use the same %2e%2e form as the files.py test, but for
            # absolute: encode the leading slash + the segments.
            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/%2Fetc%2Fpasswd",
                json={"content": "x"},
            )
            assert r.status_code in (400, 404), r.text
            assert r.status_code != 200

    asyncio.run(body())


# ── 415 — body / content validation ───────────────────────────────────


def test_put_415_non_string_content(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id, _ = await _seed_run(
                core, project_id, status="paused", review_path="plan.md"
            )
            _make_artifacts_dir(proj_root, run_id)

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/plan.md",
                json={"content": 42},
            )
            assert r.status_code == 415, r.text

    asyncio.run(body())


def test_put_415_nul_byte_content(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id, _ = await _seed_run(
                core, project_id, status="paused", review_path="plan.md"
            )
            _make_artifacts_dir(proj_root, run_id)

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/plan.md",
                json={"content": "a\x00b"},
            )
            assert r.status_code == 415, r.text

    asyncio.run(body())


# ── 413 — oversize ────────────────────────────────────────────────────


def test_put_413_oversize(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id, _ = await _seed_run(
                core, project_id, status="paused", review_path="plan.md"
            )
            _make_artifacts_dir(proj_root, run_id)

            oversize = "x" * (MAX_FILE_BYTES + 1)
            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/plan.md",
                json={"content": oversize},
            )
            assert r.status_code == 413, r.text

    asyncio.run(body())


# ── Belt-and-braces ───────────────────────────────────────────────────


def test_put_leaves_no_tmp_siblings(tmp_path: Path) -> None:
    """A successful atomic write leaves no ``.plan.md.tmp.*`` siblings
    in the artifacts dir (the tempfile-rename pattern cleans up by
    virtue of ``Path.replace`` consuming the temp file)."""
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id, _ = await _seed_run(
                core, project_id, status="paused", review_path="plan.md"
            )
            artifacts = _make_artifacts_dir(proj_root, run_id)

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/plan.md",
                json={"content": "x"},
            )
            assert r.status_code == 200, r.text
            siblings = list(artifacts.iterdir())
            assert siblings == [artifacts / "plan.md"], siblings

    asyncio.run(body())


# ── 14f / ADR-41: plural review_paths ──────────────────────────────────


def test_put_accepts_either_of_two_review_paths(tmp_path: Path) -> None:
    """A paused iter with ``signal_args.review_paths = ["a.md", "b.md"]``
    accepts PUT to either path; PUT to a third → 409 ``path_mismatch``."""
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id, _ = await _seed_run(
                core,
                project_id,
                status="paused",
                review_paths=["frontend-audit.md", "backend-audit.md"],
            )
            _make_artifacts_dir(proj_root, run_id)

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/frontend-audit.md",
                json={"content": "front\n"},
            )
            assert r.status_code == 200, r.text

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/backend-audit.md",
                json={"content": "back\n"},
            )
            assert r.status_code == 200, r.text

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/third.md",
                json={"content": "nope"},
            )
            assert r.status_code == 409, r.text
            assert "review_paths" in r.json()["detail"]

    asyncio.run(body())


def test_put_409_review_paths_empty_list(tmp_path: Path) -> None:
    """A paused iter with ``signal_args.review_paths = []`` is treated
    the same as a paused iter with no review_path attribute — 409
    ``no_review_path`` for every write."""
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id, _ = await _seed_run(
                core, project_id, status="paused", review_paths=[]
            )
            _make_artifacts_dir(proj_root, run_id)

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/plan.md",
                json={"content": "x"},
            )
            assert r.status_code == 409, r.text
            assert "review_path" in r.json()["detail"]

    asyncio.run(body())


def test_put_legacy_singular_review_path_still_works(tmp_path: Path) -> None:
    """Migration fallback: a paused iter whose ``signal_args`` carries
    ONLY the legacy scalar ``review_path`` key (no plural key) still
    accepts the PUT — handles iters paused under 14a–14d that survive
    a process restart into the 14f code."""
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id, _ = await _seed_run(
                core, project_id, status="paused", review_path="legacy.md"
            )
            artifacts = _make_artifacts_dir(proj_root, run_id)

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/legacy.md",
                json={"content": "ok\n"},
            )
            assert r.status_code == 200, r.text
            assert (artifacts / "legacy.md").read_text() == "ok\n"

    asyncio.run(body())


def test_put_plural_review_paths_primary(tmp_path: Path) -> None:
    """A paused iter seeded with the plural key (a single-element list)
    exercises the 14f primary read path; the singular key is absent."""
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id, _ = await _seed_run(
                core,
                project_id,
                status="paused",
                review_paths=["only.md"],
            )
            _make_artifacts_dir(proj_root, run_id)

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/only.md",
                json={"content": "x"},
            )
            assert r.status_code == 200, r.text

    asyncio.run(body())


def test_put_editor_field_overrides_default(tmp_path: Path) -> None:
    """An explicit ``editor`` in the body is recorded verbatim in the
    event payload (defaults to ``"dashboard"`` otherwise)."""
    s = _settings(tmp_path)
    proj_root = tmp_path / "proj"
    proj_root.mkdir()

    async def body() -> None:
        async with _client_with_core(s) as (ac, core):
            project_id = await _register_project(ac, proj_root)
            run_id, _ = await _seed_run(
                core, project_id, status="paused", review_path="plan.md"
            )
            _make_artifacts_dir(proj_root, run_id)

            r = await ac.put(
                f"/api/runs/{run_id}/artifacts/plan.md",
                json={"content": "x", "editor": "curl"},
            )
            assert r.status_code == 200, r.text

            async with core._sm() as sess:  # noqa: SLF001
                ev = list(
                    await sess.scalars(
                        select(Event).where(
                            Event.run_id == run_id,
                            Event.kind == "artifact_edited",
                        )
                    )
                )[0]
            assert ev.payload["editor"] == "curl"

    asyncio.run(body())
