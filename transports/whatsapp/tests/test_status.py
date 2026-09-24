from whatsvault.ingest import status as S


def test_empty_is_unknown():
    assert S.reduce_status([]) == {
        "delivery_rank": 0,
        "failed_at_ms": None,
        "deleted_at_ms": None,
        "unknown_statuses": [],
    }


def test_rank_is_max_of_success_events():
    ev = [{"status": s, "provider_ts_ms": i} for i, s in enumerate(["sent", "delivered", "read"], 1)]
    assert S.reduce_status(ev)["delivery_rank"] == 3


def test_late_sent_after_read_does_not_downgrade():
    ev = [{"status": "read", "provider_ts_ms": 3}, {"status": "sent", "provider_ts_ms": 99}]
    assert S.reduce_status(ev)["delivery_rank"] == 3


def test_failed_and_deleted_are_orthogonal_and_earliest():
    ev = [
        {"status": "read", "provider_ts_ms": 3},
        {"status": "failed", "provider_ts_ms": 9},
        {"status": "failed", "provider_ts_ms": 5},
        {"status": "deleted", "provider_ts_ms": 7},
    ]
    out = S.reduce_status(ev)
    assert out["delivery_rank"] == 3
    assert out["failed_at_ms"] == 5
    assert out["deleted_at_ms"] == 7


def test_unknown_statuses_are_surfaced():
    out = S.reduce_status([{"status": "warp_speed", "provider_ts_ms": 1}])
    assert out["unknown_statuses"] == ["warp_speed"]
    assert out["delivery_rank"] == 0
