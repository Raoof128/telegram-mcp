"""comms v0.3 Task C30: rate limits normalized; no hidden long sleeps (A21)."""

import pytest

from comms.core.delivery.transport import DeliveryResult, ResultKind
from comms.core.providers.protocols import ProviderResult
from comms.core.providers.ratelimit import (
    MAX_INLINE_WAIT_S,
    RateLimited,
    rate_limit_of,
    with_rate_limit,
)
from comms.core.providers.semantics import OperationSemantics

SET_STATE = OperationSemantics("SET_STATE", "natural", "retry_same_key")
CREATE = OperationSemantics("CREATE", "none", "resolve_only")
DESTROY = OperationSemantics("DESTRUCTIVE_NONIDEMPOTENT", "none", "resolve_only")
OK = ProviderResult("SUCCEEDED", None)


def limited(seconds):
    return ProviderResult(
        "FAILED", "RATE_LIMITED", detail={"retry_after": seconds} if seconds is not None else {}
    )


class Calls:
    def __init__(self, *results):
        self.results, self.count = list(results), 0

    def __call__(self):
        self.count += 1
        return self.results.pop(0)


def test_every_adapter_shape_normalizes():
    assert rate_limit_of(limited(7)) == RateLimited(7)  # bot 429, MTProto FLOOD_WAIT (admin)
    assert rate_limit_of(limited(None)) == RateLimited(None)  # a Meta throttle without a hint
    assert rate_limit_of(
        DeliveryResult(ResultKind.FAILED_TRANSIENT, retry_after=17)
    ) == RateLimited(17)
    assert (
        rate_limit_of(DeliveryResult(ResultKind.FAILED_TRANSIENT)) is None
    )  # unsent, not throttled
    assert (
        rate_limit_of(OK) is None
        and rate_limit_of(ProviderResult("FAILED", "NOT_AUTHORIZED")) is None
    )


@pytest.mark.parametrize("semantics", [SET_STATE, CREATE, DESTROY])
def test_flood_wait_never_success(semantics):
    sleeps = []
    result = with_rate_limit(Calls(limited(1), limited(1)), semantics, sleep=sleeps.append)
    assert result.outcome == "FAILED" and result.code == "RATE_LIMITED"


@pytest.mark.parametrize("seconds", [MAX_INLINE_WAIT_S + 1, 30, 86400, None])
def test_long_wait_returned_not_slept(seconds):
    sleeps, calls = [], Calls(limited(seconds))
    result = with_rate_limit(calls, SET_STATE, sleep=sleeps.append)
    assert (result.code, result.detail.get("retry_after"), sleeps, calls.count) == (
        "RATE_LIMITED",
        seconds,
        [],
        1,
    )


def test_short_wait_slept_once_then_single_retry_only_for_set_state():
    sleeps, calls = [], Calls(limited(2), OK)
    assert with_rate_limit(calls, SET_STATE, sleep=sleeps.append) == OK
    assert (sleeps, calls.count) == ([2], 2)
    sleeps, calls = [], Calls(limited(1), limited(1), OK)
    assert with_rate_limit(calls, SET_STATE, sleep=sleeps.append).code == "RATE_LIMITED"
    assert (sleeps, calls.count) == ([1], 2)  # one retry, never a loop
    for semantics in (CREATE, DESTROY):
        sleeps, calls = [], Calls(limited(1), OK)
        assert with_rate_limit(calls, semantics, sleep=sleeps.append).code == "RATE_LIMITED"
        assert (sleeps, calls.count) == ([], 1)  # a non-idempotent call is never retried here


def test_a_non_limited_result_passes_through_untouched():
    sleeps = []
    for result in (
        OK,
        ProviderResult("OUTCOME_UNKNOWN", None),
        ProviderResult("FAILED", "NOT_AUTHORIZED"),
    ):
        assert with_rate_limit(Calls(result), SET_STATE, sleep=sleeps.append) is result
    assert sleeps == []
