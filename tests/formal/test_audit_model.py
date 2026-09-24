"""Run the audit-chain model exhaustively (comms v0.3 Task B31, design §E.2)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from formal.audit_model import MAX_EPOCH, MAX_TIME, PER_EPOCH, PROPERTIES, explore

EXPECTED = {
    "Contiguity",
    "EveryNonFinalEpochSealed",
    "TruncationOnlyAtRootBeforeCutoff",
    "NoAppendAfterSealed",
    "LineageEqualOrFailClosed",
    "VerifyAcceptsExactlyLegitimate",
}


@pytest.fixture(scope="module")
def exploration():
    return explore()


def test_the_properties_are_the_design_list():
    assert set(PROPERTIES) == EXPECTED
    assert (MAX_EPOCH, PER_EPOCH, MAX_TIME) == (3, 2, 1)


def test_every_property_holds_in_every_reachable_state(exploration):
    visited, _hits, violations = exploration
    assert violations == []
    assert visited == 215_040


def test_every_property_is_evaluated(exploration):
    _visited, hits, _violations = exploration
    assert all(hits[name] > 0 for name in PROPERTIES)
