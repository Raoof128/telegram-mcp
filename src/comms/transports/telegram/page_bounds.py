"""The §13.2 page bounds for Telegram reads: field ceilings and the per-page budget.

One copy, shared by the disclosure layer (``disclosure/bounds.py`` re-exports it and adds the
worst-case reservation shapes) and the v0.3 context source (``user/context.py``), which must not
reach the retired tool vocabulary.
"""

from __future__ import annotations

from typing import Any

from comms.core.canonical import jcs_dumps

__all__ = [
    "DATA_BYTES_MAX",
    "MEDIA_KIND_MAX",
    "NAME_MAX",
    "TEXT_CODEPOINTS_MAX",
    "TEXT_MAX",
    "USERNAME_MAX",
    "PageBudget",
    "clamp",
    "fit",
    "text_codepoints",
]

NAME_MAX = 256  # display names, chat titles, post authors (codepoints)
USERNAME_MAX = 64
TEXT_MAX = 4096  # Telegram's message ceiling; anything longer is truncated
MEDIA_KIND_MAX = 32
# The dispatcher refuses a response over 64 KiB, after the commit. Canonical
# data is held to 48 KiB so meta, the proof and the envelope always fit.
DATA_BYTES_MAX = 49_152
# Spec §13.2: the combined textual payload of one result, in codepoints. Every
# string in ``data`` counts (conservative: names and refs as well as bodies).
TEXT_CODEPOINTS_MAX = 32_000


def clamp(text: str | None, limit: int) -> tuple[str | None, bool]:
    """Truncate to ``limit`` codepoints; make lone surrogates encodable."""
    if text is None:
        return None, False
    text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def text_codepoints(value: Any) -> int:
    """Codepoints of every string in ``value`` (keys excluded)."""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(text_codepoints(v) for v in value.values())
    if isinstance(value, list):
        return sum(text_codepoints(v) for v in value)
    return 0


class PageBudget:
    """Both §13.2 response caps for one page, in one place.

    Built from the page's container (everything but the records); ``take``
    admits a record only if the canonical bytes stay within
    ``DATA_BYTES_MAX`` *and* the combined text stays within
    ``TEXT_CODEPOINTS_MAX``. Enforced while the page is built, before the
    disclosure commit, never after it.
    """

    def __init__(self, container: dict[str, Any]) -> None:
        self._bytes = DATA_BYTES_MAX - len(jcs_dumps(container))
        self._points = TEXT_CODEPOINTS_MAX - text_codepoints(container)
        self._kept = 0

    def take(self, record: Any) -> bool:
        size = len(jcs_dumps(record)) + (1 if self._kept else 0)  # the separating comma
        points = text_codepoints(record)
        if size > self._bytes or points > self._points:
            return False
        self._bytes -= size
        self._points -= points
        self._kept += 1
        return True


def fit(data: dict[str, Any], element: str) -> int:
    """Keep the longest record prefix that fits both caps; return drops."""
    records = data[element]
    budget = PageBudget({**data, element: []})
    kept = 0
    for record in records:
        if not budget.take(record):
            break
        kept += 1
    dropped = len(records) - kept
    del records[kept:]
    return dropped
