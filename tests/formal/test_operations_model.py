"""Run the admin-operation model exhaustively (comms v0.3 Task D35; design §E.2)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from formal.operations_model import PROPERTIES, State, explore, step


@pytest.fixture(scope="module")
def exploration():
    return explore()


def test_the_properties_are_the_design_list():
    assert set(PROPERTIES) == {
        "AtMostOneNonIdempotentEffect",
        "NormalSucceededIsDurableChainedAnchored",
        "DegradedSuccessGivesTheDegradedAnswer",
        "DegradedBlocksNewEffects",
        "MtprotoOneReissueOneVisibleEffect",
    }


def test_every_property_holds_in_every_reachable_state(exploration):
    visited, _hits, violations = exploration
    assert violations == []
    assert visited == 4728


def test_every_property_is_evaluated(exploration):
    _visited, hits, _violations = exploration
    assert all(hits[name] > 0 for name in PROPERTIES)


def test_a_set_state_retries_once_and_a_create_never():
    lost = State(retry_class="SET_STATE", record="OUTCOME_UNKNOWN", requests=1, effects=1)
    assert step(lost, ("admin", "request")).in_call  # one same-key retry
    create = State(retry_class="CREATE", record="OUTCOME_UNKNOWN", requests=1, effects=1)
    assert step(create, ("admin", "request")).answers == frozenset({"UNKNOWN"})


def test_the_degraded_latch_refuses_a_new_record():
    latched = step(State(), ("admin", "other_operation_degraded"))
    refused = step(latched, ("admin", "request"))
    assert refused.record == "NONE" and refused.answers == frozenset({"REFUSED_DEGRADED"})
