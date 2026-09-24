"""The continuation engine as a state machine (design §4.3-§4.4, §5).

Exhaustive over small universes, fixed-seed over large ones. Pure: a fake
per-peer searcher stands in for Telegram.
"""

import asyncio
import itertools
import random
from dataclasses import dataclass

import pytest

from comms.transports.telegram.disclosure.coverage import validate_coverage
from comms.transports.telegram.telegram.search import (
    WINDOW,
    EngineState,
    SearchStop,
    rpc_bounds,
    run_page,
    universe_digest,
)

UPPER = "2026-09-23T12:00:00Z"
P = "tpr_" + "a" * 26


@dataclass(frozen=True)
class View:
    message_id: int
    sent_at: str


@dataclass
class Page:
    views: list
    exhausted: bool
    inexact: bool
    next_offset: int | None
    examined: int = 0  # raw entries Telegram returned, before any filtering
    searched: bool = True  # a real messages.Search request went out
    excluded: bool = False  # the owner's live scope excludes this peer


class FakeSearch:
    """Per peer, a descending list of message ids; minute = id % 60."""

    def __init__(
        self,
        hits_per_peer,
        *,
        rpc_budget=10**9,
        inexact=(),
        excluded=(),
        unreachable=(),
        per_request=None,
    ):
        self.data = hits_per_peer
        self.rpc_budget = rpc_budget
        self.inexact = set(inexact)
        self.excluded = set(excluded)
        self.unreachable = set(unreachable)
        self.per_request = per_request  # a withholding channel: at most this many per page
        self.scanned: set[str] = set()
        self.rpcs = 0

    async def fetch(self, peer, offset_id, want):
        if peer in self.excluded or peer in self.unreachable:  # decided without a search
            return Page(
                [],
                True,
                peer in self.unreachable,
                None,
                searched=False,
                excluded=peer in self.excluded,
            )
        if self.rpcs >= self.rpc_budget:
            raise SearchStop("rpc_budget")
        self.rpcs += 1
        self.scanned.add(peer)
        rest = [i for i in self.data[peer] if not offset_id or i < offset_id]
        ids = rest[: min(want, self.per_request or want)]
        views = [View(i, f"2026-09-23T10:{i % 60:02d}:00Z") for i in ids]
        exhausted = not ids or len(rest) == len(ids)
        return Page(
            views,
            exhausted,
            peer in self.inexact,
            None if exhausted else ids[-1],
            examined=len(ids),
        )


def _run_all(
    universe, data, *, limit, rpc_budget=10**9, peer_cap=None, max_pages=10_000, inexact=()
):
    """Page to the end. Returns (pages, all hits, fake)."""
    fake = FakeSearch(data, rpc_budget=rpc_budget, inexact=inexact)
    state = EngineState(upper_date=UPPER)
    pages, hits = [], []
    for _ in range(max_pages):
        fake.rpcs = 0  # a fresh work budget per call
        result = asyncio.run(
            run_page(
                universe,
                state,
                limit=limit,
                fetch=fake.fetch,
                since=None,
                projects={P: frozenset(universe)},
                peer_cap=peer_cap,
            )
        )
        pages.append(result)
        hits += [(h.peer, h.view.message_id) for h in result.hits]
        cursor = "tgc_" + "a" * 26 if result.state is not None else None
        validate_coverage(
            result.coverage, next_cursor=cursor, partial=not result.coverage["complete"]
        )
        assert len(result.hits) <= limit
        if result.state is None:
            return pages, hits, fake
        state = result.state
    raise AssertionError("continuation never ended")


@pytest.mark.parametrize(
    "peers,sizes,limit,budget",
    [
        (peers, sizes, limit, budget)
        for peers in (1, 2, 3)
        for sizes in itertools.product((0, 1, 3), repeat=peers)
        for limit in (1, 2, 5)
        for budget in (1, 2, 50)
    ],
)
def test_exhaustively_every_hit_arrives_exactly_once_and_it_ends(peers, sizes, limit, budget):
    universe = [f"user:{i}" for i in range(peers)]
    data = {
        p: list(range(100 * (k + 1) + n, 100 * (k + 1), -1))
        for k, (p, n) in enumerate(zip(universe, sizes, strict=True))
    }
    pages, hits, _fake = _run_all(universe, data, limit=limit, rpc_budget=budget)
    expected = {(p, i) for p, ids in data.items() for i in ids}
    assert sorted(hits) == sorted(expected) and len(hits) == len(set(hits))  # no loss, no duplicate
    assert pages[-1].complete is True and pages[-1].coverage["peers_scanned"] == peers
    for page in pages[:-1]:
        assert "response_limit" in page.coverage["partial_reasons"]


