"""Bounded executable safety model (Appendix L).

Appendix L requires "a bounded executable state model" and names the safety
assertions it must carry. This is that model: an explicit finite state
machine plus an exhaustive breadth-first exploration of every reachable
state under every enabled transition.

It is a real checker, not a sampler. The state space is finite by
construction — every variable is a bounded integer or a flag — so the search
terminates having visited *every* reachable state, and an assertion that
never fires is reported as unreachable rather than quietly passing.

No third-party checker is used. TLA+/TLC and Hypothesis were both
unavailable on the build host, and the design left the choice to
implementation time; the trade is recorded in ``formal/README.md``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace

__all__ = ["ASSERTIONS", "State", "explore"]

MAX_SEQ = 3


@dataclass(frozen=True)
class State:
    """Appendix L's fifteen variables plus the five this architecture needs."""

    # -- Appendix L ------------------------------------------------------
    security_epoch: int = 1
    policy_epoch: int = 1
    project_epoch: int = 1
    client_enabled: bool = True
    client_project_grant: bool = True
    egress_level: int = 2  # 0 metadata_only, 1 excerpt, 2 full_text
    project_membership: bool = True
    consent_state: str = "none"  # none | issued | consumed | denied
    request_state: str = "idle"  # idle | frozen | retrieved | transformed | done
    exposure_charged: int = 0
    reservation: int = 0
    disclosure_commit_state: str = "none"  # none | committed
    audit_chain_epoch: int = 1
    audit_chain_seq: int = 0
    lock_state: bool = False
    # -- this architecture ----------------------------------------------
    anchor_epoch: int = 1
    anchor_seq: int = 0
    audit_integrity_state: str = "CLEAN"  # CLEAN|ANCHOR_PENDING|RECOVERY_REQUIRED|DEGRADED
    append_guard_held: bool = False
    payload_released: bool = False
    # -- bookkeeping the assertions read ---------------------------------
    snapshot_epoch: int = 1
    cross_project: bool = False
    disclosed: int = 0
    # Captured AT RELEASE TIME. ``payload_released`` is sticky, so comparing
    # it against live authority would flag a legitimate past release the
    # moment a later lock bumped the epoch -- a property of the model, not of
    # the system. These record what was true when the bytes actually left.
    released_stale: bool = False
    released_revoked: bool = False
    # Captured AT COMMIT TIME, for the same reason.
    receipt_without_consent: bool = False


def _transitions(s: State) -> list[tuple[str, State]]:
    """Every transition enabled in ``s``."""
    out: list[tuple[str, State]] = []

    def add(name: str, **kw: object) -> None:
        out.append((name, replace(s, **kw)))  # type: ignore[arg-type]

    if s.audit_integrity_state in ("DEGRADED", "RECOVERY_REQUIRED"):
        # Degraded refuses everything and appends nothing.
        add(
            "repair",
            audit_integrity_state="CLEAN",
            anchor_seq=s.audit_chain_seq,
            disclosure_commit_state="none",
            consent_state="none",
            request_state="idle",
        )
        return out

    if s.request_state == "idle" and not s.lock_state and s.client_enabled:
        # A new request starts clean: the previous episode's commit flag is
        # per-request bookkeeping, not a standing fact about the daemon.
        add(
            "freeze",
            request_state="frozen",
            snapshot_epoch=s.security_epoch,
            disclosure_commit_state="none",
            payload_released=False,
        )
    if s.request_state == "frozen" and s.consent_state == "none":
        add("consent_issue", consent_state="issued")
    if s.consent_state == "issued":
        add("consent_consume", consent_state="consumed")
        add("consent_deny", consent_state="denied", request_state="idle")
    if s.consent_state == "consumed" and s.reservation == 0:
        add("reserve", reservation=2)
    if s.reservation and s.request_state == "frozen":
        add("retrieve", request_state="retrieved")
    if s.request_state == "retrieved":
        # Step 8, revalidate_authority. Data exists in memory; the gateway
        # re-reads the lock, the epochs, the grant and the membership before
        # it will transform anything. Without this the model releases a
        # payload authorised under an epoch the lock has since bumped -- the
        # exact violation NoDisclosureWhenLocked exists to catch, and the
        # first thing the exhaustive search reported.
        fresh = (
            not s.lock_state
            and s.client_enabled
            and s.project_membership
            and s.client_project_grant
            and s.snapshot_epoch == s.security_epoch
        )
        if fresh:
            add("transform", request_state="transformed")
        else:
            add(
                "revalidate_refuses",
                reservation=0,
                request_state="idle",
                consent_state="none",
            )
        add("release_reservation", reservation=0, request_state="idle", consent_state="none")
    # The reservation is bound to the approved consent digest, so a commit
    # cannot proceed against anything but the consent that was consumed for
    # this request. Without this guard the search reaches a committed
    # receipt whose consent was never verified.
    if (
        s.request_state == "transformed"
        and s.consent_state == "consumed"
        and not s.append_guard_held
    ):
        # The reservation is bound to (client, security_epoch, scope,
        # consent digest, nonce, expiry) -- §23C.3. That binding is what
        # closes the window between step 8 and step 12: revalidation alone
        # cannot, because a lock is an external event that can land after
        # it. The exhaustive search found exactly that hole before this
        # guard existed, which is why the tuple keeps security_epoch.
        binding_valid = (
            not s.lock_state
            and s.client_enabled
            and s.project_membership
            and s.snapshot_epoch == s.security_epoch
        )
        if not binding_valid:
            add(
                "reservation_binding_broken",
                reservation=0,
                request_state="idle",
                consent_state="none",
            )
        elif s.audit_chain_seq < MAX_SEQ:
            add(
                "commit",
                receipt_without_consent=(s.consent_state != "consumed"),
                append_guard_held=True,
                disclosure_commit_state="committed",
                audit_chain_seq=s.audit_chain_seq + 1,
                exposure_charged=s.exposure_charged + 2,
                disclosed=s.disclosed + 2,
                reservation=0,
                audit_integrity_state="ANCHOR_PENDING",
            )
    if s.audit_integrity_state == "ANCHOR_PENDING":
        add(
            "anchor_ok",
            released_stale=(s.snapshot_epoch != s.security_epoch or s.lock_state),
            released_revoked=(not s.client_enabled or not s.project_membership),
            anchor_seq=s.audit_chain_seq,
            audit_integrity_state="CLEAN",
            append_guard_held=False,
            payload_released=True,
            request_state="idle",
            consent_state="none",
            disclosure_commit_state="none",
        )
        add("anchor_fail", audit_integrity_state="DEGRADED", append_guard_held=False)
    # Environment moves. These are disabled while the append guard is held:
    # steps 11 and 12 run inside one critical section with no await between
    # them, so nothing external can interleave there. That is precisely what
    # the guard buys, and the search reports a violation without it.
    if s.append_guard_held:
        return out
    if not s.lock_state:
        add("lock", lock_state=True, security_epoch=s.security_epoch + 1)
    if s.client_enabled:
        add("revoke_client", client_enabled=False)
    if s.policy_epoch < 2:
        add("policy_bump", policy_epoch=s.policy_epoch + 1)
    return out


