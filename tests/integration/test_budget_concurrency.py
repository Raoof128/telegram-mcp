"""Two callers cannot both see the same remaining capacity (Gate P).

The daemon is single-process asyncio: concurrency here is coroutines, not
threads. That is not a convenience — a ``sqlite3`` connection is bound to the
thread that created it, so a thread-based test of this ledger fails with
"SQLite objects created in a thread can only be used in that same thread"
before it ever reaches the budget logic. The invariant that matters for this
architecture is that two interleaved coroutines cannot both reserve the last
capacity, and that is what is asserted.
"""

import asyncio

from comms.transports.telegram.disclosure.budget import (
    GLOBAL,
    BucketKey,
    BudgetError,
    BudgetLedger,
    Usage,
    subject_digest,
)
from comms.transports.telegram.keys.store import provision_missing
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.storage.migrations import migrate


async def test_concurrent_reservations_cannot_both_take_the_last_capacity(tmp_path):
    provision_missing(tmp_path / "keys", phases=(2,))
    conn = open_db(tmp_path / "meta.db")
    migrate(conn)
    ledger = BudgetLedger(conn)
    key = BucketKey(1, GLOBAL, subject_digest(GLOBAL))

    # The global hard ceiling is 1500 records. Two callers each want 800:
    # one must win, one must be refused. If both won, the ceiling would be
    # advisory rather than enforced.
    async def attempt(nonce: str) -> str:
        await asyncio.sleep(0)  # yield, so the two interleave
        try:
            ledger.reserve(
                client_id=1,
                security_epoch=1,
                project_scope_digest="d",
                binding_digest="c",
                request_nonce=nonce,
                worst_case={key: Usage(800, 0)},
                ttl_seconds=60,
            )
        except BudgetError:
            return "refused"
        return "reserved"

    outcomes = await asyncio.gather(attempt("n0"), attempt("n1"))

    assert sorted(outcomes) == ["refused", "reserved"]
    assert ledger.live_usage(key).records == 800


async def test_a_released_reservation_frees_capacity_for_the_next_caller(tmp_path):
    provision_missing(tmp_path / "keys", phases=(2,))
    conn = open_db(tmp_path / "meta.db")
    migrate(conn)
    ledger = BudgetLedger(conn)
    key = BucketKey(1, GLOBAL, subject_digest(GLOBAL))

    first = ledger.reserve(
        client_id=1,
        security_epoch=1,
        project_scope_digest="d",
        binding_digest="c",
        request_nonce="n0",
        worst_case={key: Usage(800, 0)},
        ttl_seconds=60,
    )
    ledger.release(first.reservation_ref)

    second = ledger.reserve(
        client_id=1,
        security_epoch=1,
        project_scope_digest="d",
        binding_digest="c",
        request_nonce="n1",
        worst_case={key: Usage(800, 0)},
        ttl_seconds=60,
    )
    assert second.reservation_ref != first.reservation_ref
    assert ledger.live_usage(key).records == 800
