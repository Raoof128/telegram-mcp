# Telegram MCP Phase 3 Design — Disclosure, Budgets, Proofs and Audit Integrity

**Status:** Reviewed through two gauntlet passes; ready for the three implementation plans.
**Date:** 2026-09-22 (Australia/Sydney)
**Spec:** `telegram-mcp-v0.1.10-final-engineering-spec.md` (SHA-256 `36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a`)
**Roadmap:** [release roadmap](../plans/2026-09-22-telegram-mcp-release-roadmap.md), Phase 3 row
**Basis:** Phases 1 and 2 complete on `main`; 484 tests, 41 end-to-end smoke checks, the Phase-2J join gate passing against the real consent agent.

## Controlling statement

**Phase 3 does not change the public MCP tool catalogue.** The ten tools, their
argument schemas and their result schemas are frozen from Phase 1 and stay
frozen. What changes is the disclosure semantics underneath every sensitive
tool: what must happen before a byte leaves, what is durably accounted, and
what the gateway attests afterwards.

**No tool may bypass the disclosure seam.** A tool slice does not call
retrieval and serialisation directly; it asks the coordinator, and the
coordinator is the only thing that can release a sensitive payload. Phase 4
adds real Telegram slices behind this rule, and a slice that reaches around it
is a defect, not an optimisation.

## 1. Architecture

```text
src/telegram_mcp/disclosure/
  coordinator.py   DISCLOSURE_STEPS, the coordinator, injectable seams
  egress.py        metadata_only | excerpt | full_text transformation
  provenance.py    origin_project_refs, result-provenance and coverage digests
  receipts.py      Appendix-K payload, JCS bytes, Ed25519 sign, offline verify
  budget.py        reservation lock, rolling windows, the two budget dimensions
  audit/chain.py   event MAC, BEGIN IMMEDIATE append, checkpoints
  audit/anchor.py  0600 file anchor: read, verify, refresh, degraded latch
```

`SyntheticDisclosureGate` is replaced by the real coordinator. The Phase-2
`DisclosureGate` protocol stays as the seam so Phase 4 has exactly one thing to
call.

The coordinator **sequences**; it does not decide. Authority decisions stay in
`authority/`, consent decisions in `consent/`, budget decisions in `budget.py`,
integrity decisions in `audit/`. A second policy engine inside the coordinator
would be the failure this design exists to prevent.

## 2. The disclosure transaction

Every sensitive call runs this order. The names are frozen as a declaration
the tests assert against; production calls typed functions explicitly rather
than dispatching over the tuple, because a security protocol must not be a
dynamically dispatchable plugin chain.

```python
DISCLOSURE_STEPS = (
    "freeze_arguments",
    "snapshot_authority",
    "estimate_exposure",
    "consent_issue",
    "consent_consume",
    "reserve_budget",
    "retrieve",
    "revalidate_authority",
    "transform_egress",
    "measure_and_prepare_proof",
    "commit_disclosure",
    "refresh_anchor",
)
```

| # | Step | Produces / enforces |
|---|---|---|
| 1 | `freeze_arguments` | canonical request bytes, `canonical_request_hmac` |
| 2 | `snapshot_authority` | epochs, scope digest, effective egress ceiling |
| 3 | `estimate_exposure` | conservative worst case from validated limits; `exposure_snapshot_digest`; soft/hard tier for the prompt |
| 4 | `consent_issue` | challenge binding that digest and the budget state shown |
| 5 | `consent_consume` | agent-signed approval, exact-once |
| 6 | `reserve_budget` | under one lock: recompute buckets, re-derive the snapshot digest, compare to the approved one, reserve the worst case — or refuse |
| 7 | `retrieve` | adapter call; the 15-second execution deadline starts here |
| 8 | `revalidate_authority` | re-read lock, epochs, grants, memberships, peer authorisation |
| 9 | `transform_egress` | the actual content class present in the response |
| 10 | `measure_and_prepare_proof` | actual records, actual bytes, actual egress class, provenance digest, coverage digest, signed receipt material |
| 11 | `commit_disclosure` | one transaction: ledger rows, receipt row, exactly one audit append |
| 12 | `refresh_anchor` | external head anchor — the last gate before release |

