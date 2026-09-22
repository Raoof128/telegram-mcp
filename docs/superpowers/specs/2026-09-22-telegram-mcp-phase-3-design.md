# Telegram MCP Phase 3 Design — Disclosure, Budgets, Proofs and Audit Integrity

**Status:** Revision 2. Three gauntlet passes; the third checked every claim against
the frozen specification and the shipped code and found eighteen defects, all folded
in below. Ready for the three implementation plans.
**Date:** 2026-09-22 (Australia/Sydney)
**Spec:** `telegram-mcp-v0.1.10-final-engineering-spec.md` (SHA-256 `36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a`, verified)
**Roadmap:** [release roadmap](../plans/2026-09-22-telegram-mcp-release-roadmap.md), Phase 3 row
**Basis:** Phases 1 and 2 complete on `main`; verified fresh at revision 2 — `484 passed, 7 skipped`, smoke `41 passed, 0 failed`.

## Controlling statement

**Phase 3 does not change the public MCP tool catalogue.** The ten tools, their
argument schemas and their result schemas are frozen from Phase 1 and stay
frozen. This is verified, not assumed: `telegram_status.data.json` already
carries `disclosure_proof_key_id` and `disclosure_proof_public_key` as required
fields, and `meta.json` already carries the complete `disclosure` envelope
(five fields, including the full Appendix-K `proof_payload`) and the complete
`coverage` object (eight fields, closed six-code `partial_reasons` enum,
`project_coverage` 1..8). Phase 1 transcribed the whole Phase-3 surface. What
changes is the disclosure semantics underneath every sensitive tool: what must
happen before a byte leaves, what is durably accounted, and what the gateway
attests afterwards.

**No tool may bypass the disclosure seam.** A tool slice does not call retrieval
and serialisation directly; it asks the coordinator, and the coordinator is the
only thing that can release a sensitive payload. Phase 4 adds real Telegram
slices behind this rule, and a slice that reaches around it is a defect, not an
optimisation.

**No schema change and no nineteenth table.** The five reserved tables are
already transcribed byte-faithfully from §12.2, including the deliberate absence
of a `proof_payload` column, and the §12.4 indexes already exist. Phase 3 adds
code-defined settings rows (§9.2 below) and nothing else to storage.

## 0. What revision 2 changed

The revision-1 document was reviewed twice and still contained eighteen defects,
because two review rounds harden the sections that were attacked and leave the
rest untouched. The third pass checked every normative citation against the
frozen text and every architectural claim against the shipped code.

| # | Defect in revision 1 | Resolution |
|---|---|---|
| 1 | "the `DisclosureGate` protocol stays as the seam" — it returns a boolean and cannot release a payload, contradicting the controlling statement | §1.2 defines the real coordinator interface; the Phase-2 gate is named a placeholder with no production caller |
| 2 | Appendix L is normative; revision 1 invented its own eight invariants and named neither required file | §10.3 carries all fifteen state variables, all fourteen required assertions verbatim, and both file paths |
| 3 | §23D search coverage proof had no design text and no plan owner | new §3.3, owned by plan 3a |
| 4 | one global `bytes_disclosed` formula; §23C.2 requires per-project attribution with envelope charged globally only | §5.2 |
| 5 | reservation tuple dropped `security_epoch` and `consent_challenge_digest` against §23C.3 | §5.4 restores the frozen tuple |
| 6 | no pre-consent hard-budget refusal; §23C.3 requires refusal *before* prompting | §5.3, two normative budget consultations |
| 7 | degraded state versus auditability left unstated | §6.6 |
| 8 | receipt reconstruction depends on re-mintable opaque refs | §4.3 ref-stability invariant plus a rotation test |
| 9 | the zero-seed synthetic key sits on the Appendix K.2 verification path | §4.4; tripwire test and source warning landed with this revision |
| 10 | checkpoint cadence bounds permitted a §26.5 violation, with a false rationale comment | fixed in `settings.py` with this revision, test first |
| 11 | settings work understated as three rows | §9.2 enumerates eleven |
| 12 | the two non-cadence checkpoint MUSTs were missing | §6.4 |
| 13 | genesis value and `chain_epoch` bump rule undefined | §6.2 |
| 14 | no single measurement authority, against a Gate P MUST | §5.1 |
| 15 | no gate mapping and no content-leak sweep, against a Gate O MUST | §10.1, §10.2 |
| 16 | re-prompt timing exceeds the recommended client timeout | §5.5 — **refined, not adopted**: §23C.3 mandates the re-prompt, so the bound stays and the timing is documented |
| 17 | per-tool record rules, the zero-project case and `project_count` semantics absent | §5.2 |
| 18 | Phase-5 purge constraints and receipt purge ordering unrecorded | §9.3 |

## 1. Architecture

```text
src/telegram_mcp/disclosure/
  coordinator.py   DISCLOSURE_STEPS, the coordinator, injectable seams
  measure.py       THE measurement authority: records and bytes, one copy
  egress.py        metadata_only | excerpt | full_text transformation
  provenance.py    origin_project_refs, result-provenance digest
  coverage.py      the §23D coverage object, its invariants and its digest
  receipts.py      Appendix-K payload, JCS bytes, Ed25519 sign, offline verify
  budget.py        reservation lock, rolling windows, the two budget dimensions
  audit/chain.py   event MAC, genesis, BEGIN IMMEDIATE append, checkpoints
  audit/anchor.py  0600 file anchor: read, verify, refresh, degraded latch
```

