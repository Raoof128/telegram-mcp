"""Run the key and credential rotation model exhaustively (comms v0.3 Task B32)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from formal.keys_model import PROPERTIES, explore, recover


@pytest.fixture(scope="module")
def exploration():
    return explore()


def test_the_properties_are_the_plan_list():
    assert set(PROPERTIES) == {
        "ExactlyOneKeyOpensAndRecoveryFindsIt",
        "FailedCandidateNeverActive",
        "OldDestroyedOnlyAfterVerifiedReopen",
    }


def test_every_property_holds_in_every_reachable_state(exploration):
    visited, _hits, violations = exploration
    assert violations == []
    assert visited == 512


def test_every_property_is_evaluated(exploration):
    _visited, hits, _violations = exploration
    assert all(hits[name] > 0 for name in PROPERTIES)


def test_recovery_is_a_trial_open_not_a_guess():
    from formal.keys_model import State

    after_rekey = State(stored=frozenset({1, 2}), pointer=1, file_key=2, phase="rekeyed", new=2)
    assert recover(after_rekey) == 2
    assert recover(State(stored=frozenset({1}), pointer=1, file_key=2)) is None
