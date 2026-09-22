"""Budget subjects, windows and dimensions (design §5.2; frozen spec §23C)."""

import pytest

from telegram_mcp.disclosure.budget import (
    GLOBAL,
    PROJECT,
    BucketKey,
    Usage,
    subject_digest,
    window_start,
)
from telegram_mcp.keys.store import provision_missing

_P = "tpr_" + "a" * 26


@pytest.fixture(autouse=True)
def _keys(tmp_path):
    provision_missing(tmp_path, phases=(2,))


def test_subject_digest_is_deterministic_keyed_hex():
    first = subject_digest(PROJECT, _P)
    assert first == subject_digest(PROJECT, _P)
    assert len(first) == 64 and first == first.lower()
    int(first, 16)


def test_the_two_dimensions_never_collide():
    assert subject_digest(GLOBAL) != subject_digest(PROJECT, _P)
    assert subject_digest(PROJECT, _P) != subject_digest(PROJECT, "tpr_" + "b" * 26)


def test_digest_carries_no_recoverable_ref():
    # The ref must not appear anywhere in the digest's text form.
    assert _P not in subject_digest(PROJECT, _P)


def test_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        subject_digest("vibes", _P)


def test_window_start_is_iso_z_and_moves_with_the_clock():
    early = window_start(1_800_000_000.0, 30)
    later = window_start(1_800_003_600.0, 30)
    assert early.endswith("Z") and "T" in early
    assert later > early


def test_usage_adds_componentwise():
    assert Usage(1, 10) + Usage(2, 20) == Usage(3, 30)


def test_bucket_key_is_hashable_for_use_as_a_dict_key():
    key = BucketKey(client_id=1, kind=GLOBAL, subject_digest=subject_digest(GLOBAL))
    assert {key: Usage(0, 0)}[key] == Usage(0, 0)


def test_catalogue_tools_charge_only_the_global_bucket():
    from telegram_mcp.disclosure.budget import GLOBAL, buckets_for

    data = {"projects": [{"project_ref": _P, "origin_project_refs": []}]}
    buckets = buckets_for("telegram_list_projects", data, client_id=1)

    assert len(buckets) == 1
    assert next(iter(buckets)).kind == GLOBAL


def test_three_projects_touch_four_buckets():
    from telegram_mcp.disclosure.budget import GLOBAL, PROJECT, buckets_for

    refs = ["tpr_" + c * 26 for c in "abc"]
    data = {
        "projects": [{"project_ref": r} for r in refs],
        "search_scope": {"mode": "cross_project"},
        "results": [
            {"message_ref": "tgm_" + "a" * 26, "origin_project_refs": [refs[0]]},
            {"message_ref": "tgm_" + "b" * 26, "origin_project_refs": [refs[1]]},
            {"message_ref": "tgm_" + "c" * 26, "origin_project_refs": [refs[2]]},
        ],
    }
    buckets = buckets_for("telegram_cross_project_search", data, client_id=1)

    assert len(buckets) == 4
    assert sum(1 for k in buckets if k.kind == PROJECT) == 3
    assert sum(1 for k in buckets if k.kind == GLOBAL) == 1


def test_a_shared_record_is_charged_once_globally_and_once_per_project():
    from telegram_mcp.disclosure.budget import GLOBAL, PROJECT, buckets_for

    a, b = "tpr_" + "a" * 26, "tpr_" + "b" * 26
    data = {
        "projects": [{"project_ref": a}, {"project_ref": b}],
        "search_scope": {"mode": "cross_project"},
        "results": [{"message_ref": "tgm_" + "a" * 26, "origin_project_refs": [a, b]}],
    }
    buckets = buckets_for("telegram_cross_project_search", data, client_id=1)

    glob = next(u for k, u in buckets.items() if k.kind == GLOBAL)
    projects = [u for k, u in buckets.items() if k.kind == PROJECT]
    assert glob.records == 1
    assert [u.records for u in projects] == [1, 1]
    # Deduplication at the retrieval layer must never reach accounting.
    assert sum(u.records for u in projects) == 2


def test_tiers_follow_the_frozen_baseline():
    from telegram_mcp.disclosure.budget import Thresholds, Usage, tier

    limits = Thresholds(
        soft_records=100, hard_records=500, soft_bytes=500_000, hard_bytes=5_000_000
    )

    assert tier(Usage(99, 0), limits) == "normal"
    assert tier(Usage(100, 0), limits) == "elevated"  # at the soft threshold
    assert tier(Usage(499, 0), limits) == "elevated"
    assert tier(Usage(500, 0), limits) == "refuse"  # at the hard threshold
    assert tier(Usage(0, 500_000), limits) == "elevated"
    assert tier(Usage(0, 5_000_000), limits) == "refuse"


