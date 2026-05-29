"""The auto-generated OpenAPI schema must be valid OpenAPI v3.

docs/plan.md Phase 3 verification: ``GET /openapi.json`` returns a valid
OpenAPI v3 schema. We assert it structurally (openapi-spec-validator),
that every spec §7 resource path is present, and that operations are
grouped by resource tag (plan.md: "OpenAPI tags grouped by resource").
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from openapi_spec_validator import validate

from relay.app import create_app
from relay.config import Settings

# spec.md §7 surface (path templates as FastAPI emits them).
EXPECTED_PATHS = {
    "/api/runs",
    "/api/runs/{run_id}",
    "/api/runs/{run_id}/cancel",
    "/api/runs/{run_id}/resume",
    "/api/runs/{run_id}/events",
    "/api/runs/{run_id}/preview",
    "/api/runs/{run_id}/artifacts",
    "/api/runs/{run_id}/artifacts/{file_path}",
    "/api/events/{run_id}",
    "/api/projects",
    "/api/projects/{project_id}",
    "/api/projects/{project_id}/files",
    "/api/projects/{project_id}/files/{file_path}",
    "/api/prompts",
    "/api/prompts/{prompt_id}",
    "/api/prompts/{prompt_id}/versions",
    "/api/system/browse",
}


def _schema() -> dict:
    app = create_app(Settings(data_dir=Path(tempfile.mkdtemp()) / ".relay"))
    return app.openapi()


def test_openapi_is_valid_v3() -> None:
    schema = _schema()
    assert schema["openapi"].startswith("3."), schema["openapi"]
    validate(schema)  # raises if not a valid OpenAPI document


def test_every_spec_7_path_is_present() -> None:
    paths = set(_schema()["paths"])
    missing = EXPECTED_PATHS - paths
    assert not missing, f"spec §7 endpoints missing from OpenAPI: {missing}"


def test_operations_are_tagged_by_resource() -> None:
    schema = _schema()
    tags = {
        tag
        for path, ops in schema["paths"].items()
        if path != "/health"
        for op in ops.values()
        for tag in op.get("tags", [])
    }
    assert {
        "runs",
        "projects",
        "prompts",
        "files",
        "events",
        "artifacts",
    } <= tags, tags
