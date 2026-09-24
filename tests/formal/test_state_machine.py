"""Run the bounded model exhaustively (Appendix L, Gate Q)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from formal.model import ASSERTIONS, explore


def test_every_reachable_state_satisfies_every_assertion():
    visited, _hits, violations = explore()

    assert violations == [], violations[:3]
    assert visited > 100, f"state space too small to be meaningful: {visited}"
    print(f"\nexplored {visited} reachable states, {len(ASSERTIONS)} assertions")


def test_no_assertion_is_unreachable():
    """An assertion that never evaluates is not passing -- it is dead.

    This is the check that stops a model from proving nothing: every
    predicate must actually be exercised by at least one reachable state.
    """
    visited, hits, _ = explore()

    dead = [name for name, count in hits.items() if count == 0]
    assert dead == [], f"assertions never evaluated: {dead}"
    for name, count in sorted(hits.items()):
        print(f"  {name:42} {count}/{visited}")


KEPT = {
    "NoDisclosureWhenLocked",
    "NoDisclosureAfterClientRevoke",
    "NoDisclosureAfterProjectRevoke",
    "NoDisclosureWithStaleEpoch",
    "NoOrdinaryCrossProjectDisclosure",
    "CrossProjectRequiresExplicitSetAndGrant",
    "HardExposureBudgetCannotBeBypassed",
    "ConcurrentBudgetReservationIsAtomic",
    "EgressNeverExpandsAuthorisedPayload",
    "ProvenanceMatchesAuthorisingProjectSet",
    "DisclosureCommitIsAtomic",
    "AuditSequenceNeverForks",
    "NoPayloadBeforeAnchorRefresh",
    "ChainNeverMoreThanOneAheadOfAnchor",
    "ActualNeverExceedsReserved",
    "ExfiltrationHasNoSilentPath",
}
# comms spec v0.2 (5b-3 design §2.6): consent's two assertions are replaced.
REPLACEMENTS = {
    "OwnerDirectReceiptNeverClaimsConsent",
    "ReceiptVersionsDistinguishable",
    "V2RequiresOwnerDirect",
    "HardRefusalPrecedesRetrieval",
    "ReservationCommitsAtMostOnce",
    "NoHandoffBeforeCommitAndAnchor",
}


def test_the_assertion_set_is_the_owner_direct_set():
    assert set(ASSERTIONS) == KEPT | REPLACEMENTS
    assert not {"ConsentConsumedAtMostOnce", "NoReceiptWithoutVerifiedConsent"} & set(ASSERTIONS)


def test_the_model_has_no_consent_protocol():
    from formal.model import State, _transitions

    assert "consent_state" not in State.__dataclass_fields__
    labels: set[str] = set()
    frontier, seen = [State()], {State()}
    while frontier:
        for label, nxt in _transitions(frontier.pop()):
            labels.add(label)
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert not [label for label in labels if "consent" in label]
    assert {"consult_refuse", "hard_refusal", "reserve", "commit"} <= labels