The coordinator **sequences**; it does not decide. Authority decisions stay in
`authority/`, consent decisions in `consent/`, budget decisions in `budget.py`,
integrity decisions in `audit/`. A second policy engine inside the coordinator
would be the failure this design exists to prevent.

### 1.1 The Phase-2 gate is a placeholder, not the seam

`consent/gate.py` defines `DisclosureGate.authorize_disclosure(challenge,
snapshot) -> DisclosureDecision(allowed: bool)`. A boolean cannot release a
payload. If that stayed the seam, the tool slice would still perform retrieval
and serialisation itself — precisely the bypass the controlling statement
forbids. `grep` confirms the protocol has **no production caller**: it appears
only in `tests/unit/test_gate.py` and `tests/integration/test_vertical_slice.py`.

Phase 3 therefore defines a new seam and keeps `SyntheticDisclosureGate` only as
a test double. This is a placeholder being replaced, not an interface being
broken.

### 1.2 The coordinator interface

```python
async def disclose(
    self,
    *,
    tool_name: str,
    arguments: Mapping[str, object],
    principal: PrincipalContext,
    adapter: RetrievalAdapter,
) -> DisclosureOutcome: ...
```

`DisclosureOutcome` is either a released payload carrying `data`, `meta` (with
the populated `disclosure` and `coverage` objects) and the committed
`disclosure_ref`, or a refusal carrying a frozen error code. There is no third
shape: a caller cannot obtain data without a receipt, because they arrive in the
same object. `RetrievalAdapter` is the Phase-4 seam, faked in Phase 3 exactly as
Phase 2 faked its dependencies.

## 2. The disclosure transaction

Every sensitive call runs this order. The names are frozen as a declaration the
tests assert against; production calls typed functions explicitly rather than
dispatching over the tuple, because a security protocol must not be a
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
| 3 | `estimate_exposure` | conservative worst case via `measure.py`; `exposure_snapshot_digest`; soft/hard tier — **and refuses with `EXPOSURE_BUDGET_EXCEEDED` before any prompt if the hard threshold is already reached** |
| 4 | `consent_issue` | challenge binding that digest and the budget state shown |
| 5 | `consent_consume` | agent-signed approval, exact-once |
| 6 | `reserve_budget` | under one lock: recompute buckets, re-derive the snapshot digest, compare to the approved one, reserve the worst case — or refuse |
| 7 | `retrieve` | adapter call; the 15-second execution deadline starts here |
| 8 | `revalidate_authority` | re-read lock, epochs, grants, memberships, peer authorisation |
| 9 | `transform_egress` | the actual content class present in the response |
| 10 | `measure_and_prepare_proof` | actual records/bytes via `measure.py`, coverage object and digest, provenance digest, signed receipt material |
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
flushed or returned to an MCP transport before `refresh_anchor` succeeds. Every
sensitive response is fully materialised in memory before step 12. This
forecloses the shortcut where a later async generator starts streaming content
while the audit transaction is still finishing.

Step 10 prepares a signed receipt; step 11 makes it authoritative. The
distinction is not pedantry: a receipt that exists before its commit is a claim
about a disclosure that has not happened. No Appendix-K field depends on the
audit chain head, so nothing in the payload needs finalising inside step 11; if
that ever changes, the dependent field moves into step 11 rather than the chain
head moving out.

### Crash model

| Crash point | Durable state | Payload | Recovery |
|---|---|---|---|
| before step 11 | none | none | nothing to do; unreserved means undisclosed |
| between 11 and 12 | accounted, chain at most one event ahead of the anchor | withheld | `audit verify` then user-presence `audit repair-anchor` |
| after step 12 | accounted and anchored | released | none needed |

Once sensitive bytes are handed to the transport after a successful anchor, the
exposure is conservatively treated as disclosed even if delivery acknowledgement
never arrives. A privacy budget is not refunded because a client disconnected at
an awkward moment.

## 3. Egress, provenance and coverage (spec §23B, §23D)

### 3.1 Egress

Egress transformation runs after retrieval and revalidation and before
accounting, signing and serialisation.

- `metadata_only` — `text=null`, no excerpt, `text_truncated=false`.
- `excerpt` — text truncated to the grant's `excerpt_max_codepoints`, counted in
  codepoints, `text_truncated=true` when the source was longer.
- `full_text` — the bounded result rules from Phase 1.

For a record authorised through several selected projects, the effective profile
is the most restrictive and the smallest excerpt limit wins.
`effective_egress_level` in the receipt is the **actual maximum content class
present in this response**, not the grant's ceiling: a response that happens to
carry only metadata is `metadata_only` even under a `full_text` grant. It is
always `metadata_only` for `telegram_list_projects`, `telegram_resolve_project`,
`telegram_list_chats`, `telegram_resolve_peer` and `telegram_get_unread`.

**Message text is never sanitised.** The consent-display sanitiser from Phase 2b
strips C0/C1 and bidi controls for a prompt string; running it over a Telegram
message body would corrupt the evidence the model is reading. Transformation
preserves logical Unicode exactly, and the only content change is explicit
excerpt truncation. Determinism is tested against a Unicode/RTL corpus.

### 3.2 Provenance