### Two barriers

```text
steps 1-6   │ nothing has been retrieved
════════════╪══ SECURITY BARRIER ═══════════════════════════════
steps 7-12  │ data exists in memory, nothing has been released
════════════╪══ DISCLOSURE BARRIER ═════════════════════════════
            │ payload may cross the process boundary
```

**Normative:** no sensitive result bytes may be emitted, yielded, streamed,
flushed or returned to an MCP transport before `refresh_anchor` succeeds.
Every sensitive response is fully materialised in memory before step 12. This
forecloses the shortcut where a later async generator starts streaming content
while the audit transaction is still finishing.

Step 10 prepares a signed receipt; step 11 makes it authoritative. The
distinction is not pedantry: a receipt that exists before its commit is a
claim about a disclosure that has not happened. No Appendix-K field depends on
the audit chain head, so nothing in the payload needs finalising inside step
11; if that ever changes, the dependent field moves into step 11 rather than
the chain head moving out.

### Crash model

| Crash point | Durable state | Payload | Recovery |
|---|---|---|---|
| before step 11 | none | none | nothing to do; unreserved means undisclosed |
| between 11 and 12 | accounted, chain at most one event ahead of the anchor | withheld | `audit verify` then user-presence `audit repair-anchor` |
| after step 12 | accounted and anchored | released | none needed |

Once sensitive bytes are handed to the transport after a successful anchor,
the exposure is conservatively treated as disclosed even if delivery
acknowledgement never arrives. A privacy budget is not refunded because a
client disconnected at an awkward moment.

## 3. Egress and provenance (spec §23B)

Egress transformation runs after retrieval and revalidation and before
accounting, signing and serialisation.

- `metadata_only` — `text=null`, no excerpt, `text_truncated=false`.
- `excerpt` — text truncated to the grant's `excerpt_max_codepoints`, counted
  in codepoints, `text_truncated=true` when the source was longer.
- `full_text` — the bounded result rules from Phase 1.

For a record authorised through several selected projects, the effective
profile is the most restrictive and the smallest excerpt limit wins.
`effective_egress_level` in the receipt is the **actual maximum content class
present in this response**, not the grant's ceiling: a response that happens to
carry only metadata is `metadata_only` even under a `full_text` grant.

**Message text is never sanitised.** The consent-display sanitiser from Phase
2b strips C0/C1 and bidi controls for a prompt string; running it over a
Telegram message body would corrupt the evidence the model is reading.
Transformation preserves logical Unicode exactly, and the only content change
is explicit excerpt truncation. Determinism is tested against a Unicode/RTL
corpus.

Provenance binds **what leaves**, not what Telegram returned:
`canonical_result_provenance_digest` commits to the ordered set of privacy-safe
record identities, `origin_project_refs`, effective egress level and truncation
state, computed after transformation. For `excerpt` it proves the excerpt; for
`metadata_only` it proves that no body was emitted. It never hashes message or
search text.

## 4. Receipts, proofs and verification keys (spec §23A, Appendix K)

The proof payload carries exactly the Appendix-K fields, signed with the
dedicated Ed25519 disclosure key over RFC 8785 canonical bytes, with
`proof_payload_sha256` equal to the SHA-256 of those exact bytes.

A verifier must be able to check a live result with only `proof_payload`,
`proof_signature` and the matching public key — no database, no Telegram.
That property is a test, not an aspiration: the suite verifies a receipt in a
process that has no database handle.

Historical verification matters as much as live verification, because receipts
are retained 180 days and keys rotate. Public halves live in
`verification_keys` with `activated_at`/`retired_at`, and are reachable through
the already-frozen §33 command surface:

```text
telegram-mcp disclosure key                 # current plus historical
telegram-mcp disclosure key --id <key-id>   # export one, public material
```

No new operator command is introduced; the frozen surface already covers it.

## 5. Exposure budgets (spec §23C)

