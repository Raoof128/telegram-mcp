"""WhatsApp export header grammar + whole-file date-format validation (spec §8).

The header regex captures the date token, time, and sender family-independently;
the family (DMY/MDY/YMD) only determines how the three numeric date components
are interpreted. A day value > 12 disproves any family that would read it as a
month — this is the load-bearing disambiguation rule (refuse, don't guess)."""

import calendar
import re

FAMILIES = ("DMY", "MDY", "YMD")

_DATE = r"\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}"
_TIME = r"\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap]\.?[Mm]\.?)?"
_HEADER_RE = re.compile(
    rf"^\[?(?P<date>{_DATE}),?\s+(?P<time>{_TIME})\]?\s*[-–]?\s*(?P<sender>[^:]{{1,100}}?):\s"
)


def build_header_regex(date_format: str) -> re.Pattern:
    if date_format not in FAMILIES:
        raise ValueError(f"unknown date_format: {date_format!r}")
    return _HEADER_RE


def _parse_date(date_str: str, family: str):
    parts = re.split(r"[/.\-]", date_str)
    if len(parts) != 3:
        return None
    try:
        a, b, c = (int(x) for x in parts)
    except ValueError:
        return None
    if family == "DMY":
        d, m, y = a, b, c
    elif family == "MDY":
        m, d, y = a, b, c
    else:  # YMD
        y, m, d = a, b, c
    if y < 100:
        y += 2000
    if not (1970 <= y <= 2100):
        return None
    if not (1 <= m <= 12):
        return None
    if not (1 <= d <= calendar.monthrange(y, m)[1]):
        return None
    return (y, m, d)


def validate_family(text: str, date_format: str, tz_name: str) -> dict:
    if date_format not in FAMILIES:
        raise ValueError(f"unknown date_format: {date_format!r}")
    header_count = 0
    first_bad_line = None
    for i, line in enumerate(text.splitlines(), 1):
        m = _HEADER_RE.match(line)
        if not m:
            continue
        header_count += 1
        if _parse_date(m.group("date"), date_format) is None and first_bad_line is None:
            first_bad_line = i
    ok = header_count > 0 and first_bad_line is None
    return {"ok": ok, "header_count": header_count, "first_bad_line": first_bad_line}


def suggest_families(text: str) -> list[str]:
    return [f for f in FAMILIES if validate_family(text, f, "UTC")["ok"]]