Provenance binds **what leaves**, not what Telegram returned:
`canonical_result_provenance_digest` commits to the ordered set of privacy-safe
record identities, `origin_project_refs`, effective egress level and truncation
state, computed after transformation. For `excerpt` it proves the excerpt; for
`metadata_only` it proves that no body was emitted. It never hashes message or
search text.

### 3.3 Search coverage proof (§23D)

The coverage object is already frozen in `contracts/meta.json`. Phase 3 owns its
construction, its invariants and its digest; Phase 4 supplies the counts.

```text
complete  eligible_peers  peers_scanned  telegram_rpcs
hits_examined  hits_returned  partial_reasons[]  project_coverage[]
```

`project_coverage` carries `{project_ref, eligible_peers, peers_scanned}` for
each selected project, 1..8 entries.

**Deduplication runs the opposite way from accounting, and this is the single
easiest thing in Phase 3 to get backwards.**

| Layer | Shared canonical peer appearing in several projects |
|---|---|
| coverage totals | de-duplicated once in global `eligible_peers` / `peers_scanned` |
| coverage per project | counted in **each** contributing `project_coverage` entry |
| exposure accounting | counted once globally and **once in each** contributing project bucket |

Coverage de-duplicates its global totals; accounting deliberately over-counts
per project. A test asserts both directions on the same fixture so neither rule
can be copied onto the other.

Invariants Phase 3 enforces before signing, none of which JSON Schema can
express:

- `complete=true` is permitted only when every eligible canonical peer reached
  Telegram's exhaustion condition for the requested query and interval and no
  deadline, RPC, hit, peer or response bound interrupted the search. One RPC per
  peer is not sufficient.
- `complete=true` implies `meta.next_cursor=null`, `meta.partial=false` and
  `partial_reasons=[]`.
- `meta.next_cursor != null` implies `complete=false` and `response_limit` in
  `partial_reasons`.
- eligible peers above `max_cross_project_peers` (250) either refuse before
  search or return partial data with `complete=false` and `peer_budget` present.
- caller cancellation never yields a partial sensitive result; it emits nothing.

`canonical_coverage_digest` is SHA-256 over the exact JCS-canonical emitted
coverage object, non-null for the two search tools and null for the other seven.

## 4. Receipts, proofs and verification keys (spec §23A, Appendix K)

The proof payload carries exactly the twenty-one Appendix-K fields, signed with
the dedicated Ed25519 disclosure key over RFC 8785 canonical bytes, with
`proof_payload_sha256` equal to the SHA-256 of those exact bytes. The
canonicalisation implementation and version are recorded in the release manifest,
as §23A.2 requires.

### 4.1 Live verification

A verifier must be able to check a live result with only `proof_payload`,
`proof_signature` and the matching public key — no database, no Telegram. That
property is a test, not an aspiration: the suite verifies a receipt in a process
that has no database handle.

### 4.2 Historical verification

Receipts are retained 180 days and keys rotate. Public halves live in
`verification_keys` with `activated_at`/`retired_at`, reachable through the
already-frozen §33 surface:

```text
telegram-mcp disclosure key                 # current plus historical
telegram-mcp disclosure key --id <key-id>   # export one, public material
```

No new operator command is introduced.

### 4.3 Reconstruction rests on ref stability — make it an invariant

There is no `proof_payload` column, by design. `disclosure verify` must
therefore rebuild byte-identical canonical payload from the receipt row, and
three of the twenty-one fields are not stored on it: `principal_ref`,
`client_ref` and `account_ref` are read through the foreign keys from
`principals`, `mcp_clients` and `accounts`. (`schema` and `consent_verified` are
constants.)

`opaque.py` mints refs from `secrets.randbits(130)`; they are stored, not
derived. So **any operation that re-mints one of these three refs silently
invalidates every retained receipt that names it** — the signature stops
verifying because the payload no longer rebuilds. Two such operations are
already specified: client rotation, and §33.3's committed restore, which "MUST
regenerate local opaque refs as necessary".

Phase 3 states the invariant and tests it:

- `principal_ref`, `client_ref` and `account_ref` are **immutable for at least
  the receipt retention window**. `client rotate` rotates credentials and
  `auth_binding`; it must not re-mint `client_ref`.
- Where a restore does regenerate refs (a Phase-5 path), it MUST bump
  `chain_epoch` and record that pre-restore receipts are no longer
  reconstructable; `disclosure show` reports `payload_unreconstructable` rather
  than a verification failure, because the distinction between "tampered" and
  "identity regenerated by an authorised restore" is exactly what an operator
  needs.
- **Test:** rotate a client's credentials, then re-verify a receipt minted
  before the rotation. Revision 1's proposed test ("a historical key verifies an
  old receipt") would have stayed green through this entire hole, because it
  rotates the key and never the ref. That is a check passing for the wrong
  reason, and it is replaced.

### 4.4 Retiring the synthetic key

`tools/status.py` advertises a key derived from 32 zero bytes under the id
`synthetic-test-key-v1`. Appendix K.2 step 4 tells verifiers to resolve
`proof_key_id` against the key `telegram_status` advertises. While nothing signs
anything that is merely honest labelling; the moment a real signer exists it is
a forgery oracle, because the private half is public knowledge.

