"""OQ-5: pi version pin advisory check (pure parsing/compare)."""

from __future__ import annotations

from relay.harness.pi import pi_version_mismatch_warning


def test_matching_version_returns_none() -> None:
    assert pi_version_mismatch_warning("pi 0.74.0", "0.74.0") is None


def test_matching_version_with_extra_text_returns_none() -> None:
    assert (
        pi_version_mismatch_warning("pi version 0.74.0 (abc123)\n", "0.74.0")
        is None
    )


def test_mismatched_version_warns() -> None:
    msg = pi_version_mismatch_warning("pi 0.75.3", "0.74.0")
    assert msg is not None
    assert "0.75.3" in msg and "0.74.0" in msg


def test_unparseable_output_warns() -> None:
    msg = pi_version_mismatch_warning("command not found", "0.74.0")
    assert msg is not None
    assert "0.74.0" in msg
