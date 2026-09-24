"""Conservative transcript parsing (spec §8): multiline assembly, system/media
classification, and boundary surfacing.

SR-2: emits NAIVE local wall-clock components + dst_class; the writer computes the
UTC epoch only after dst_resolutions are applied. Never fabricates a participant
message — a header-shaped line whose date is invalid under the declared family is
surfaced as an `ambiguous_boundary` (#28), never a new message."""

import datetime as dt
import re

from ..timemodel import classify_local
from .grammar import _DATE, _TIME, _parse_date

_ENTRY_RE = re.compile(rf"^\[?(?P<date>{_DATE}),?\s+(?P<time>{_TIME})\]?\s*[-–]?\s*(?P<rest>.*)$")
_SENDER_RE = re.compile(r"^(?P<sender>[^:\n]{1,100}?):\s(?P<body>.*)$", re.DOTALL)
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([APap])?\.?([Mm])?")

# Media placeholder markers -> attachments.retrieval_state values.
_MEDIA_MARKERS = (
    "<media omitted>",
    "image omitted",
    "video omitted",
    "audio omitted",
    "sticker omitted",
    "gif omitted",
    "document omitted",
    "contact card omitted",
)
# Known system-line substrings (checked only when a line has NO sender prefix).
_KNOWN_SYSTEM = (
    "end-to-end encrypted",
    "created group",
    "added",
    "left",
    "removed",
    "changed the subject",
    "changed this group",
    "changed their phone number",
    "you deleted this message",
    "this message was deleted",
    "changed to",
    "missed voice call",
    "missed video call",
    "security code changed",
    "joined using",
    "changed the group description",
)


def _parse_time(tstr: str) -> tuple[int, int, int]:
    m = _TIME_RE.match(tstr)
    hh, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    ap = m.group(4)
    if ap:
        if ap.lower() == "p" and hh != 12:
            hh += 12
        elif ap.lower() == "a" and hh == 12:
            hh = 0
    return hh, mm, ss


def _media_state(body: str):
    low = body.strip().lower()
    return "MEDIA_PLACEHOLDER" if any(mk in low for mk in _MEDIA_MARKERS) else None


def _system_kind(text: str) -> str:
    low = text.lower()
    return "system" if any(pat in low for pat in _KNOWN_SYSTEM) else "system_unknown"


def _append_body(rec: dict, line: str, end: int) -> None:
    key = "body" if rec["kind"] == "message" else "text"
    rec[key] = (rec.get(key, "") + "\n" + line) if rec.get(key) else line
    rec["source_end_offset"] = end


def parse_transcript(text: str, date_format: str, tz_name: str) -> list[dict]:
    records: list[dict] = []
    cur: dict | None = None
    offset = 0
    for raw in text.splitlines(keepends=True):
        line = raw.rstrip("\n").rstrip("\r")
        start, end = offset, offset + len(raw)
        offset = end
        em = _ENTRY_RE.match(line)
        valid_date = _parse_date(em.group("date"), date_format) if em else None

        if em and valid_date is not None:
            if cur is not None:
                records.append(cur)
            y, m, d = valid_date
            hh, mm, ss = _parse_time(em.group("time"))
            dst = classify_local(tz_name, dt.datetime(y, m, d, hh, mm, ss)).value
            base = {
                "source_start_offset": start,
                "source_end_offset": end,
                "year": y,
                "month": m,
                "day": d,
                "hour": hh,
                "minute": mm,
                "second": ss,
                "dst_class": dst,
            }
            rest = em.group("rest")
            sm = _SENDER_RE.match(rest)
            if sm:
                body = sm.group("body")
                cur = {
                    **base,
                    "kind": "message",
                    "sender": sm.group("sender"),
                    "body": body,
                    "media_state": _media_state(body),
                }
            else:
                cur = {**base, "kind": _system_kind(rest), "text": rest}
        elif em and valid_date is None:
            # header-shaped but invalid date -> surface, never fabricate a message (#28)
            records.append(
                {
                    "kind": "ambiguous_boundary",
                    "text": line,
                    "source_start_offset": start,
                    "source_end_offset": end,
                }
            )
            if cur is not None:
                _append_body(cur, line, end)
        elif cur is not None:
            _append_body(cur, line, end)
            # leading noise before the first header is ignored (no evidence to attribute)

    if cur is not None:
        records.append(cur)
    for i, r in enumerate(records):
        r["source_ordinal"] = i
    return records