There are **two budget dimensions**, not two buckets: `client_global`, and
`(client, origin project)` for every contributing project. A cross-project
search over three projects therefore touches four physical buckets. A record
whose `origin_project_refs` names several projects counts **once** globally and
**once in each** contributing project bucket — deliberate over-counting that
prevents evasion by cycling through projects. Deduplication at the retrieval
layer must never reach the accounting layer; a test asserts the contributing
buckets are each charged.

`bytes_disclosed` is the UTF-8 length of the canonical JSON encoding of the
final `data` object after egress transformation and before the `meta`/proof
envelope, so the proof's own size cannot change the number it reports.

### Reservation lifecycle

```text
acquire lock
  recompute every affected bucket from committed ledger rows plus live reservations
  re-derive exposure_snapshot_digest and compare with the approved one
  hard threshold reached -> EXPOSURE_BUDGET_EXCEEDED, before any retrieval
  otherwise create the reservation
release lock                     <- the lock is never held across retrieval
  ...retrieval, revalidation, transformation, measurement...
commit transaction
  convert the reservation into actual ledger rows and release the remainder
```

A reservation is memory-only capability state, never MCP-visible, bound to
`(reservation_ref, runtime_id, request_nonce, client, scope, reserved_records,
reserved_bytes, created_at, expires_at)`. A crash loses reservations, which is
correct: nothing was disclosed.

**Invariant: actual ≤ reserved.** If measured records or bytes exceed the
reservation, the worst-case estimator is wrong, and that is not a licence to
charge more. The call fails closed with `PROOF_GENERATION_FAILED`, emits no
payload, and records an internal invariant failure. Otherwise an attacker who
found an estimator gap could exceed an approved exposure.

**Conservative cleanup.** Where a transaction outcome is uncertain — SQLite
unavailable mid-commit, for instance — releasing the reservation may itself
fail. The rule is over-count, never under-count: the payload is withheld, a
release is attempted, and if the release fails the reservation stays charged
until verified recovery or expiry. A stale reservation is an annoyance; a
reservation freed against a commit that may have happened is a hole.

### Consent re-prompt bound

If the recomputed snapshot digest differs from the approved one, the approval
is void and the call re-prompts with the new quantities — regardless of whether
the warning tier changed, because the operator approved specific disclosure
conditions and not a colour. Exactly **one** automatic restart is permitted.
The second divergence returns:

```text
public error   CONSENT_UNAVAILABLE, retryable=false
internal reason exposure_snapshot_changed_after_reprompt
message        "Consent conditions changed before execution. Retry the request explicitly."
```

`retryable=false` is the point: it stops an agent from autonomously spinning
through consent prompts. The operator retries deliberately.

## 6. Audit chain and external anchor

Each event's MAC is
`HMAC-SHA-256(audit_chain_key, domain || chain_epoch || chain_seq ||
prev_event_mac || canonical_event_without_event_mac)`, appended under one
writer or a `BEGIN IMMEDIATE` transaction that reads the head and inserts the
next sequence atomically, so concurrent completions cannot fork the chain.

The anchor is a daemon-owned `0600` file in a `0700` non-database directory —
the spec's reviewed fallback, chosen over the System Keychain baseline because
the runtime is a non-login service account, the keychain path would make every
anchor test platform-gated, and the file is fully under the account that owns
the chain. It holds at least `(chain_epoch, chain_seq, event_id, event_mac)`
and is authenticated by daemon-held key material.

### The ANCHOR_PENDING barrier

```text
CLEAN ──commit_disclosure──▶ ANCHOR_PENDING ──refresh ok──▶ CLEAN
                                   │ refresh fails
                                   ▼
                               DEGRADED   (latched, survives restart)
```

While `ANCHOR_PENDING`, **no further append to the anchored chain may commit**.
This is what makes "at most one event ahead" an invariant rather than a hope:
without it, a second operation could commit event 101 while the anchor still
reads 99. Every chain appender obeys the barrier, including administrative and
security events — an exemption for "just an admin event" would silently break
the theorem.

Checkpoints sign the current head with the dedicated checkpoint key on the
cadence the Phase-2 settings registry already carries inert
(`audit.checkpoint_cadence_events`, `audit.checkpoint_cadence_seconds`); Phase
3 makes those rows live rather than adding new ones. Checkpoints are local by
default and are never published automatically.