Plan 3a MUST replace the factory with the recomputed `disclosure-key` id and
public half before the first receipt is signed. A tripwire test
(`tests/security/test_demo_isolation.py`) pins the current synthetic values and
will fail when the real key lands, forcing the author to handle it; a warning in
the module source names the obligation at the point of the hazard. Both landed
with this revision.

## 5. Exposure budgets (spec §23C)

### 5.1 One measurement authority

Gate P requires that "exposure measurement rules are identical between prompts,
reservations, ledger rows and receipts". There are four measurement sites —
step 3, step 6, step 10 and step 11 — so measurement lives in exactly one module,
`disclosure/measure.py`, and is imported by all four. This is the project's
existing "one copy of each shared rule" convention applied to the number that
privacy accounting depends on: a second copy is the defect, and a divergence
between the number shown to the human and the number charged to the ledger is
the specific dishonesty this rule prevents.

A test asserts cross-site identity on every call in the suite: the quantity the
prompt displayed, the quantity reserved, the quantity written to the ledger and
the quantity in the signed receipt are computed by one function.

### 5.2 Dimensions, subjects and quantities

There are **two budget dimensions**, not two buckets: `client_global`, and
`(client, origin project)` for every contributing project. A cross-project search
over three projects therefore touches four physical buckets — one global row plus
three project rows, which the `UNIQUE(disclosure_ref, budget_subject_kind,
budget_subject_digest)` constraint supports exactly.

- A record whose `origin_project_refs` names several projects counts **once**
  globally and **once in each** contributing project bucket. Deduplication at the
  retrieval layer must never reach the accounting layer.
- `telegram_list_projects` and `telegram_resolve_project` select no project and
  charge **only** the client-global bucket: one ledger row, `project_count = 0`.
- `telegram_status` is not exposure-accounted and generates no receipt; it is the
  sole non-sensitive tool.
- `project_count` in the receipt means explicitly selected gateway projects: zero
  for the two catalogue tools, one for ordinary project-scoped tools, two to
  eight for cross-project search.

`records_disclosed` is deterministic by tool after egress transformation: project
records for list/resolve projects; chat or peer records for list/resolve chats,
resolve peer and unread; message or search-hit records for get messages, get
context, search and cross-project search.

**Bytes are attributed differently at the two dimensions**, and revision 1 gave
only the global rule:

- **Global `bytes_disclosed`** is the UTF-8 length of the canonical JSON encoding
  of the final `data` object after egress transformation and before the
  `meta`/proof envelope, so the proof's own size cannot change the number it
  reports.
- **Per-project bytes** are the sum of canonical JSON bytes of the *result
  records attributed to that project*, conservatively including the complete
  record when it has several origins. Envelope and coverage overhead are charged
  **globally only**.

Charging the global figure to every project bucket is over-counting, so no naive
test catches it, and it makes project budgets fire early and unpredictably. The
suite asserts that the sum of per-project record bytes plus envelope overhead
equals the global figure for a single-project call, and that envelope overhead
appears in no project bucket.

### 5.3 The budget is consulted twice, and both are normative

§23C.3 requires a pre-consent evaluation and a post-consent reservation. Both
are load-bearing and neither replaces the other.

| Where | Reads | Effect |
|---|---|---|
| step 3, before any prompt | committed ledger rows plus live reservations | below soft: ordinary prompt. At or above soft: elevated warning showing cumulative quantities. At or above hard: **refuse with `EXPOSURE_BUDGET_EXCEEDED` without prompting** |
| step 6, under the reservation lock | the same, recomputed | authoritative; refuses or reserves |

Revision 1 checked only at step 6, which meant prompting a human for a call the
gateway was about to refuse. Asking for consent to something that cannot happen
trains an operator to click through prompts.

### 5.4 Reservation lifecycle

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

A reservation is memory-only capability state, never MCP-visible, bound to the
tuple §23C.3 freezes:

```text
(client_id, security_epoch, project_scope_digest,
 consent_challenge_digest, request_nonce, expires_at)
```

Revision 1 dropped `security_epoch` and `consent_challenge_digest` from this
tuple. Both are load-bearing: `security_epoch` is what makes an emergency lock
invalidate reservations in flight, and `consent_challenge_digest` is what stops
a reservation minted under one approval from being spent by a different call.
Implementation detail may carry `reservation_ref`, `runtime_id` and the reserved
quantities alongside, but the six frozen components are the binding.

A crash loses reservations, which is correct: nothing was disclosed.

**Invariant: actual ≤ reserved.** If measured records or bytes exceed the
reservation, the worst-case estimator is wrong, and that is not a licence to
charge more. The call fails closed with `PROOF_GENERATION_FAILED`, emits no
payload, and records an internal invariant failure. Otherwise an attacker who
found an estimator gap could exceed an approved exposure.

**Conservative cleanup.** Where a transaction outcome is uncertain — SQLite
unavailable mid-commit, for instance — releasing the reservation may itself fail.
The rule is over-count, never under-count: the payload is withheld, a release is
attempted, and if the release fails the reservation stays charged until verified
recovery or expiry. A stale reservation is an annoyance; a reservation freed
against a commit that may have happened is a hole.

### 5.5 Consent re-prompt bound, and its honest timing

If the recomputed snapshot digest differs from the approved one, the approval is
void and §23C.3 says the request **MUST** be re-prompted with the new exact
quantities — regardless of whether the warning tier changed, because the operator
approved specific disclosure conditions and not a colour. Exactly **one**
automatic restart is permitted. The second divergence returns:

