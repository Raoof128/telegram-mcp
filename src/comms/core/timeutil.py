"""UTC instants only (comms 5b-4 design §8, R16).

Naive datetimes are refused; offsets are normalized to UTC; the stored form is
exactly ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` so text comparison equals time comparison.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

__all__ = ["instant", "iso", "parse", "utc"]

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
# The legacy Telegram rows' second-precision spelling (read-only: comms never writes it).
_LEGACY = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


def utc(dt: datetime) -> datetime:
    """``dt`` as a UTC instant; a naive datetime is refused."""
    if not isinstance(dt, datetime) or dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("a timezone-aware datetime is required")
    return dt.astimezone(UTC)


def iso(dt: datetime) -> str:
    """The canonical stored form of ``dt``."""
    return utc(dt).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse(text: object) -> datetime:
    """Inverse of :func:`iso`; refuses every other spelling."""
    if not isinstance(text, str) or _ISO.fullmatch(text) is None:
        raise ValueError("not a canonical UTC time")
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def instant(text: object) -> datetime:
    """A stored comms time, or a legacy Telegram second-precision time; nothing else."""
    if isinstance(text, str) and _LEGACY.fullmatch(text):
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return parse(text)
