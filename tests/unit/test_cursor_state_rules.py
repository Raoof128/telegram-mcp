"""Design §3.4/§4.3: every cursor-state key has exactly one value rule."""

import pytest

from comms.transports.telegram.authority.cursors import (
    ALLOWED_STATE_KEYS,
    STATE_VALUE_RULES,
    _check_state,
)

REF = "tgp_" + "a" * 26
DIGEST = "hmac-sha256:" + "0" * 64


def test_each_allowed_key_has_exactly_one_rule():
    assert set(STATE_VALUE_RULES) == ALLOWED_STATE_KEYS
    assert set(STATE_VALUE_RULES.values()) <= {
        "int",
        "date",
        "peer_ref",
        "seen_ids",
        "per_peer",
        "hmac",
        "flag",
        "counts",
    }
    for key in (
        "upper_date",
        "universe_digest",
        "window_start",
        "next_unstarted_index",
        "uncertain",
        "scanned_counts",
        "excluded_counts",
    ):
        assert key in ALLOWED_STATE_KEYS


def test_a_search_window_state_validates():
    state = {
        "upper_date": "2026-09-23T00:00:00Z",
        "universe_digest": DIGEST,
        "window_start": 3,
        "next_unstarted_index": 67,
        "uncertain": 1,
        "scanned_counts": [3, 2, 1],
        "excluded_counts": [0, 0, 0],
        "per_peer": {REF: {"offset_id": 99}},
    }
    assert _check_state(state) == state


@pytest.mark.parametrize(
    "state",
    [
        {"universe_digest": "0" * 64},  # unlabelled
        {"universe_digest": "hmac-sha256:" + "Z" * 64},
        {"window_start": -1},
        {"window_start": True},
        {"uncertain": 2},  # a flag is 0 or 1
        {"uncertain": True},
        {"per_peer": {REF: {}}},  # an entry is exactly its offset
        {"scanned_counts": []},
        {"scanned_counts": [1] * 10},  # global + at most 8 projects
        {"excluded_counts": [-1]},
        {"excluded_counts": [True]},
        {"scanned_counts": "3"},
        {"upper_date": "2026-09-23"},
        {"per_peer": {REF: {"offset_id": 1, "exhausted": True}}},  # absence encodes exhaustion
        {"per_peer": {REF: {"page": 1}}},
        {"per_peer": {"tgm_" + "a" * 26: {"offset_id": 1}}},
        {"query": "secret words"},  # never content
    ],
)
def test_out_of_rule_state_is_refused(state):
    with pytest.raises(ValueError):
        _check_state(state)
