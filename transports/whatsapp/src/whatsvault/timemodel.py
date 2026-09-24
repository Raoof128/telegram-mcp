"""Time as uncertainty intervals (spec §3.3)."""

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from zoneinfo import ZoneInfo

_PRECISIONS = {"ms", "s", "min", "day"}


@dataclass(frozen=True)
class Interval:
    ts_lower_ms: int
    ts_upper_ms_exclusive: int
    ts_precision: str

    def __post_init__(self):
        if self.ts_upper_ms_exclusive <= self.ts_lower_ms:
            raise ValueError("interval upper bound must exceed lower bound")
        if self.ts_precision not in _PRECISIONS:
            raise ValueError(f"bad precision: {self.ts_precision!r}")


def from_provider_seconds(ts_seconds: int) -> Interval:
    lower = int(ts_seconds) * 1000
    return Interval(lower, lower + 1000, "s")


def from_local_minute(epoch_minute_start_ms: int) -> Interval:
    return Interval(epoch_minute_start_ms, epoch_minute_start_ms + 60000, "min")


def definitely_before(a: Interval, b: Interval) -> bool:
    return a.ts_upper_ms_exclusive <= b.ts_lower_ms


def temporal_overlap(a: Interval, b: Interval) -> bool:
    return a.ts_lower_ms < b.ts_upper_ms_exclusive and b.ts_lower_ms < a.ts_upper_ms_exclusive


class DstClass(Enum):
    UNAMBIGUOUS = "unambiguous"
    FOLD = "fold"
    NONEXISTENT = "nonexistent"


def classify_local(zone: str, local_dt: dt.datetime) -> DstClass:
    tz = ZoneInfo(zone)
    aware = local_dt.replace(tzinfo=tz)
    normalised = aware.astimezone(dt.UTC).astimezone(tz).replace(tzinfo=None)
    if normalised != local_dt:
        return DstClass.NONEXISTENT
    off0 = local_dt.replace(tzinfo=tz, fold=0).utcoffset()
    off1 = local_dt.replace(tzinfo=tz, fold=1).utcoffset()
    return DstClass.FOLD if off0 != off1 else DstClass.UNAMBIGUOUS
