"""Run the campaign-core model exhaustively (comms 5b-4 design §11, R17)."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from formal.campaign_model import MODEL_RETRY_CAP, N_JOBS, PROPERTIES, explore


@pytest.fixture(scope="module")
def exploration():
    started = time.monotonic()
    visited, hits, violations = explore()
    return visited, hits, violations, time.monotonic() - started


EXPECTED = {
    "NeverBelowDeliveredOnceThere",
    "UnknownGainsAttemptOnlyAfterResolveNotSent",
    "OneKeyNamesOnePayload",
    "NoExecutionBeforeSendAt",
    "ExecutionSetWithinFrozenSet",
    "NeverStrandedInSending",
    "NoEmptyGeneration",
    "SentOnlyIfEveryJobSucceeded",
    "UnknownNeverYieldsFailed",
    "EarlyProviderUpdateNeverLost",
    "EarlierAttemptFailureNeverTouchesTheJob",
}


def test_every_reachable_state_satisfies_every_property(exploration):
    visited, _hits, violations, elapsed = exploration
    assert violations == [], violations[:3]
    assert 10_000 < visited <= 2_000_000
    assert elapsed < 60, elapsed
    print(
        f"\ncampaign model: explored {visited} reachable states, {len(PROPERTIES)} properties,"
        f" {N_JOBS} jobs, retry cap {MODEL_RETRY_CAP}, {elapsed:.1f} s"
    )


def test_the_properties_are_the_designed_set():
    assert set(PROPERTIES) == EXPECTED


def test_no_property_is_unreachable(exploration):
    visited, hits, _, _ = exploration
    assert [name for name, count in hits.items() if count == 0] == []
    assert all(count == visited for count in hits.values())
