"""The search continuation engine (spec §21, §21A, §23D; design §4.2-§4.4, §5).

Pure: no Telethon, no SQLite. It walks a canonical, digest-bound peer
universe through a window of at most 64 active peers, round-robin, one
bounded page per call, and reports §23D coverage. The caller supplies a
``fetch(identity, offset_id, want)`` coroutine returning a page with
``views`` (each with ``message_id`` and ``sent_at``), ``exhausted``,
``inexact`` and ``next_offset``, and raises :class:`SearchStop` when a
work bound fires.

The invariants, each tested exhaustively in ``test_search_engine.py``:

- a page never collects more than ``limit`` hits, so nothing examined is lost;
- a peer is exhausted exactly when it is absent from ``per_peer`` inside the
  window (no flag to spoof), and the window only advances past exhausted peers;
- across a full continuation the union of scanned peers is the universe
  (or its first ``peer_cap`` peers, with ``peer_budget`` on every page);
- ``complete`` implies no cursor, no partial reasons, every peer scanned; any
  cursor carries ``response_limit``;
- ``peers_scanned`` counts peers searched to their end, and Telegram
  uncertainty, once seen, is reported on every later page of the walk.
"""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from comms.transports.telegram.canonical import jcs_dumps
from comms.transports.telegram.disclosure.coverage import build_coverage
from comms.transports.telegram.validation import parse_time

__all__ = [
    "WINDOW",
    "EngineState",
    "Hit",
    "PageResult",
    "SearchStop",
    "rpc_bounds",
    "run_page",
    "universe_digest",
]

WINDOW = 64  # design §4.3: the reviewed _PER_PEER_MAX
MAX_PEER_PAGE = 100  # Telegram's messages.search page ceiling
MAX_SEARCH_PAGES = 10  # spec §13.2 / §28: max_search_pages_per_call
_UNIVERSE_DOMAIN = b"telegram-mcp-universe/v1\0"
_EPOCH_FLOOR = datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)


