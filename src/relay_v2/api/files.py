"""W3: sandboxed, read-only project file browser (spec.md §7).

Two GET routes expose a project's working tree:

- ``GET /api/projects/{project_id}/files?path=<relative>`` — directory
  listing (default = project root).
- ``GET /api/projects/{project_id}/files/{file_path:path}`` — text file
  content.

Everything is **read-only** and **sandboxed** to the project's
``root_path``. The security-critical confinement is concentrated in the
single audited :func:`resolve_within_sandbox` function below; route
handlers never resolve a path any other way.

Threat model
------------
A caller controls the ``path`` query / ``file_path`` segment and may try
to read files outside the project root via:

1. an **absolute path** (``/etc/passwd``),
2. **``..`` traversal** (``../../etc/passwd``, including URL-encoded
   ``%2e%2e%2f`` which FastAPI/Starlette percent-decodes *before* the
   handler sees it — so the decoded value is what we validate),
3. a **symlink inside the sandbox** that points outside it
   (``ln -s /etc/passwd <root>/evil``),
4. a **NUL byte** smuggled into the path.

:func:`resolve_within_sandbox` blocks all four (defense in depth) and
raises :class:`SandboxViolation`, which the handlers map to HTTP 400.
Non-existence is a separate concern → HTTP 404 (never 400, so a probe
cannot distinguish "blocked" from "absent" beyond the status code).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from relay_v2.api.deps import get_core

__all__ = [
    "SandboxViolation",
    "resolve_within_sandbox",
    "router",
    "serve_file",
    "serve_listing",
]

# Largest file body we will read into memory / return. Larger → HTTP 413.
MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB
# Bytes sniffed for a NUL to classify a file as binary (→ HTTP 415).
BINARY_SNIFF_BYTES = 8192


class SandboxViolation(Exception):
    """The requested path escaped (or tried to escape) the project root.

    Always maps to HTTP 400. Raised for absolute input, ``..``
    traversal, an embedded NUL byte, or a symlink whose real target is
    outside the sandbox.
    """


def resolve_within_sandbox(root: Path, rel: str) -> Path:
    """Resolve ``rel`` against ``root``, guaranteeing the result is
    inside the real (symlink-resolved) root.

    Raises :class:`SandboxViolation` (→ HTTP 400) on absolute paths,
    ``..`` traversal, an embedded NUL byte, or symlink escape. Raises
    :class:`FileNotFoundError` (→ HTTP 404) only because ``root`` itself
    does not exist; a non-existent *target* is **not** an error here
    (callers check existence separately and return 404).

    Defense in depth — every check below is independently sufficient to
    block its attack; all are performed:

    1. ``rel`` containing a NUL byte → reject (path-truncation tricks).
    2. ``rel`` absolute (``/etc/passwd``) → reject.
    3. any path component equal to ``..`` → reject (lexical traversal,
       caught *before* touching the filesystem).
    4. ``root.resolve(strict=True)`` then
       ``(root_real / rel).resolve(strict=False)``: ``Path.resolve``
       follows symlinks, so a symlink inside the sandbox pointing out
       resolves to its real (outside) location.
    5. final containment assertion: the resolved target must be the real
       root itself or a descendant of it
       (``target_real.is_relative_to(root_real)``, Python 3.13). This is
       the catch-all that defeats symlink escape and any traversal that
       slipped past the lexical check.
    """
    # (1) NUL byte — never legal in a path; reject before anything else.
    if "\x00" in rel:
        raise SandboxViolation("path contains a NUL byte")

    # (2) Absolute input. Check both POSIX semantics (what the URL/query
    # carries) and the host os, so e.g. a leading "/" is rejected
    # regardless of platform quirks.
    if PurePosixPath(rel).is_absolute() or os.path.isabs(rel):
        raise SandboxViolation(f"absolute path not allowed: {rel!r}")

    # (3) Lexical ".." rejection — catch traversal before the filesystem
    # is consulted at all. Split on both separators defensively.
    parts = PurePosixPath(rel).parts
    if any(part == ".." for part in parts):
        raise SandboxViolation(f"'..' traversal not allowed: {rel!r}")

    # (4) Resolve. strict=True on the root: if the project root does not
    # exist that is a genuine FileNotFoundError (→ 404), not a 400.
    root_real = root.resolve(strict=True)
    # strict=False: a non-existent target still normalises so we can
    # range-check it; existence is the handler's concern (→ 404).
    target_real = (root_real / rel).resolve(strict=False)

    # (5) Containment catch-all. is_relative_to is true for the root
    # itself and any descendant; symlink escape fails here because (4)
    # already followed the link to its real outside-the-sandbox path.
    if not target_real.is_relative_to(root_real):
        raise SandboxViolation(
            f"path escapes project root: {rel!r}"
        )
    return target_real


def _mtime_iso(p: Path) -> str:
    """File mtime as a UTC ISO-8601 string (stable, timezone-explicit)."""
    return datetime.fromtimestamp(
        p.stat().st_mtime, tz=UTC
    ).isoformat()


def _err(status: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail})


# ── shared serving logic (the single audited read-only file path) ──────
# Both the project file browser and the run-artifacts browser (ADR-25)
# call these with a different sandbox ``root``. Exactly one
# ``resolve_within_sandbox`` and one serving implementation — no
# duplication across the two trust roots.


def serve_listing(root: Path, path: str) -> JSONResponse:
    """Directory listing JSON for ``path`` resolved within ``root``.
    Entries sorted dirs-first then name-ascending. Errors → typed JSON:
    400 sandbox violation, 404 missing root / path / not-a-dir-as-dir."""
    try:
        target = resolve_within_sandbox(root, path)
    except SandboxViolation as exc:
        return _err(400, str(exc))
    except FileNotFoundError:
        return _err(404, "sandbox root does not exist")

    if not target.exists():
        return _err(404, f"path not found: {path!r}")
    if not target.is_dir():
        return _err(400, f"not a directory: {path!r}")

    entries: list[dict[str, Any]] = []
    for child in target.iterdir():
        is_dir = child.is_dir()
        try:
            size = child.stat().st_size
            modified = _mtime_iso(child)
        except OSError:
            # A broken symlink inside the sandbox: list it, but do not
            # 500 trying to stat its missing target.
            size = 0
            modified = ""
        entries.append(
            {
                "name": child.name,
                "is_dir": is_dir,
                "size": size,
                "modified": modified,
            }
        )
    entries.sort(key=lambda e: (not e["is_dir"], e["name"]))

    root_real = root.resolve(strict=True)
    rel = target.resolve(strict=False).relative_to(root_real)
    normalized = "" if str(rel) == "." else str(rel)
    return JSONResponse(
        status_code=200,
        content={"path": normalized, "entries": entries},
    )


def serve_file(root: Path, file_path: str) -> JSONResponse:
    """Text file content JSON for ``file_path`` resolved within ``root``.
    Binary (NUL in the first 8 KiB) → 415; larger than
    :data:`MAX_FILE_BYTES` → 413; decoded UTF-8 ``errors="replace"``
    (display, not byte-exact). A typed JSON envelope (not a raw body)
    keeps the dashboard client strongly typed."""
    try:
        target = resolve_within_sandbox(root, file_path)
    except SandboxViolation as exc:
        return _err(400, str(exc))
    except FileNotFoundError:
        return _err(404, "sandbox root does not exist")

    if not target.exists():
        return _err(404, f"path not found: {file_path!r}")
    if target.is_dir():
        return _err(400, f"is a directory: {file_path!r}")

    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        return _err(
            413,
            f"file too large: {size} bytes > {MAX_FILE_BYTES} limit",
        )

    raw = target.read_bytes()
    if b"\x00" in raw[:BINARY_SNIFF_BYTES]:
        return _err(415, "binary file: not text")

    content = raw.decode("utf-8", errors="replace")
    root_real = root.resolve(strict=True)
    rel = target.resolve(strict=False).relative_to(root_real)
    return JSONResponse(
        status_code=200,
        content={
            "path": str(rel),
            "content": content,
            "size": size,
            "modified": _mtime_iso(target),
        },
    )


router = APIRouter(prefix="/api", tags=["files"])


@router.get("/projects/{project_id}/files")
async def list_files(
    project_id: int,
    request: Request,
    path: str = Query(default=""),
) -> JSONResponse:
    """Project-root directory listing (thin adapter over
    :func:`serve_listing`; sandbox root = the project's ``root_path``)."""
    core = get_core(request)
    project = await core.get_project(project_id)
    if project is None:
        return _err(404, f"unknown project {project_id}")
    return serve_listing(Path(project.root_path), path)


@router.get("/projects/{project_id}/files/{file_path:path}")
async def get_file(
    project_id: int,
    file_path: str,
    request: Request,
) -> JSONResponse:
    """Project-root file content (thin adapter over :func:`serve_file`;
    sandbox root = the project's ``root_path``)."""
    core = get_core(request)
    project = await core.get_project(project_id)
    if project is None:
        return _err(404, f"unknown project {project_id}")
    return serve_file(Path(project.root_path), file_path)