def test_the_window_holds_64_and_moves_to_peer_65():
    universe = [f"user:{i}" for i in range(70)]
    data = {p: [5, 4, 3, 2, 1] for p in universe}
    state = EngineState(upper_date=UPPER)
    fake = FakeSearch(data)
    asyncio.run(
        run_page(
            universe,
            state,
            limit=1,
            fetch=fake.fetch,
            since=None,
            projects={P: frozenset(universe)},
        )
    )
    assert state.next_unstarted_index == WINDOW and len(state.per_peer) == WINDOW
    _pages, hits, fake = _run_all(universe, data, limit=50)
    assert len(hits) == 70 * 5 and "user:64" in fake.scanned


def test_an_ordinary_universe_of_300_is_fully_searched():
    rng = random.Random(300)
    universe = [f"channel:{i}" for i in range(300)]
    data = {p: sorted(rng.sample(range(1, 400), rng.randint(0, 4)), reverse=True) for p in universe}
    pages, hits, fake = _run_all(universe, data, limit=50, rpc_budget=20)
    assert fake.scanned == set(universe) and pages[-1].complete is True
    assert len(hits) == sum(len(v) for v in data.values())


def test_a_cross_universe_of_251_never_searches_peer_251():
    universe = [f"user:{i}" for i in range(251)]
    data = {p: [1] for p in universe}
    pages, _hits, fake = _run_all(universe, data, limit=50, peer_cap=250)
    assert "user:250" not in fake.scanned and len(fake.scanned) == 250
    for page in pages:
        assert "peer_budget" in page.coverage["partial_reasons"] and page.complete is False
        assert page.coverage["eligible_peers"] == 251


def test_hits_above_the_upper_anchor_never_appear():
    universe = ["user:1"]
    state = EngineState(upper_date="2026-09-23T10:30:00Z")
    fake = FakeSearch({"user:1": [45, 31, 30, 29]})  # minutes 45, 31 are at/after the anchor
    result = asyncio.run(
        run_page(
            universe,
            state,
            limit=10,
            fetch=fake.fetch,
            since=None,
            projects={P: frozenset(universe)},
        )
    )
    assert [h.view.message_id for h in result.hits] == [29]


def test_since_is_inclusive_and_bounds_over_fetch_by_a_second():
    lower, upper = rpc_bounds("2026-09-23T10:00:00.500+00:00", "2026-09-23T11:00:00Z")
    assert lower.isoformat() == "2026-09-23T09:59:59+00:00"
    assert upper.isoformat() == "2026-09-23T11:00:01+00:00"
    assert rpc_bounds(None, UPPER)[0] is None  # absent is 0: unbounded
    # A widened bound at or before the epoch would be Telegram's 0 (= unbounded)
    # or negative: send no lower bound at all and rely on the exact post-filter.
    assert rpc_bounds("1970-01-01T00:00:00Z", UPPER)[0] is None
    assert rpc_bounds("1970-01-01T00:00:01.5Z", UPPER)[0] is None
    assert rpc_bounds("1970-01-01T00:00:03Z", UPPER)[0].timestamp() == 2


def test_a_hit_budget_stops_the_page_with_a_cursor():
    universe = ["user:1"]
    fake = FakeSearch({"user:1": list(range(1000, 0, -1))})
    state = EngineState(upper_date=UPPER)
    result = asyncio.run(
        run_page(
            universe,
            state,
            limit=50,
            fetch=fake.fetch,
            since=None,
            projects={P: frozenset(universe)},
            max_hits=40,
        )
    )
    assert result.coverage["partial_reasons"] == ["hit_budget", "response_limit"]
    assert result.state is not None and len(result.hits) == 40