class SearchStop(Exception):
    """A §23D work bound fired: deadline, rpc_budget or hit_budget."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Hit:
    peer: str  # canonical identity, "<type>:<id>"
    view: Any  # a MessageView; the engine reads only message_id and sent_at


@dataclass
class EngineState:
    upper_date: str
    window_start: int = 0
    next_unstarted_index: int = 0
    per_peer: dict[str, int] = field(default_factory=dict)  # identity -> offset_id
    # Sticky for the whole continuation: once Telegram answered inexactly (or a
    # peer was unreachable), no later page may claim completeness (§23D).
    uncertain: bool = False
    # Cumulative over the continuation, as [global, *one per selected project]:
    # peers searched to Telegram's own end, and peers the owner's live scope
    # excluded (they leave the eligible set; they are never searched).
    scanned: list[int] = field(default_factory=list)
    excluded: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class PageResult:
    hits: list[Hit]
    state: EngineState | None  # None: nothing left to continue
    coverage: dict[str, Any]
    complete: bool


def universe_digest(key: bytes, universe: Sequence[str]) -> str:
    """HMAC of the full ordered universe; the cursor stores this, never the list."""
    mac = hmac.new(key, _UNIVERSE_DOMAIN + jcs_dumps(list(universe)), hashlib.sha256)
    return "hmac-sha256:" + mac.hexdigest()


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text: str) -> datetime:
    return parse_time(text).astimezone(UTC)


def rpc_bounds(since: str | None, upper: str) -> tuple[datetime | None, datetime]:
    """Telegram's min/max_date are strict and 0 means unbounded (design §4.2).

    Over-fetch by a second on each side and post-filter. An absent ``since``
    is sent as 0 on purpose; a present one is clamped to at least 1 so an
    epoch-era ``since`` can never become an accidental 0.
    """
    upper_rpc = datetime.fromtimestamp(math.ceil(_parse(upper).timestamp()), UTC) + timedelta(
        seconds=1
    )
    if since is None:
        return None, upper_rpc
    lower = datetime.fromtimestamp(math.floor(_parse(since).timestamp()), UTC) - timedelta(
        seconds=1
    )
    # At or before the epoch the widened bound would be Telegram's 0
    # (unbounded) or negative. Over-fetching is safe (the post-filter is
    # exact); under-fetching is not, so send no lower bound at all.
    return (lower if lower >= _EPOCH_FLOOR else None), upper_rpc


def _in_range(sent_at: str, since: str | None, upper: str) -> bool:
    moment = _parse(sent_at)
    return (since is None or moment >= _parse(since)) and moment < _parse(upper)


async def run_page(
    universe: Sequence[str],
    state: EngineState,
    *,
    limit: int,
    fetch: Callable[[str, int, int], Awaitable[Any]],
    since: str | None,
    projects: Mapping[str, frozenset[str]],
    peer_cap: int | None = None,
    max_hits: int = 500,
    max_pages: int = MAX_SEARCH_PAGES,
    accept: Callable[[str, Any], bool] = lambda peer, view: True,
    rpcs_used: Callable[[], int] | None = None,
) -> PageResult:
    """One bounded page. ``state.upper_date`` is the frozen upper anchor.

    ``accept(peer, view)`` is asked before each in-range hit is taken; the
    reads use it to hold a page under the response cap. A refusal ends the
    page with that peer resuming *at* the refused hit, so nothing examined
    is ever lost or repeated.

    Coverage counters are measured, never re-derived: ``rpcs_used`` reports
    every Telegram request the call really made (dialog checks included), a
    page's ``examined`` is the raw entries Telegram returned before any
    filtering, a page with ``searched=False`` was decided without a search
    request, and one with ``excluded=True`` left the eligible set.
    """
    searchable = list(universe[:peer_cap]) if peer_cap is not None else list(universe)
    capped = peer_cap is not None and len(universe) > peer_cap
    hits: list[Hit] = []
    reasons: list[str] = []
    rpcs = examined = pages = 0
    stopped = False
    width = 1 + len(projects)
    if not state.scanned:
        state.scanned = [0] * width
    if not state.excluded:
        state.excluded = [0] * width
    memberships = list(projects.values())

    def count(vector: list[int], peer: str) -> None:
        vector[0] += 1
        for index, members in enumerate(memberships, start=1):
            if peer in members:
                vector[index] += 1

    def active() -> list[str]:
        return [
            searchable[i]
            for i in range(state.window_start, state.next_unstarted_index)
            if searchable[i] in state.per_peer
        ]

    def refill() -> None:
        while len(active()) < WINDOW and state.next_unstarted_index < len(searchable):
            state.per_peer[searchable[state.next_unstarted_index]] = 0
            state.next_unstarted_index += 1

    def advance() -> None:
        # The window only moves past exhausted peers: nothing is ever skipped.
        while (
            state.window_start < state.next_unstarted_index
            and searchable[state.window_start] not in state.per_peer
        ):
            state.window_start += 1

    refill()
    while len(hits) < limit and not stopped:
        peers = active()
        if not peers:
            break
        for peer in peers:
            if len(hits) >= limit:
                break
            # Never ask for more than the page, Telegram's ceiling, or the
            # examined-hit budget still allows: nothing is examined and lost.
            want = min(limit - len(hits), MAX_PEER_PAGE, max_hits - examined)
            if pages >= max_pages:  # §13.2: the closed reasons name this rpc_budget
                reasons.append("rpc_budget")
                stopped = True
                break
            try:
                page = await fetch(peer, state.per_peer[peer], want)
            except SearchStop as stop:
                reasons.append(stop.reason)
                stopped = True
                break
            searched = getattr(page, "searched", True)
            if searched:
                rpcs += 1
                pages += 1
            examined += getattr(page, "examined", len(page.views))
            if page.inexact:
                state.uncertain = True
            full = False
            for view in page.views[:want]:
                if not _in_range(view.sent_at, since, state.upper_date):
                    continue
                # Always asked (so the caller counts every hit); a refusal is honoured
                # once the page holds one hit, which the response bounds guarantee fits.
                if not accept(peer, view) and hits:
                    state.per_peer[peer] = int(view.message_id) + 1  # offset_id is exclusive
                    full = True
                    break
                hits.append(Hit(peer, view))
            if full:
                stopped = True
                break
            if page.exhausted:
                del state.per_peer[peer]
                if getattr(page, "excluded", False):
                    count(state.excluded, peer)
                elif searched:
                    count(state.scanned, peer)
            else:
                state.per_peer[peer] = int(page.next_offset)
            if examined >= max_hits:  # spec §13.2: at most 500 examined hits per call
                reasons.append("hit_budget")
                stopped = True
                break
        advance()
        refill()

    advance()
    remaining = bool(active()) or state.next_unstarted_index < len(searchable)
    if state.uncertain:
        reasons.append("telegram_partial")
    if capped:
        reasons.append("peer_budget")
    if remaining:
        reasons.append("response_limit")
    complete = not reasons
    hits.sort(key=lambda h: (h.view.sent_at, h.peer, h.view.message_id), reverse=True)
    # Scanned means searched to Telegram's own end (never merely started: the
    # window starts up to 64 peers before a request). Eligible excludes the
    # peers the owner's live scope has excluded so far; an unreachable peer
    # stays eligible and unscanned.
    project_coverage = [
        {
            "project_ref": ref,
            "eligible_peers": sum(1 for p in universe if p in members) - state.excluded[index],
            "peers_scanned": state.scanned[index],
        }
        for index, (ref, members) in enumerate(projects.items(), start=1)
    ]
    coverage = build_coverage(
        complete=complete,
        eligible_peers=len(universe) - state.excluded[0],
        peers_scanned=state.scanned[0],
        telegram_rpcs=rpcs_used() if rpcs_used is not None else rpcs,
        hits_examined=examined,
        hits_returned=len(hits),
        partial_reasons=_ordered(reasons),
        project_coverage=project_coverage,
    )
    return PageResult(
        hits=hits, state=state if remaining else None, coverage=coverage, complete=complete
    )


def _ordered(reasons: Sequence[str]) -> list[str]:
    order = (
        "deadline",
        "rpc_budget",
        "hit_budget",
        "peer_budget",
        "response_limit",
        "telegram_partial",
    )
    return [r for r in order if r in set(reasons)]