### Startup derives integrity; the flag is only a latch

Integrity is recomputed from the anchor and the chain, never taken on trust
from a persisted bit:

| Observation | State |
|---|---|
| anchor == head, **and** head MAC verifies, **and** the sequence is valid, **and** retained chain/checkpoint linkage verifies | CLEAN |
| head exactly one ahead and the extension verifies from the anchored event | RECOVERY_REQUIRED |
| anchor ahead of the head | FAIL CLOSED — the database was truncated |
| head more than one ahead | FAIL CLOSED |
| any MAC or signature mismatch | FAIL CLOSED |

Equality alone never proclaims integrity. If the persisted flag is cleared by
accident or by hand, the anchor still catches the inconsistency.

### Degraded state and what it records

`audit_integrity_degraded` persists through the closed settings registry, with
two privacy-safe companions so the operator can see what was withheld:

```text
audit.integrity_degraded          0 | 1
audit.degraded_disclosure_ref     tdr_...
audit.degraded_reason             anchor_refresh_failure | chain_verify_failure | anchor_read_failure
```

A `tdr_` reference and a fixed reason code are not content, so the settings
registry's prohibition is respected.

This is where the "charged but withheld" fact lives, and it must live here
rather than in the audit event: at step 11 the event is already MAC-chained and
cannot learn that step 12 later failed without invalidating the chain. So the
receipt says `commit_status="committed"`, which the spec defines as *the
gateway committed and accounted the disclosure before transport handoff* — not
that a client received it — and the degraded record says what actually
happened. `disclosure show` renders the pair truthfully:

```text
Accounting:     committed
Anchor:         failed
Payload:        withheld
Exposure charge: retained
Reason:         AUDIT_INTEGRITY_UNAVAILABLE
```

After repair, that history is preserved rather than rewritten to look like an
ordinary delivery.

While degraded, all nine sensitive tools fail with
`AUDIT_INTEGRITY_UNAVAILABLE` before retrieval; `telegram_status` may report the
non-secret health state. There is no automatic reset. Recovery is successful
`audit verify` followed by user-presence-approved `audit repair-anchor`, which
may advance the anchor only when the retained chain cryptographically extends
from the previously trusted anchor.

## 7. Error mapping

Only the frozen enum is used; Phase 3 introduces no new code.

| Condition | Code | Retryable |
|---|---|---|
| hard budget threshold reached | `EXPOSURE_BUDGET_EXCEEDED` | maybe |
| snapshot changed twice around consent | `CONSENT_UNAVAILABLE` | no |
| consent denied, cancelled or timed out | `CONSENT_DENIED` | no |
| authority moved before serialisation | `SECURITY_LOCKED` / `CLIENT_REVOKED` / `POLICY_CHANGED` / `CURSOR_PROJECT_CHANGED` / `NOT_ACCESSIBLE` | per code |
| actual exceeded reservation, or the proof could not be completed | `PROOF_GENERATION_FAILED` | maybe |
| anchor or chain integrity unavailable | `AUDIT_INTEGRITY_UNAVAILABLE` | no, until operator repair |

## 7A. Operator surface

Every command Phase 3 needs already exists in the frozen §33 surface, so the
`AdminRouter`'s closed table does not grow:

```text
telegram-mcp disclosure show <tdr_...>     privacy-minimised metadata + signature state
telegram-mcp disclosure verify <tdr_...>   reconstructs and verifies without Telegram
telegram-mcp disclosure key [--id <id>]    current and historical public keys
telegram-mcp exposure status [--client <c>] [--project <p>]
telegram-mcp audit verify
telegram-mcp audit checkpoint
telegram-mcp audit repair-anchor           user-presence gated
```

Commands that mutate security state stay behind the presence gate Phase 2a
built; `audit repair-anchor` is the one that matters most, because it is the
only path out of the degraded latch.

## 8. Keys

