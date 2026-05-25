"""Wire-format helpers for datetime serialization.

SQLite's ``CURRENT_TIMESTAMP`` returns a naive UTC datetime; emitting it
as a naive ISO string ("2026-05-25T14:07:58") makes JavaScript's
``Date.parse`` interpret it as LOCAL time, so the dashboard's live
duration reads N hours off for any non-UTC client timezone. Anything
that crosses the wire goes through here so the UTC marker is explicit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import PlainSerializer


def to_utc_iso(dt: datetime) -> str:
    """Render a datetime as an ISO-8601 string with an explicit UTC offset.

    A naive datetime is assumed to be UTC (SQLite's ``CURRENT_TIMESTAMP``
    invariant) and tagged accordingly; an aware datetime is converted to
    UTC first so the wire format is uniform.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return dt.isoformat()


UtcDatetime = Annotated[
    datetime,
    PlainSerializer(to_utc_iso, return_type=str, when_used="json"),
]
"""``datetime`` field that serializes to UTC-tagged ISO on the wire.

Python attribute access still yields a ``datetime``; only JSON output is
affected (``when_used="json"``).
"""