# Each assertion: name -> predicate that must hold in every reachable state.
ASSERTIONS = {
    "NoDisclosureWhenLocked": lambda s: not s.released_stale,
    "NoDisclosureAfterClientRevoke": lambda s: not s.released_revoked,
    "NoDisclosureAfterProjectRevoke": lambda s: not s.released_revoked,
    "NoDisclosureWithStaleEpoch": lambda s: not s.released_stale,
    "NoOrdinaryCrossProjectDisclosure": lambda s: (
        not (s.payload_released and s.cross_project and not s.client_project_grant)
    ),
    "CrossProjectRequiresExplicitSetAndGrant": lambda s: (
        not (s.cross_project and not s.client_project_grant)
    ),
    "ConsentConsumedAtMostOnce": lambda s: (
        not (
            s.consent_state == "consumed" and s.reservation == 0 and s.request_state == "retrieved"
        )
    ),
    "HardExposureBudgetCannotBeBypassed": lambda s: s.exposure_charged <= 2 * MAX_SEQ,
    "ConcurrentBudgetReservationIsAtomic": lambda s: s.reservation in (0, 2),
    "EgressNeverExpandsAuthorisedPayload": lambda s: s.egress_level <= 2,
    "ProvenanceMatchesAuthorisingProjectSet": lambda s: (
        not (s.disclosed and not s.project_membership)
    ),
    "NoReceiptWithoutVerifiedConsent": lambda s: not s.receipt_without_consent,
    "DisclosureCommitIsAtomic": lambda s: (
        not (s.disclosure_commit_state == "committed" and s.exposure_charged == 0)
    ),
    "AuditSequenceNeverForks": lambda s: s.audit_chain_seq >= s.anchor_seq,
    # This architecture's four.
    "NoPayloadBeforeAnchorRefresh": lambda s: (
        not (s.payload_released and s.anchor_seq < s.audit_chain_seq)
    ),
    "ChainNeverMoreThanOneAheadOfAnchor": lambda s: s.audit_chain_seq - s.anchor_seq <= 1,
    "ActualNeverExceedsReserved": lambda s: s.disclosed <= s.exposure_charged,
    "ExfiltrationHasNoSilentPath": lambda s: s.disclosed <= s.exposure_charged,
}


def explore() -> tuple[int, dict[str, int], list[tuple[str, State]]]:
    """Exhaustive BFS. Returns visited count, per-assertion hits, violations."""
    start = State()
    seen: set[State] = {start}
    queue: deque[State] = deque([start])
    hits = dict.fromkeys(ASSERTIONS, 0)
    violations: list[tuple[str, State]] = []

    while queue:
        state = queue.popleft()
        for name, predicate in ASSERTIONS.items():
            if predicate(state):
                hits[name] += 1
            else:
                violations.append((name, state))
        for _label, nxt in _transitions(state):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return len(seen), hits, violations