```text
public error   CONSENT_UNAVAILABLE, retryable=false
internal reason exposure_snapshot_changed_after_reprompt
message        "Consent conditions changed before execution. Retry the request explicitly."
```

`retryable=false` is the point: it stops an agent from autonomously spinning
through consent prompts. The operator retries deliberately.

**Timing, stated rather than discovered.** `consent_wait_timeout_seconds` is 45
and `recommended_client_tool_timeout_seconds` is 75. Two consent windows plus a
15-second execution deadline exceed 75 seconds whenever the operator spends more
than about thirty seconds reading. A re-prompted call can therefore outlive the
recommended client timeout. The gateway does **not** shorten the second window to
fit: a human being shown new quantities needs time to read them, and truncating
that is worse than a client timeout. If the client has already given up, the
gateway still fails closed — no payload, reservation released — so a timeout is
wasteful, never unsafe. A test asserts that a client disconnecting mid-re-prompt
releases the reservation and emits nothing.

### 5.6 The trichotomy

Every route to bulk extraction costs the adversary, and there is no silent fourth
path:

| Route | Outcome |
|---|---|
| one large call | step 3 refuses before the prompt; nothing retrieved |
| many small calls | the client-global rolling bucket accumulates and refuses |
| cycle through projects | per-project buckets over-count shared records and the global ceiling still binds |

Each branch is either refused or ledgered. This is Gate P's three bullets stated
as one attackable property, and it is carried into the formal model as
`ExfiltrationHasNoSilentPath`.

**Its honest bound:** rolling windows limit burst rate, not lifetime exposure. A
patient extractor pacing below the budget is not stopped by this mechanism, and
saying otherwise would be marketing. What the budget guarantees is that bulk
extraction is impossible without either a refusal or a durable, signed record of
every unit disclosed.

## 6. Audit chain and external anchor

### 6.1 The event MAC

```text
event_mac = HMAC-SHA-256(
    audit_chain_key,
    "telegram-mcp-audit-v1"
      || uint64_be(chain_epoch)
      || uint64_be(chain_seq)
      || raw32(prev_event_mac)
      || JCS(event_without_event_mac)
)
```

Hex-encoded columns are decoded to raw bytes before entering the MAC input;
`JCS` is the same TG-JCS-v1 encoder the rest of the system uses. Appends run
under a single writer or a `BEGIN IMMEDIATE` transaction that reads the head and
inserts the next sequence atomically, so concurrent completions cannot fork the
chain.

### 6.2 Genesis and chain epochs

§26.5 requires "an explicit genesis value and sequence 1"; revision 1 never
defined it. For `chain_seq = 1`:

```text
prev_event_mac = hex(SHA-256("telegram-mcp-audit-genesis-v1" || uint64_be(chain_epoch)))
```

Binding genesis to the epoch stops two epochs sharing a first link.

`chain_epoch` increments only on audit-chain-key rotation or a committed
policy-bundle restore, each user-presence gated, and a signed checkpoint of the
old head is written **before** the bump. `audit_events` carries
`UNIQUE(chain_epoch, chain_seq)`, so a bump is the only way a sequence restarts.

### 6.3 The anchor

The anchor is a daemon-owned `0600` file in a `0700` non-database directory —
the spec's reviewed fallback, chosen over the System Keychain baseline because
the runtime is a non-login service account, the keychain path would make every
anchor test platform-gated, and the file is fully under the account that owns the
chain. It holds at least `(chain_epoch, chain_seq, event_id, event_mac)` and is
authenticated by daemon-held key material.

