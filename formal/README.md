# Bounded formal safety model (Appendix L)

Appendix L requires the release repository to contain "a bounded executable
state model" carrying fourteen named safety assertions, and says the exact
checker, bounds and configuration belong here and in `SECURITY-MANIFEST.json`.

## Checker

**No third-party checker is used.** TLA+/TLC and Hypothesis were both absent
from the build host, and the Phase-3 design left the choice to implementation
time. `formal/model.py` is therefore a self-contained checker: an explicit
finite state machine plus an exhaustive breadth-first exploration of every
reachable state.

That is a real check, not a sample. Every state variable is a bounded integer
or a flag, so the reachable set is finite and the search terminates having
visited all of it. Two properties are asserted:

1. every assertion holds in **every** reachable state; and
2. every assertion is **evaluated** at least once — an assertion that never
   fires is dead, not passing, and the suite fails on it.

The second check is the one that stops a model from proving nothing.

## Configuration

| Parameter | Value |
|---|---|
| Checker | `formal/model.py`, exhaustive BFS, pure Python 3.12 |
| Bound | `MAX_SEQ = 3` audit events per run |
| State variables | 20 — Appendix L's fifteen (with `budget_decision` in place of `consent_state`, comms spec v0.2) plus `anchor_epoch`, `anchor_seq`, `audit_integrity_state`, `append_guard_held`, `payload_released`; and 13 bookkeeping variables the assertions read |
| Assertions | 22 — Appendix L's fourteen less its two consent assertions, this architecture's four, and six owner-direct replacements |
| Reachable states | 544 |
| Runner | `uv run pytest tests/formal -q` |

## What the model found

The search was not a formality. Three real gaps surfaced before it went green,
and each is recorded because the fix is the interesting part:

1. **Revalidation alone does not stop a locked disclosure.** With step 8
   modelled but no reservation binding, the search released a payload
   authorised under an epoch a later lock had bumped.
2. **The window between step 8 and step 12 is closed by the reservation
   tuple, not by revalidation.** §23C.3 binds a reservation to
   `security_epoch`, and the model shows why: a lock is an external event
   that can land after the last authority check. This is exactly the
   component an earlier draft of the design had dropped.
3. **The append guard must exclude environment interleaving.** Steps 11 and
   12 run inside one critical section with no await between them. Modelled
   without that, the search interleaved a lock between the commit and the
   anchor refresh.

## Owner-direct revision (comms spec v0.2, 5b-3)

Consent is gone from the gateway, so `ConsentConsumedAtMostOnce` and
`NoReceiptWithoutVerifiedConsent` are retired. Six assertions replace them:
`OwnerDirectReceiptNeverClaimsConsent`, `ReceiptVersionsDistinguishable`,
`V2RequiresOwnerDirect`, `HardRefusalPrecedesRetrieval`,
`ReservationCommitsAtMostOnce` and `NoHandoffBeforeCommitAndAnchor`. The
budget tier is chosen nondeterministically at `consult`, and a retained v1
receipt is present in every initial state.

The search found one more gap on its first run. Without a `request_state ==
"frozen"` guard on `reserve`, a second reservation could be minted after
commit and carried into a later request whose consult said `refuse`, which
then reached `retrieve`. The implementation mints exactly one reservation per
call and releases it in `finally`. The model now says so too.

A predicate that holds everywhere proves something only if it can fail.
`tests/formal/test_invariant_mutations.py` breaks each new invariant's guard
in the model source and requires the search to report that invariant.

## Honest bounds

- The model abstracts one client and one project. Cross-project accounting is
  covered by the implementation suite, not here.
- `MAX_SEQ = 3` bounds chain length. Longer chains are covered by
  `tests/unit/test_audit_chain.py`, which appends 40 events under four-way
  contention.
- The model checks the *design*'s state machine. It does not verify the
  Python implementation line by line; the crash-injection suite does that.
