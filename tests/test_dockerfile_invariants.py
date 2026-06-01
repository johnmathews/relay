"""Guard against accidental reverts of the pi-from-fork build (ADR-52).

The Dockerfile stage that produces pi for the runtime image must:
1. Reference johnmathews/pi (not @earendil-works/pi-coding-agent on npm).
2. Pin to an immutable ref (SHA or annotated tag).
3. Run `npm ci && npm run build` so the bridge files are produced.
4. Result in /opt/pi being copied into the runtime image.
5. Remove the musl claude-agent-sdk variants post-install — the SDK's
   variant selector (`sdk.mjs` `F5`) tries `linux-*-musl` FIRST on
   Linux and locks in whichever path `require.resolve` succeeds for.
   On a glibc Debian image the musl ELF fails ENOENT at spawn
   (missing `/lib/ld-musl-x86_64.so.1` interpreter); the SDK reports
   the misleading "Claude Code native binary not found at …-musl/
   claude". Deleting the musl tree forces the fallback to the glibc
   variant.

If any of these invariants is broken, the published image will silently
fall back to npm-published pi which strips the @anthropic-ai/
claude-agent-sdk bridge and bills against extra usage instead of the
Max subscription. The runtime symptom is a 400 from Anthropic, but it
only shows up under live OAuth — too late for CI.
"""
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def test_pi_stage_builds_from_fork() -> None:
    text = DOCKERFILE.read_text()
    assert "johnmathews/pi" in text, "pi must be cloned from the fork"
    assert "npm ci" in text and "npm run build" in text, (
        "pi must be built from source, not installed from npm"
    )
    assert "@earendil-works/pi-coding-agent" not in text, (
        "must not fall back to npm-published pi (strips agent-sdk bridge)"
    )


def test_pi_ref_is_immutable() -> None:
    text = DOCKERFILE.read_text()
    import re

    match = re.search(r"ARG\s+PI_REF=([^\s]+)", text)
    assert match, "Dockerfile must declare ARG PI_REF"
    ref = match.group(1)
    assert (
        len(ref) == 40 or ref.startswith("relay-bridge-")
    ), f"PI_REF must be a full SHA or a relay-bridge tag; got {ref!r}"


def test_runtime_stage_copies_built_tree() -> None:
    text = DOCKERFILE.read_text()
    assert "COPY --from=pi" in text, "runtime stage must copy from pi build stage"
    assert "/opt/pi" in text, (
        "runtime stage must place the built pi tree at /opt/pi "
        "(matches the symlink target)"
    )


def test_pi_stage_removes_musl_variants() -> None:
    text = DOCKERFILE.read_text()
    assert "claude-agent-sdk-*-musl" in text, (
        "Dockerfile must `rm -rf` the musl claude-agent-sdk variants "
        "after npm install — the SDK selector tries musl first on Linux "
        "and the musl ELF fails ENOENT at spawn on glibc images. See the "
        "module docstring for the failure mode."
    )
