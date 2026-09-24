"""Monotonic delivery-rank lattice with orthogonal failure/deletion (spec §3.7).
Arrival order is irrelevant; rank is MAX over success events. failed_at_ms and
deleted_at_ms record the EARLIEST provider timestamp. Unknown statuses are
surfaced for diagnosis rather than silently ignored."""

RANK = {"sent": 1, "delivered": 2, "read": 3}
_KNOWN = set(RANK) | {"failed", "deleted"}


def reduce_status(events: list[dict]) -> dict:
    rank = 0
    failed_at = None
    deleted_at = None
    unknown: set[str] = set()
    for e in events:
        s = e["status"]
        ts = e["provider_ts_ms"]
        if s in RANK:
            rank = max(rank, RANK[s])
        elif s == "failed":
            failed_at = ts if failed_at is None else min(failed_at, ts)
        elif s == "deleted":
            deleted_at = ts if deleted_at is None else min(deleted_at, ts)
        else:
            unknown.add(s)
    return {
        "delivery_rank": rank,
        "failed_at_ms": failed_at,
        "deleted_at_ms": deleted_at,
        "unknown_statuses": sorted(unknown),
    }