def test_telegram_uncertainty_is_never_complete():
    universe = ["user:1"]
    fake = FakeSearch({"user:1": [3, 2]}, inexact={"user:1"})
    result = asyncio.run(
        run_page(
            universe,
            EngineState(upper_date=UPPER),
            limit=10,
            fetch=fake.fetch,
            since=None,
            projects={P: frozenset(universe)},
        )
    )
    assert result.complete is False and result.coverage["partial_reasons"] == ["telegram_partial"]
    assert result.state is None  # nothing left to continue, but still not claimed complete


def test_shared_peers_count_once_globally_and_once_per_project():
    universe = ["user:1", "user:2", "user:3"]
    projects = {
        P: frozenset({"user:1", "user:2"}),
        "tpr_" + "b" * 26: frozenset({"user:2", "user:3"}),
    }
    result = asyncio.run(
        run_page(
            universe,
            EngineState(upper_date=UPPER),
            limit=10,
            fetch=FakeSearch({p: [] for p in universe}).fetch,
            since=None,
            projects=projects,
        )
    )
    assert result.coverage["eligible_peers"] == 3
    assert [c["eligible_peers"] for c in result.coverage["project_coverage"]] == [2, 2]


def test_the_universe_digest_is_keyed_and_order_sensitive():
    key = b"\x01" * 32
    one = universe_digest(key, ["user:1", "user:2"])
    assert one.startswith("hmac-sha256:") and len(one) == 12 + 64
    assert (
        one
        != universe_digest(key, ["user:2", "user:1"])
        != universe_digest(b"\x02" * 32, ["user:1", "user:2"])
    )


def test_a_page_held_under_the_cap_loses_and_repeats_nothing():
    """The reads' accept() refuses hits past the response cap; the next page resumes at them."""
    universe = ["user:1", "user:2"]
    data = {"user:1": [9, 8, 7, 6], "user:2": [5, 4, 3]}
    fake = FakeSearch(data)
    state = EngineState(upper_date=UPPER)
    seen, pages = [], 0
    while True:
        taken = []

        def accept(peer, view, taken=taken):
            taken.append(view.message_id)
            return len(taken) < 3  # room for two hits per page

        result = asyncio.run(
            run_page(
                universe,
                state,
                limit=10,
                fetch=fake.fetch,
                since=None,
                projects={P: frozenset(universe)},
                accept=accept,
            )
        )
        pages += 1
        seen += [(h.peer, h.view.message_id) for h in result.hits]
        assert len(result.hits) <= 2  # accept() leaves room for two
        if result.state is None:
            break
        assert "response_limit" in result.coverage["partial_reasons"]
        state = result.state
    assert sorted(seen) == sorted((p, i) for p, ids in data.items() for i in ids)
    assert len(seen) == len(set(seen)) and pages >= 3


def test_telegram_uncertainty_sticks_to_the_whole_continuation():
    """Spec §23D: complete only if EVERY peer reached Telegram's exhaustion."""
    universe = ["user:1", "user:2"]
    data = {"user:1": [3, 2], "user:2": [9, 8, 7, 6, 5]}
    pages, hits, _fake = _run_all(universe, data, limit=1, inexact={"user:1"})
    assert len(hits) == 7 and pages[-1].state is None
    first = next(
        i for i, p in enumerate(pages) if "telegram_partial" in p.coverage["partial_reasons"]
    )
    for page in pages[first:]:
        assert "telegram_partial" in page.coverage["partial_reasons"] and page.complete is False


def test_peers_scanned_counts_only_peers_searched_to_the_end():
    universe = [f"user:{i}" for i in range(10)]
    fake = FakeSearch({p: [5, 4, 3, 2, 1] for p in universe}, rpc_budget=3)
    result = asyncio.run(
        run_page(
            universe,
            EngineState(upper_date=UPPER),
            limit=50,
            fetch=fake.fetch,
            since=None,
            projects={P: frozenset(universe[:4])},
        )
    )
    assert fake.rpcs == 3 and result.coverage["peers_scanned"] == 3  # never the 10 started
    assert result.coverage["project_coverage"][0]["peers_scanned"] == 3