The spec's reference configuration declares `external_anchor_provider:
macos_system_keychain`. Choosing the fallback means the declaration must change
with it, so Phase 3 adds `audit.external_anchor_provider` and
`audit.external_anchor_ref` to the settings registry (§9.2) and `doctor` verifies
the anchor's existence, mode and owner. A manifest that claims a provider the
build does not use is a lie in the artifact that exists to prevent lies.

### 6.4 Checkpoints

Checkpoints sign the current head with the dedicated checkpoint key. §26.5 sets
three triggers, and revision 1 carried only the first:

1. at least every 500 events or 60 minutes, whichever comes first;
2. before clean shutdown;
3. before key rotation.

Cadence comes from the Phase-2 settings rows, which shipped with maxima of
100,000 events and 7 days — wide enough to configure a violation of the §26.5
MUST, under a comment asserting the spec states no number. Both are corrected
with this revision: the maxima are now 500 and 3600, test first. Checkpoints are
local by default and are never published automatically.

### 6.5 The ANCHOR_PENDING barrier

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
security events — an exemption for "just an admin event" would silently break the
theorem.

The barrier window is one file write and fsync. A second call that arrives inside
it waits up to a bounded interval and then fails with
`AUDIT_INTEGRITY_UNAVAILABLE` rather than queueing indefinitely; its reservation
is released and nothing is emitted.

### 6.6 Degraded means unauditable, and that is stated

While DEGRADED the anchor cannot advance. Since §26.5 requires an anchor refresh
after *every* append, and since a head more than one event ahead fails closed at
startup, **no audit event may be appended at all while degraded**. The
consequence must be said out loud rather than discovered during an incident:

> While degraded, the nine sensitive tools refuse with
> `AUDIT_INTEGRITY_UNAVAILABLE` before retrieval, and **those refusals are not
> audited**, because appending would make the chain unrecoverable. The degraded
> record itself, written once at latch time, is the durable evidence that the
> gap exists and why.

This is the correct trade — an unrecoverable chain is worse than an unrecorded
refusal, and no disclosure can occur during the gap — but it is a real reduction
in auditability and it belongs in the operator documentation, not only here.
`telegram_status` may continue to report the non-secret health state.

### 6.7 Startup derives integrity; the flag is only a latch

Integrity is recomputed from the anchor and the chain, never taken on trust from
a persisted bit:

| Observation | State |
|---|---|
| anchor == head, **and** head MAC verifies, **and** the sequence is valid, **and** retained chain/checkpoint linkage verifies | CLEAN |
| head exactly one ahead and the extension verifies from the anchored event | RECOVERY_REQUIRED |
| anchor ahead of the head | FAIL CLOSED — the database was truncated |
| head more than one ahead | FAIL CLOSED |
| any MAC or signature mismatch | FAIL CLOSED |

Equality alone never proclaims integrity. If the persisted flag is cleared by
accident or by hand, the anchor still catches the inconsistency.

RECOVERY_REQUIRED and the latched DEGRADED state have identical externally
visible behaviour — all nine sensitive tools refuse, no appends occur, recovery
is the same ceremony. They are distinguished only in `doctor` and `audit verify`
output, because an operator needs to know whether the daemon crashed between
steps 11 and 12 or whether an anchor write failed outright.

### 6.8 Degraded state and what it records

`audit_integrity_degraded` persists through the closed settings registry, with
two privacy-safe companions so the operator can see what was withheld:

```text
audit.integrity_degraded          0 | 1
audit.degraded_disclosure_ref     tdr_...
audit.degraded_reason             anchor_refresh_failure | chain_verify_failure | anchor_read_failure
```

A `tdr_` reference and a fixed reason code are not content, so the settings
registry's prohibition is respected.

This is where the "charged but withheld" fact lives, and it must live here rather
than in the audit event: at step 11 the event is already MAC-chained and cannot
learn that step 12 later failed without invalidating the chain. So the receipt
says `commit_status="committed"`, which the spec defines as *the gateway
committed and accounted the disclosure before transport handoff* — not that a
client received it — and the degraded record says what actually happened.
`disclosure show` renders the pair truthfully:

```text
Accounting:     committed
Anchor:         failed
Payload:        withheld
Exposure charge: retained
Reason:         AUDIT_INTEGRITY_UNAVAILABLE
```

After repair, that history is preserved rather than rewritten to look like an
ordinary delivery. There is no automatic reset. Recovery is successful
`audit verify` followed by user-presence-approved `audit repair-anchor`, which
may advance the anchor only when the retained chain cryptographically extends
from the previously trusted anchor.

## 7. Error mapping

Only the frozen §27.1 enum is used; Phase 3 introduces no new code. Retryability
is quoted from that table, not invented.

| Condition | Code | Retryable |
|---|---|---|
| hard budget reached, at step 3 or step 6 | `EXPOSURE_BUDGET_EXCEEDED` | maybe |
| snapshot changed twice around consent | `CONSENT_UNAVAILABLE` | no |
| consent denied, cancelled or timed out | `CONSENT_DENIED` | no |
| authority moved before serialisation | `SECURITY_LOCKED` / `CLIENT_REVOKED` / `POLICY_CHANGED` / `CURSOR_PROJECT_CHANGED` / `NOT_ACCESSIBLE` | per code |
| actual exceeded reservation, or the proof could not be completed | `PROOF_GENERATION_FAILED` | maybe |
| anchor or chain integrity unavailable; barrier wait exhausted | `AUDIT_INTEGRITY_UNAVAILABLE` | no, until operator repair |

## 7A. Operator surface

Every command Phase 3 needs already exists in the frozen §33 surface, verified
line by line, so the `AdminRouter`'s closed table does not grow:

```text
telegram-mcp disclosure show <tdr_...>     privacy-minimised metadata + signature state
telegram-mcp disclosure verify <tdr_...>   reconstructs and verifies without Telegram
telegram-mcp disclosure key [--id <id>]    current and historical public keys
telegram-mcp exposure status [--client <c>] [--project <p>]
telegram-mcp audit verify
telegram-mcp audit checkpoint
telegram-mcp audit repair-anchor           user-presence gated
```

Commands that mutate security state stay behind the presence gate Phase 2a built;
`audit repair-anchor` is the one that matters most, because it is the only path
out of the degraded latch. Hard thresholds are mutable only through this plane
with user presence: §23C.3 is explicit that no MCP argument, header or
alternate project bypasses a hard budget, and a test asserts it.

## 8. Keys

Three Phase-3 registry rows are provisioned, each with a distinct purpose and a
distinct recomputed id: `disclosure-key` (Ed25519, signs proofs),
`audit-checkpoint-key` (Ed25519, signs chain heads) and `audit-chain-key`
(HMAC-SHA-256, the event MAC). All three already exist in `keys/registry.py` with
`required_phase=3`. Purpose separation is asserted, as it is for the Phase-2 rows.
Private halves never enter SQLite, logs or any exported artifact; public halves go
to `verification_keys` and stay available for receipt retention plus the grace
period.

**Provisioning needs a code change plans must not overlook.** `provision_missing`
only creates names listed in `_FILE_BACKED_ROWS`, which today holds the four
Phase-2 rows. The three Phase-3 rows are excluded, so provisioning silently skips
them regardless of the `phases` argument. Plan 3a extends that frozenset, passes
`phases=(2, 3)` at startup, populates `verification_keys` with the two public
halves and their `activated_at`, and extends the `doctor` key-inventory check —
which reads the same `FILE_BACKED_KEYS` alias — to cover them.

## 9. Storage and settings

### 9.1 Tables

The five reserved tables come alive exactly as transcribed in Phase 2a:
`disclosure_receipts`, `exposure_ledger`, `audit_events`, `audit_checkpoints` and
`verification_keys`. The §12.4 indexes already exist, including
`idx_exposure_ledger_window` on `(client_id, budget_subject_kind,
budget_subject_digest, ts)` which the rolling-window recomputation uses directly.
No schema change.

### 9.2 Settings rows — eleven, not three

Revision 1 named three. The real count, all code-defined registry rows in the
closed allowlist, none of which can carry content:

| Row | Purpose |
|---|---|
| `audit.integrity_degraded` | the latch |
| `audit.degraded_disclosure_ref` | which disclosure was charged but withheld |
| `audit.degraded_reason` | fixed reason code |
| `audit.external_anchor_provider` | declares the file provider (§6.3) |
| `audit.external_anchor_ref` | the anchor path |
| `retention.disclosure_receipt_days` | 180 |
| `retention.exposure_ledger_days` | 30 |
| `retention.audit_events_days` | 30 |
| `retention.audit_checkpoint_days` | 180 |
| `retention.verification_key_grace_days` | 30 |
| `retention.message_ref_days` | 180 |

The six retention values are in the spec's configuration block and have **no
registry row today** — `grep retention settings.py` returns nothing — so
revision 1's claim that "Phase 3 sets the retention values" had nowhere to set
them.

### 9.3 Retention, and the constraints Phase 3 places on Phase 5

Phase 5 enforces purge schedules. Phase 3 sets the values and must not make them
unsatisfiable, and it records three ordering rules that a purge implementation
written later would otherwise have to rediscover:

- **Exposure rows before receipts.** §23A.3 permits purging an expired receipt
  only after its dependent exposure rows are eligible, which the
  `exposure_ledger.disclosure_ref` foreign key also enforces mechanically.
- **Keys outlive receipts.** Historical public verification keys are retained for
  at least receipt retention plus the grace period, so a retained receipt always
  has a key that can verify it.
- **Purge the chain from a verified checkpoint, never an arbitrary row.** §26.5
  requires enough signed checkpoint and anchor metadata to distinguish intentional
  retention truncation from an unexplained chain break. A purge that starts from
  an unauthenticated row destroys exactly the evidence `audit verify` needs, and
  would turn routine retention into a permanent FAIL CLOSED.

## 10. Testing

### 10.1 Gate mapping

Phase 3 is the phase that moves Gates O, P and Q. Evidence lands in
`docs/verification/phase-3.md` with a gate ledger in the established format, and
every gate line names the command that proves it.

| Gate | Phase-3 obligation |
|---|---|
| **O** | one signed receipt per sensitive success; signature verifies and fails on tampering; no content in receipts, ledger, chain or checkpoints; provenance correct for ordinary and cross-project records; chain and checkpoints verify; atomic commit before serialisation; linear sequence under concurrency; rollback behind the anchor detected; key history satisfies retention |
| **P** | egress can only reduce; most restrictive profile for shared objects; soft/hard thresholds exact; hard limits unbypassable by arguments or retries; concurrent reservations prevent race bypass; client-global ceiling prevents project cycling; conservative per-project and single global accounting; measurement identical across prompt, reservation, ledger and receipt |
| **Q** | bounded formal state-machine verification passes for the release configuration; property and state-machine tests pass |

### 10.2 The suites

- **Content-leak sweep.** Gate O's first privacy MUST, and absent from revision 1:
  drive every tool against a corpus of distinctive marker strings, then assert no
  marker appears in any `disclosure_receipts` column, any `exposure_ledger` row,
  any `audit_events` or `audit_checkpoints` row, any settings value, or any log
  line. Whole-store scan, not field-by-field, so a field added later cannot
  escape it.
- **Crash injection at each of the twelve steps**, asserting the crash-model
  table: nothing durable before 11, accounted-but-withheld between 11 and 12,
  released after 12.
- **Concurrency:** two calls cannot both observe the same remaining hard-budget
  capacity; the chain never forks; `ANCHOR_PENDING` blocks a second append and
  the waiter fails with the right code.
- **Accounting:** shared and cross-project records charge every contributing
  bucket; retrieval-layer deduplication never undercharges; per-project bytes
  exclude envelope overhead; catalogue tools charge only the global bucket.
- **Coverage:** the invariant chain of §3.3 holds; coverage de-duplicates
  globally while accounting over-counts per project, asserted on one fixture.
- **Egress:** deterministic over a Unicode/RTL corpus; the display sanitiser is
  never applied to message bodies.
- **Receipts:** an independent verifier with no database handle verifies a live
  result; a historical key verifies an old receipt; **a rotated client's old
  receipt still reconstructs**; a tampered payload fails.
- **Anchor:** truncation, rollback, anchor-ahead, more-than-one-ahead and MAC
  mismatch each fail closed; the degraded latch survives restart; repair refuses
  an unexplained chain break.
- **No uncommitted escape:** no sensitive byte reaches a transport before a
  successful anchor, asserted by a transport double that records every write.
- **Adversarial maximal extraction.** A scripted client instructed to extract as
  much as the rules permit runs against the fake adapter for a simulated
  twenty-four hours. Whatever total it achieves is recorded in the Phase-3
  evidence, first run sealed, never re-run until the number looks better. Either
  the budgets hold it to the expected ceiling, or the run has found a gap — both
  outcomes are results. The published figure is the one number this project can
  offer that nobody else publishes: how much private content a *compliant*
  assistant can see.

### 10.3 Formal model (Appendix L is normative)

Appendix L requires a bounded executable state model. Revision 1 substituted its
own paraphrase; revision 2 carries the appendix.

State: `security_epoch`, `policy_epoch`, `project_epoch[]`, `client_enabled[]`,
`client_project_grants[]`, `egress_level[]`, `project_membership[]`,
`consent_state`, `request_state`, `exposure_state`, `exposure_reservations[]`,
`disclosure_commit_state`, `audit_chain_epoch`, `audit_chain_seq`, `lock_state`.

Required assertions, verbatim:

```text
NoDisclosureWhenLocked            NoDisclosureAfterClientRevoke
NoDisclosureAfterProjectRevoke    NoDisclosureWithStaleEpoch
NoOrdinaryCrossProjectDisclosure  CrossProjectRequiresExplicitSetAndGrant
ConsentConsumedAtMostOnce         HardExposureBudgetCannotBeBypassed
ConcurrentBudgetReservationIsAtomic
EgressNeverExpandsAuthorisedPayload
ProvenanceMatchesAuthorisingProjectSet
NoReceiptWithoutVerifiedConsent   DisclosureCommitIsAtomic
AuditSequenceNeverForks
```

This design adds four of its own, which the appendix permits and this
architecture requires:

```text
NoPayloadBeforeAnchorRefresh      ChainNeverMoreThanOneAheadOfAnchor
ActualNeverExceedsReserved        ExfiltrationHasNoSilentPath
```

Transitions: request, consent issue/consume/deny/expire, reserve, retrieve,
revalidate, transform, commit, anchor refresh success/failure, crash at each of
the twelve steps, epoch bump, lock, unlock, client disable, restart.

The model is written checker-agnostically so either TLA+/TLC or Hypothesis
stateful tests can consume it; the choice is made at implementation time. The
exact checker, bounds and configuration belong in **`formal/README.md`** and
**`SECURITY-MANIFEST.json`**, which Appendix L names and revision 1 did not.

## 11. Claim boundaries

Appendix K.3 requires the PCR claim boundary to appear in operator and security
documentation. Phase 3 states it in three parts, the third of which the frozen
spec does not require and honesty does:

1. A receipt attests that the gateway committed and accounted a disclosure under
   the named authority, consent, provenance, egress and coverage state. It does
   **not** prove a remote client received the response.
2. It provides **no** long-term cryptographic commitment to exact private text
   bytes. Content hashes were deliberately avoided because they become a
   privacy and fingerprinting oracle.
3. **Nobody in those conversations consented to any of this.** Every mechanism in
   Phase 3 protects the account owner: their budget, their receipts, their audit
   trail. The other participants in a Telegram chat cannot see a prompt, cannot
   read a receipt and cannot object. That is out of scope for V0.1.10, and
   saying so plainly is the minimum owed to people the system discloses and
   never serves.

Two further bounds belong beside them: exposure budgets limit burst rate, not
lifetime exposure (§5.6); and the audit chain detects database-only tampering,
not an attacker holding both the daemon account and the anchor.

## 12. Decomposition

| Plan | Contents | Character |
|---|---|---|
| **3a** | measurement authority, egress, provenance, **coverage object and its invariants**, receipts, proof signing, verification keys and their offline verifier, **key provisioning and the synthetic-key retirement** | deterministic, pure machinery |
| **3b** | exposure accounting, rolling windows, per-project byte attribution, reservations, both budget consultations, consent snapshot binding | shared mutable privacy state |
| **3c** | audit chain with genesis and epochs, external anchor and its settings rows, degraded state and recovery, the coordinator and its interface, the operator commands, the formal model, the crash/concurrency gauntlet and the content-leak sweep | transactional composition |

The dependency direction is one-way: 3a has no dependency on 3b or 3c, 3b depends
on 3a's measurement, and 3c composes both.

## 13. Out of scope

The real Telegram adapter and the tool slices (Phase 4); retention enforcement,
recovery and backup-restore (Phase 5); installed clients and tunnel acceptance
(Phase 6); the release gauntlet (Phase 7). Phase 3 is built and qualified against
the fake adapter, exactly as Phase 2 was. No Telegram access, no production claim.

## Normative grounding

The frozen V0.1.10 specification controls wherever this document is silent.
Ambiguities resolved here, and the reasons, are recorded in the sections above:
the coordinator interface replacing the Phase-2 placeholder, the anchor mechanism
and its declared provider, the genesis value and epoch-bump rule, the
`ANCHOR_PENDING` barrier and its waiting policy, startup-derived integrity, the
unauditable degraded window, the re-prompt bound and its timing, ref stability as
a precondition for receipt reconstruction, and the rule that no byte leaves
before the anchor.
