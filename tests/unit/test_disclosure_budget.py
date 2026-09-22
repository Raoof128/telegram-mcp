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
