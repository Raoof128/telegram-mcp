"""Each owner-direct invariant must be able to fail (5b-3 design §2.6).

A predicate that holds on every reachable state proves something only if
removing the guard it protects makes the search find a violation. Each case
below breaks one guard in the model's source and names the invariant that
must catch it.
"""

import sys
import types
from pathlib import Path

import pytest

MODEL = Path(__file__).resolve().parents[2] / "formal" / "model.py"

MUTATIONS = {
    "reserve_under_refuse": (
        'and s.budget_decision in ("normal", "elevated")\n        and s.reservation == 0',
        'and s.budget_decision != "none"\n        and s.reservation == 0',
        "HardRefusalPrecedesRetrieval",
    ),
    "commit_keeps_its_reservation": (
        "                disclosed=s.disclosed + 2,\n                reservation=0,\n",
        "                disclosed=s.disclosed + 2,\n",
        "ReservationCommitsAtMostOnce",
    ),
    "v2_claims_consent": (
        "receipt_claims_consent=False,\n                reservation_commits",
        "receipt_claims_consent=True,\n                reservation_commits",
        "OwnerDirectReceiptNeverClaimsConsent",
    ),
    "v2_is_not_owner_direct": (
        'receipt_mode="owner_direct",\n                receipt_claims',
        'receipt_mode="consent",\n                receipt_claims',
        "V2RequiresOwnerDirect",
    ),
    "commit_rewrites_v1": (
        "receipt_version=2,\n                receipt_mode",
        "receipt_version=2,\n                v1_version=2,\n                receipt_mode",
        "ReceiptVersionsDistinguishable",
    ),
    "release_before_commit": (
        'if s.audit_integrity_state == "ANCHOR_PENDING":',
        'if s.audit_integrity_state == "ANCHOR_PENDING" or s.request_state == "transformed":',
        "NoHandoffBeforeCommitAndAnchor",
    ),
}


@pytest.mark.parametrize("case", sorted(MUTATIONS))
def test_breaking_the_guard_is_caught_by_its_invariant(case):
    old, new, invariant = MUTATIONS[case]
    source = MODEL.read_text(encoding="utf-8")
    assert source.count(old) == 1, "the mutation no longer matches the model"
    module = types.ModuleType(f"formal_mutant_{case}")
    sys.modules[module.__name__] = module  # dataclasses resolve their module
    try:
        mutant = compile(source.replace(old, new), module.__name__, "exec")
        exec(mutant, module.__dict__)  # noqa: S102 -- our own model source, mutated on purpose
        _visited, _hits, violations = module.explore()
    finally:
        del sys.modules[module.__name__]
    assert invariant in {name for name, _state in violations}