Three Phase-3 registry rows are provisioned, each with a distinct purpose and a
distinct recomputed id: `disclosure-key` (Ed25519, signs proofs),
`audit-checkpoint-key` (Ed25519, signs chain heads) and `audit-chain-key`
(HMAC-SHA-256, the event MAC). Purpose separation is asserted, as it is for the
Phase-2 rows. Private halves never enter SQLite, logs or any exported artifact;
public halves go to `verification_keys` and stay available for receipt
retention plus the verification-key grace period.

## 9. Storage

Retention follows the spec: receipts 180 days, the exposure ledger 30 days and
never shorter than the longest configured rolling window, checkpoints 180 days,
and historical public verification keys for at least receipt retention plus the
grace period — so a retained receipt always has a key that can verify it.
Enforcement of purge schedules is Phase 5; Phase 3 sets the retention values
and must not make them unsatisfiable.

The five reserved tables come alive exactly as transcribed in Phase 2a:
`disclosure_receipts`, `exposure_ledger`, `audit_events`, `audit_checkpoints`
and `verification_keys`. **No schema change and no nineteenth table.** The
§12.4 indexes already exist. The settings additions above are code-defined
registry rows, not schema.

## 10. Testing

- **Crash injection at each of the twelve steps**, asserting the crash-model
  table: nothing durable before 11, accounted-but-withheld between 11 and 12,
  released after 12.
- **Concurrency:** two calls cannot both observe the same remaining hard-budget
  capacity; the chain never forks; `ANCHOR_PENDING` blocks a second append.
- **Accounting:** shared and cross-project records charge every contributing
  bucket; retrieval-layer deduplication never undercharges.
- **Egress:** deterministic over a Unicode/RTL corpus; the display sanitiser is
  never applied to message bodies.
- **Receipts:** an independent verifier with no database handle verifies a live
  result; a historical key verifies an old receipt; a tampered payload fails.
- **Anchor:** truncation, rollback, anchor-ahead, more-than-one-ahead and MAC
  mismatch each fail closed; the degraded latch survives restart; repair
  refuses an unexplained chain break.
- **No uncommitted escape:** no sensitive byte reaches a transport before a
  successful anchor, asserted by a transport double that records every write.

### Formal model

The model is specified as invariants plus transitions in a checker-agnostic
form, so either TLA+/TLC or Hypothesis stateful tests can consume it; the
choice is made at implementation time and the checker, version and
configuration are recorded in the release manifest.

Invariants: no payload released without a committed receipt and a refreshed
anchor; charged ≥ disclosed; actual ≤ reserved; chain never forks; the chain is
never more than one event ahead of the anchor; a consumed consent is never
reused; authority changes always precede release.

Transitions: request, consent issue/consume/deny/expire, reserve, retrieve,
revalidate, transform, commit, anchor refresh success/failure, crash at each
step, epoch bump, lock, unlock, client disable, restart.

## 11. Decomposition

| Plan | Contents | Character |
|---|---|---|
| **3a** | egress, provenance, receipts, proof signing, verification keys and their offline verifier | deterministic, pure machinery |
| **3b** | exposure accounting, rolling windows, reservations, consent snapshot binding | shared mutable privacy state |
| **3c** | audit chain, external anchor, degraded state and recovery, the coordinator that makes 3a–3c atomic, the operator commands, the formal model and the crash/concurrency gauntlet | transactional composition |

The dependency direction is one-way: 3a has no dependency on 3b or 3c, 3b
depends on 3a's measurement, and 3c composes both.

## 12. Out of scope

The real Telegram adapter and the tool slices (Phase 4); retention, recovery
and backup-restore (Phase 5); installed clients and tunnel acceptance (Phase
6); the release gauntlet (Phase 7). Phase 3 is built and qualified against the
fake adapter, exactly as Phase 2 was. No Telegram access, no production claim.

## Normative grounding

The frozen V0.1.10 specification controls wherever this document is silent.
Ambiguities resolved here, and the reasons, are recorded in the sections above:
the anchor mechanism, the `ANCHOR_PENDING` barrier, startup-derived integrity,
the re-prompt bound and its frozen error code, where the "charged but withheld"
fact is recorded, and the rule that no byte leaves before the anchor.
