"""Each campaign-model property must be able to fail (comms 5b-4 design §11).

A property that holds on every reachable state proves something only if removing
the guard it protects makes the search find a violation.
"""

import sys
import types
from pathlib import Path

import pytest

MODEL = Path(__file__).resolve().parents[2] / "formal" / "campaign_model.py"

MUTATIONS = {
    "provider_failure_after_delivered": (
        'elif current and s in ("IN_FLIGHT", "ACCEPTED", "OUTCOME_UNKNOWN"):',
        'elif current and s in ("IN_FLIGHT", "ACCEPTED", "OUTCOME_UNKNOWN", "DELIVERED"):',
        "NeverBelowDeliveredOnceThere",
    ),
    "retry_an_unknown": (
        'elif evidence == "RETRY" and s == "FAILED_TRANSIENT"',
        'elif evidence == "RETRY" and s in ("FAILED_TRANSIENT", "OUTCOME_UNKNOWN")',
        "UnknownGainsAttemptOnlyAfterResolveNotSent",
    ),
    "reuse_a_generation": (
        "    gen = s.gens\n",
        "    gen = max(s.gens - 1, 0)\n",
        "OneKeyNamesOnePayload",
    ),
    "claim_while_scheduled": (
        'if job.state != "PENDING" or s.lifecycle != "SENDING":',
        'if job.state != "PENDING" or s.lifecycle not in ("SENDING", "SCHEDULED"):',
        "NoExecutionBeforeSendAt",
    ),
    "no_revalidation": (
        '        if not job.eligible:\n            return _settle(_set(s, i, reduce_job(job, "SKIP")))\n',
        "",
        "ExecutionSetWithinFrozenSet",
    ),
    "no_completion_with_the_last_job": (
        'if lifecycle == "SENDING" and not any(j.state in ACTIVE for j in s.jobs):',
        "if False:",
        "NeverStrandedInSending",
    ),
    "freeze_an_empty_generation": (
        "    if empty:\n        return None  # NO_ELIGIBLE_ENDPOINTS (R8)\n",
        "",
        "NoEmptyGeneration",
    ),
    "sent_on_any_success": (
        "    if wins == len(states):\n",
        "    if wins:\n",
        "SentOnlyIfEveryJobSucceeded",
    ),
    "unknown_is_failed": (
        '    if "OUTCOME_UNKNOWN" in states:\n        return "INDETERMINATE"\n',
        "",
        "UnknownNeverYieldsFailed",
    ),
    "no_pending_reconciliation": (
        "if done.bound and done.pending:",
        "if False:",
        "EarlyProviderUpdateNeverLost",
    ),
    "earlier_attempt_treated_as_current": (
        'after = reduce_job(job, "PROVIDER", "FAILED_PERMANENT", current=False)',
        'after = reduce_job(job, "PROVIDER", "FAILED_PERMANENT", current=True)',
        "EarlierAttemptFailureNeverTouchesTheJob",
    ),
}


def test_every_property_has_a_mutation():
    sys.path.insert(0, str(MODEL.parents[1]))
    from formal.campaign_model import PROPERTIES

    assert {invariant for _old, _new, invariant in MUTATIONS.values()} == set(PROPERTIES)


@pytest.mark.parametrize("case", sorted(MUTATIONS))
def test_breaking_the_guard_is_caught_by_its_property(case):
    old, new, invariant = MUTATIONS[case]
    source = MODEL.read_text(encoding="utf-8")
    assert source.count(old) == 1, "the mutation no longer matches the model"
    module = types.ModuleType(f"campaign_mutant_{case}")
    sys.modules[module.__name__] = module  # dataclasses resolve their module
    try:
        exec(compile(source.replace(old, new), module.__name__, "exec"), module.__dict__)  # noqa: S102
        _visited, _hits, violations = module.explore(stop_on=invariant)
    finally:
        del sys.modules[module.__name__]
    assert invariant in {name for name, _state in violations}
