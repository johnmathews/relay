"""Regression tests for the UTC wire-format helper.

The bug: SQLite ``CURRENT_TIMESTAMP`` returns a naive UTC datetime; emitting
it with bare ``.isoformat()`` produces "2026-05-25T14:07:58", which
JavaScript's ``Date.parse`` interprets as LOCAL time. In a CEST browser
that's +2 h, so the dashboard's "live · Xs ago" badge reads 2 h too
high. The fix forces an explicit UTC offset on every datetime that
crosses the wire.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from relay._time import to_utc_iso


def test_to_utc_iso_tags_naive_as_utc() -> None:
    naive = datetime(2026, 5, 25, 14, 7, 58)
    out = to_utc_iso(naive)
    assert out.endswith("+00:00"), out


def test_to_utc_iso_normalises_aware_to_utc() -> None:
    cest = timezone(timedelta(hours=2))
    aware = datetime(2026, 5, 25, 16, 7, 58, tzinfo=cest)
    assert to_utc_iso(aware) == "2026-05-25T14:07:58+00:00"


def test_to_utc_iso_already_utc_is_unchanged() -> None:
    aware = datetime(2026, 5, 25, 14, 7, 58, tzinfo=UTC)
    assert to_utc_iso(aware) == "2026-05-25T14:07:58+00:00"