@pytest.mark.parametrize(
    "text", ["2026-09-23T10:00:00Z", "2026-09-23T10:00:00z", "2026-09-23t10:00:00Z"]
)
def test_rfc3339_case_variants_parse_the_same(text):
    """RFC 3339 §5.6 allows lowercase t/z; jsonschema's date-time accepts them."""
    from comms.transports.telegram.validation import parse_time

    assert parse_time(text).isoformat() == "2026-09-23T10:00:00+00:00"
    assert rpc_bounds(text, text)[0].isoformat() == "2026-09-23T09:59:59+00:00"


def _one_page(universe, fake, **kw):
    kw.setdefault("projects", {P: frozenset(universe)})
    return asyncio.run(
        run_page(
            universe,
            EngineState(upper_date=UPPER),
            limit=kw.pop("limit", 50),
            fetch=fake.fetch,
            since=None,
            **kw,
        )
    )


@pytest.mark.parametrize("pages_needed,complete", [(9, True), (10, True), (11, False)])
def test_search_pages_are_bounded_at_ten_per_call(pages_needed, complete):
    """Spec §13.2: at most 10 Telegram search pages per tool call."""
    fake = FakeSearch({"user:1": list(range(pages_needed, 0, -1))}, per_request=1)
    result = _one_page(["user:1"], fake)
    assert fake.rpcs == min(pages_needed, 10) and result.complete is complete
    if not complete:
        assert "rpc_budget" in result.coverage["partial_reasons"] and result.state is not None


def test_telegram_rpcs_and_hits_examined_are_measured_not_guessed():
    """Dialog checks are RPCs too, and a filtered-out entry was still examined."""

    async def fetch(peer, offset_id, want):
        return Page([View(9, "2026-09-23T10:09:00Z")], True, False, None, examined=5)

    result = asyncio.run(
        run_page(
            ["user:1"],
            EngineState(upper_date=UPPER),
            limit=10,
            fetch=fetch,
            since=None,
            projects={P: frozenset({"user:1"})},
            rpcs_used=lambda: 7,
        )
    )
    assert result.coverage["telegram_rpcs"] == 7 and result.coverage["hits_examined"] == 5


def test_an_owner_excluded_peer_leaves_the_eligible_set():
    universe = ["user:1", "user:2", "user:3"]
    fake = FakeSearch({p: [1] for p in universe}, excluded={"user:2"})
    result = _one_page(
        universe, fake, projects={P: frozenset(universe), "tpr_" + "b" * 26: frozenset({"user:2"})}
    )
    c = result.coverage
    assert (c["eligible_peers"], c["peers_scanned"], result.complete) == (2, 2, True)
    assert [(e["eligible_peers"], e["peers_scanned"]) for e in c["project_coverage"]] == [
        (2, 2),
        (0, 0),
    ]
    assert "user:2" not in fake.scanned


def test_an_unreachable_peer_stays_eligible_and_is_never_counted_scanned():
    universe = ["user:1", "user:2", "user:3"]
    fake = FakeSearch({p: [1] for p in universe}, unreachable={"user:3"})
    c = _one_page(universe, fake).coverage
    assert (c["eligible_peers"], c["peers_scanned"], c["complete"]) == (3, 2, False)
    assert c["partial_reasons"] == ["telegram_partial"]


def test_scanned_and_excluded_counts_survive_the_continuation():
    universe = [f"user:{i}" for i in range(5)]
    data = {p: [3, 2, 1] for p in universe}
    fake = FakeSearch(data, excluded={"user:1"})
    state, pages = EngineState(upper_date=UPPER), []
    while True:
        result = asyncio.run(
            run_page(
                universe,
                state,
                limit=2,
                fetch=fake.fetch,
                since=None,
                projects={P: frozenset(universe)},
            )
        )
        pages.append(result)
        if result.state is None:
            break
        state = result.state
    last = pages[-1].coverage
    assert len(pages) > 3 and pages[-1].complete is True
    assert (last["eligible_peers"], last["peers_scanned"]) == (4, 4)
    scanned = [p.coverage["peers_scanned"] for p in pages]
    assert scanned == sorted(scanned)  # cumulative, never re-counted