def test_either_quantity_alone_can_refuse():
    from telegram_mcp.disclosure.budget import Thresholds, Usage, tier

    limits = Thresholds(
        soft_records=100, hard_records=500, soft_bytes=500_000, hard_bytes=5_000_000
    )
    assert tier(Usage(0, 5_000_001), limits) == "refuse"
    assert tier(Usage(501, 0), limits) == "refuse"


def test_thresholds_are_read_from_the_settings_registry(tmp_path):
    from telegram_mcp.disclosure.budget import GLOBAL, PROJECT, thresholds_for
    from telegram_mcp.storage.db import open_db
    from telegram_mcp.storage.migrations import migrate

    conn = open_db(tmp_path / "meta.db")
    migrate(conn)

    assert thresholds_for(conn, PROJECT).hard_records == 500
    assert thresholds_for(conn, GLOBAL).hard_records == 1_500
    assert thresholds_for(conn, GLOBAL).hard_bytes == 15_000_000


def _ledger(tmp_path):
    from telegram_mcp.disclosure.budget import BudgetLedger
    from telegram_mcp.storage.db import open_db
    from telegram_mcp.storage.migrations import migrate

    conn = open_db(tmp_path / "meta.db")
    migrate(conn)
    return BudgetLedger(conn)


def _worst(client_id=1, records=10, size=1000):
    from telegram_mcp.disclosure.budget import GLOBAL, BucketKey, Usage, subject_digest

    return {BucketKey(client_id, GLOBAL, subject_digest(GLOBAL)): Usage(records, size)}


def test_reservation_binds_the_six_frozen_components(tmp_path):
    ledger = _ledger(tmp_path)
    reservation = ledger.reserve(
        client_id=1,
        security_epoch=4,
        project_scope_digest="hmac-sha256:" + "0" * 64,
        consent_challenge_digest="1" * 64,
        request_nonce="n" * 32,
        worst_case=_worst(),
        ttl_seconds=60,
    )
    # §23C.3 freezes exactly these; dropping security_epoch or the challenge
    # digest would let an emergency lock or a different approval be ignored.
    assert reservation.client_id == 1
    assert reservation.security_epoch == 4
    assert reservation.project_scope_digest == "hmac-sha256:" + "0" * 64
    assert reservation.consent_challenge_digest == "1" * 64
    assert reservation.request_nonce == "n" * 32
    assert reservation.expires_at > 0
    assert reservation.reservation_ref


def test_a_live_reservation_counts_against_the_next_consultation(tmp_path):
    from telegram_mcp.disclosure.budget import GLOBAL, BucketKey, subject_digest

    ledger = _ledger(tmp_path)
    key = BucketKey(1, GLOBAL, subject_digest(GLOBAL))
    assert ledger.live_usage(key).records == 0

    ledger.reserve(
        client_id=1,
        security_epoch=1,
        project_scope_digest="d",
        consent_challenge_digest="c",
        request_nonce="n",
        worst_case=_worst(records=7),
        ttl_seconds=60,
    )
    assert ledger.live_usage(key).records == 7


def test_release_frees_the_reservation(tmp_path):
    from telegram_mcp.disclosure.budget import GLOBAL, BucketKey, subject_digest

    ledger = _ledger(tmp_path)
    key = BucketKey(1, GLOBAL, subject_digest(GLOBAL))
    reservation = ledger.reserve(
        client_id=1,
        security_epoch=1,
        project_scope_digest="d",
        consent_challenge_digest="c",
        request_nonce="n",
        worst_case=_worst(records=7),
        ttl_seconds=60,
    )
    ledger.release(reservation.reservation_ref)
    assert ledger.live_usage(key).records == 0


def test_an_expired_reservation_stops_counting(tmp_path):
    from telegram_mcp.disclosure.budget import GLOBAL, BucketKey, BudgetLedger, subject_digest
    from telegram_mcp.storage.db import open_db
    from telegram_mcp.storage.migrations import migrate

    now = [1_800_000_000.0]
    conn = open_db(tmp_path / "meta.db")
    migrate(conn)
    ledger = BudgetLedger(conn, clock=lambda: now[0])
    key = BucketKey(1, GLOBAL, subject_digest(GLOBAL))

    ledger.reserve(
        client_id=1,
        security_epoch=1,
        project_scope_digest="d",
        consent_challenge_digest="c",
        request_nonce="n",
        worst_case=_worst(records=7),
        ttl_seconds=60,
    )
    assert ledger.live_usage(key).records == 7
    now[0] += 61
    assert ledger.live_usage(key).records == 0


def test_reserving_past_a_hard_ceiling_refuses(tmp_path):
    import pytest

    from telegram_mcp.disclosure.budget import BudgetError

    ledger = _ledger(tmp_path)
    with pytest.raises(BudgetError):
        ledger.reserve(
            client_id=1,
            security_epoch=1,
            project_scope_digest="d",
            consent_challenge_digest="c",
            request_nonce="n",
            worst_case=_worst(records=2_000),  # global hard ceiling is 1500
            ttl_seconds=60,
        )
