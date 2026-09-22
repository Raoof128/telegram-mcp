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
