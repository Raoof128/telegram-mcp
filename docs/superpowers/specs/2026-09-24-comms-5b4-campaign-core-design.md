# Comms 5b-4 Design: the Campaign Core (fake transports only)

**Status:** Revision 4. Revision 1 carried the owner's six corrections (§0A). Revision 2 folded in a line-by-line self-gauntlet whose SQLCipher findings were measured. Revision 3 folds in the owner's gauntlet of revision 1 (§0B): 7 blockers and 8 hardening items, 11 adopted, 1 refined, 3 already fixed by revision 2. Revision 4 folds in the spec-level consequences of the owner's gauntlet of the implementation plan (§0C).
**Date:** 2026-09-24 (Australia/Sydney)
**Parent:** [`comms-consolidation-design.md`](2026-09-24-comms-consolidation-design.md) rev 2, §4.2; governed by [`docs/comms-spec-v0.2.md`](../../comms-spec-v0.2.md).
**Source requirements:** the owner's *Persian Society Communications Gateway v0.1*, §§4–19 and §32 ([`docs/provenance/comms-gateway-v0.1.md`](../../provenance/comms-gateway-v0.1.md)).

## Controlling statement

The campaign core turns "send this campaign" into durable, deduplicated, idempotent delivery jobs, and executes them against transports **without ever resending blindly**. **The campaign the operator can inspect is the campaign that will execute.** An operator command is the authorization; there is no second approval anywhere. Nothing here talks to Telegram or Meta: every transport in 5b-4 is a fake.

## 0. Owner decisions

| # | Decision |
|---|---|
| D1 | Storage: a new `comms.db`, SQLCipher. Core accepts a key; it never obtains one. 5e supplies it. |
| D2 | Code lives in `comms.core`. The "core stays empty" guard is retired for transport-neutral domain code; the one-way dependency rule is unchanged. |
| D3 | Surface: a Python library, fake transports and tests. The CLI, admin commands and AI drafting tools stay in 5e. |
| D4 | Lifecycle events go to a plain append-only journal now; 5c puts them on the tamper-evident chain. |
| D5 | `SCHEDULED` is immutable. To edit: unschedule, edit, validate, schedule again (deviation from source §19, approved). |

## 0A. Revision 1: the owner's six corrections and hardening

| # | Item | Where |
|---|---|---|
| C1 | No external side effect or I/O inside a database transaction | §5 |
| C2 | `INDETERMINATE`: unknown ≠ failed | §6.3 |
| C3 | Cancel and retry act on jobs | §6 |
| C4 | One logical job per frozen delivery; attempts in their own table | §7 |
| C5 | Recipient separate from contact points | §3 |
| C6 | Scheduled jobs are time-gated | §8 |
| H1 | Execution set ⊆ frozen set | §5.3 |
| H2 | `prepare` / `deliver`; the prepared payload is frozen | §4 |
| H3 | Asynchronous provider updates, idempotent and monotonic | §7.3 |
| H4 | "Campaign event log", not the audit chain | §9 |
| H5 | SQLCipher fail-closed tests | §2 |
| H6 | TG-JCS-v1 identifier kept; central prefix registry | §1 |
| H7 | Unscheduling discards the frozen generation | §6.2 |

## 0B. Findings folded into revisions 2 and 3

| # | Finding | Resolution |
|---|---|---|
| R1 | 🔴 A wrong SQLCipher key is accepted silently by `PRAGMA key`; failure shows only at the first read (**measured**, 4.12.0). | `open_comms_db` proves the key with a read before returning (§2). |
| R2 | 🔴 A new file opened without a key is created as **plaintext** (**measured**); an empty key raises. | Key must be exactly 32 bytes before `connect`; header asserted non-plaintext; `cipher_version` required (§2). |
| R3 | 🔴 Same-transport dedupe keyed on refs: `dst_A` and `rct_B` can both be Telegram peer 12345; one person may hold two WhatsApp points. | A canonical **delivery identity** per transport, produced by the transport's pure normalizer, controls dedupe and the idempotency key. **Refinement:** it is the ID of the chat actually sent to, *not* kind-qualified — `telegram:user:12345` vs `telegram:chat:12345` would re-open the bug, because a private chat's ID is the user's ID. At most one **enabled** contact point per recipient per transport (§3, §5.1). |
| R4 | 🔴 Provider events bound to a job, not an attempt; three independent writers of job state could regress `DELIVERED → ACCEPTED`; `SENT` could not become `PARTIAL` without breaking the lifecycle. | Events carry `(transport, provider_event_ref)` and the attempt; **one reducer** for every job-state write; campaign **lifecycle** separated from the derived **delivery summary** (§6, §7.3). |
| R5 | 🔴 `FAILED_TRANSIENT` could permit a blind resend when the provider has no idempotency. | `FAILED_TRANSIENT` is legal only when no send provably occurred, or the provider's idempotency makes this exact retry safe; every ambiguous failure is `OUTCOME_UNKNOWN` (§7.2). |
| R6 | 🔴 Unschedule deleted jobs, then an edited reschedule reused the same idempotency key for a different payload. | Every freeze creates a new immutable **generation** (`gen_`); the key binds it; an unscheduled generation is discarded, never reused (§7.1, §8). |
| R7 | 🔴 A crash after the last job outcome but before aggregation stranded the campaign in `SENDING`. | The summary is recomputed **in the same transaction** as every job change; recovery also re-derives it and closes abandoned attempts (§6.3, §10). |
| R8 | 🔴 An empty job set vacuously satisfied "every job succeeded". | Freeze refuses `NO_ELIGIBLE_ENDPOINTS` when no job would be `PENDING`; the summary is defined only over a non-empty set (§5.1, §6.3). |
| R9 | 🔴 Claim-time revalidation could not know *why* a contact point was targeted (which location, which audience). | Each job freezes its **origin paths**; revalidation keeps a job only if some frozen path is still valid now (§5.3). |
| R10 | 🟠 `deliver(PreparedDelivery)` vs "`DeliveryJob` is what deliver receives". | One boundary type, `FrozenDelivery` (§4). |
| R11 | 🟠 Endpoint identity mutability undefined. | Transport and platform identity are **immutable**; a change is disable-old, create-new (§3). |
| R12 | 🟠 Canary test covered only the main file. | Every file the database produces (WAL, SHM, journal, temp) is searched; `temp_store=MEMORY` (§2). |
| R13 | 🟠 `provider_event_ref` globally unique could collide across providers. | `UNIQUE(transport, provider_event_ref)` (§7.3). |
| R14 | 🟠 Snapshot digest preimage underspecified. | Exact domain and preimage pinned (§5.4). |
| R15 | 🟠 Journal and state could diverge on a crash. | Every event is appended in the transaction of its state change; `event_seq` is a global sequence (§9). |
| R16 | 🟠 Time representation. | UTC instants; naive datetimes refused; offsets normalized before persistence; comparisons on UTC (§8). |
| R17 | 🟠 The bounded model was optional. | Required, with named properties (§11). |
| R18 | 🟠 Wording: C1 said "no transport operation" in a transaction, but `prepare` runs inside the freeze. | "No external side effect or I/O"; `prepare` is pure computation and may run inside; `deliver` must not (§5). |
| R19 | 🟠 WhatsApp eligibility depends on send time (24-hour window). | `prepare` renders for the send time; pure `still_valid` at claim may only remove (§4). |
| R20 | 🟠 Claim, cancel and a second executor race on one job; recovery could misclassify a live executor's job. | Claim and cancel are compare-and-set; execution and recovery require the `ExecutorLease` (§5.2, §10). |
| R21 | 🟠 Nothing stopped an MCP surface importing the send library. | Import guard (§1). |
| R22 | 🟡 Retry past the cap, event digests over identities, freeze-transaction size. | `retry_exhausted`; digests over opaque refs only; a bounded freeze-size and timing test (§7.2, §9, §11). |

## 0C. Revision 4: consequences of the plan gauntlet

| # | Finding | Resolution |
|---|---|---|
| S1 | 🔴 "A delivery identity is owned by exactly one destination or contact point" made the R3 same-person case unconstructible, and made disable-old / create-new (R11) impossible for the same number. | A delivery-identity row is **shared**: creating an endpoint reuses the row for its `(transport, delivery_identity)`. At most one **enabled** destination and at most one **enabled** contact point per identity; freeze dedupes by identity (§3). |
| S2 | 🟠 "The target chat ID" was ambiguous across Telegram peer kinds: raw user, basic-group and channel IDs can coincide. | The Telegram delivery identity is the **marked** chat ID (user and private chat `N`; basic group `-N`; channel `-100N`). A user and their private chat are equal; a user and a group with the same raw number are not (§3). |
| S3 | 🔴 Purity said "no filesystem side effect"; a `still_valid` that reads a file, another database or the network would still run inside `BEGIN IMMEDIATE`. | `normalize`, `prepare`, `still_valid` perform **no I/O of any kind** (no network, filesystem read or write, other database, RPC, subprocess or `await`). A live provider check belongs in `deliver` (§4). |
| S4 | 🔴 A webhook arriving before the synchronous result persisted `provider_message_ref` was refused and lost. | An unmatched, well-formed provider update is recorded `pending_match`; binding a `provider_message_ref` to an attempt reconciles its pending updates through the reducer **in the same transaction**. An ambiguous match (two attempts) is refused (§7.3). |
| S5 | 🔴 The event's audience and recipient digests had the same preimage. | Three distinct commitments with their own domains: `target_digest` (what the operator selected), `recipient_digest` (what freeze resolved), `snapshot_digest` (§5.4). All over opaque refs (§9). |
| S6 | 🔴 Event privacy was checked by string shape: `{"reason": "hello"}` passed. | Each payload key has a fixed type and finite domain; there is no generic "code" bucket (§9). |
| S7 | 🔴 Transport isolation caught every exception in a transport's loop, turning core corruption into provider outcomes. | Only an exception raised **by `deliver`** becomes `OUTCOME_UNKNOWN`. Every other exception propagates and fails closed. Isolation means a persisted outcome on one transport never stops another (§5.2). |
| S8 | 🟠 Unscheduling "its jobs → CANCELLED" would move terminal skipped jobs, and a skipped job has no payload. | Unschedule and scheduled-cancel move only `PENDING` jobs to `CANCELLED`; the discarded generation is what retires the rest. The campaign's current generation is cleared (§6.2, §8). |
| S9 | 🟠 `recover` could not resume delivery without the transports. | `recover` repairs durable state and **returns** the campaigns with resumable `PENDING` work; the caller executes them (§10). |
| S10 | 🟠 A job's single `endpoint_ref` could not represent two endpoints sharing an identity, and nothing bound it to the job's identity. | The job keeps no endpoint ref; each **origin** stores its endpoint ref, and the database refuses an origin whose endpoint does not resolve to the job's `(transport, identity)` (§3, §5.3). |
| S11 | 🟡 A duplicate provider event with different contents was undefined. | Recorded as a conflict (`delivery.provider_update_refused`, reason `DUPLICATE_CONFLICT`); no state change (§7.3). |
| S12 | 🟠 "`OUTCOME_UNKNOWN` never resent" read as absolute, but resolution `not_sent` then retry is legitimate. | Property: an `OUTCOME_UNKNOWN` job gains another attempt only after an explicit `RESOLVE_NOT_SENT` (§11). |

## 1. Placement and the first shared-core extraction

- **`comms.core` gains domain code.** The empty-core guard is replaced by the rule that matters: core never imports `comms.transports.*`, `telegram_mcp` or `whatsvault`, statically or dynamically, and holds no transport path strings. The transitive-closure and planted-violation guards stay.
- **No AI surface can reach the send library (R21).** No module under `comms/transports/*/{server,dispatch,sensitive_dispatch}.py`, any `mcp` package, or WhatsVault's `apps/mcp` may import `comms.core.campaigns` or `comms.core.delivery`. The operator plane (5e) is the only caller of `send`, `schedule`, `retry_failed`, `resolve_outcome` and `cancel`.
- **Two single-copy moves, before any campaign code** (5b-3 Task 1's method: AST equality plus byte vectors): `transports/telegram/canonical.py` → `comms/core/canonical.py` (still **TG-JCS-v1** byte for byte; the identifier is not renamed) and `transports/telegram/opaque.py` → `comms/core/opaque.py`. Telegram imports both from core.
- **Prefix registry.** `comms/core/refs.py` declares the core's prefixes; a test requires them disjoint from Telegram's `REF_PREFIXES` (including tombstoned `tgu_`) and WhatsVault's `ids.PREFIXES`. The source spec's example `aud_` and `job_` collide with WhatsVault's audit-log and scheduled-job IDs.

| Object | Prefix | Object | Prefix |
|---|---|---|---|
| Location | `loc_` | Campaign | `cmp_` |
| Destination | `dst_` | Snapshot generation | `gen_` |
| Recipient | `rcp_` | Delivery job | `djb_` |
| Contact point | `rct_` | Delivery attempt | `dat_` |
| Audience | `cau_` | Campaign event | `cev_` |

## 2. Storage: `comms.db`

SQLCipher (`sqlcipher3` 0.6.2, pinned; measured `cipher_version` `4.12.0 community`). `open_comms_db(path, key: bytes)` is the only way to open the file:

1. refuse any key that is not exactly 32 bytes, **before** `connect` (R2);
2. `connect`; `PRAGMA key` as a raw hex key, before any other statement;
3. require a non-empty `PRAGMA cipher_version` (proves the library is SQLCipher);
4. prove the key by reading `sqlite_master`; a wrong key raises a fixed `CommsDbKeyError` (R1);
5. after creating a file, assert its first 16 bytes are not the plaintext SQLite header (R2);
6. `PRAGMA foreign_keys=ON`, `PRAGMA temp_store=MEMORY` (R12); every write in `BEGIN IMMEDIATE`.

Append-only numbered migrations (the Telegram `Migration(version, statements)` pattern) in `comms/core/storage/`. Platform identities are plain values **inside** the encrypted file so `UNIQUE` constraints work; everywhere outside — return values, events, logs, errors — only opaque refs appear.

**Tests (H5, R12):** no key and a wrong key fail closed with the fixed error; a keyless or short key is refused before any file exists; a phone-number canary and a message-body canary are absent from the bytes of **every** file in the database directory after writes, checkpoints and a crash (main, `-wal`, `-shm`, `-journal`, anything else); `cipher_version` is printed into the evidence.

**Schema constraints (pinned, not left to Python):** `CHECK` on every state column; `audience_members` has exactly one member-type column non-null (`CHECK`); `UNIQUE(job_id, attempt_no)`; `UNIQUE(transport, provider_event_ref)`; `UNIQUE(generation_id, transport, delivery_identity)` on jobs; foreign keys are `ON DELETE RESTRICT` throughout — destinations, contact points and recipients referenced by any job are **never deleted**, only disabled; identity columns are immutable (an `UPDATE` trigger refuses changes, R11).

## 3. Domain model (source §§4–7)

| Table | Holds |
|---|---|
| `locations` | `loc_`, name, `enabled` |
| `destinations` | `dst_`, `location_id`, transport (`telegram`), `platform_identity`, delivery identity (shared row), `display_name`, `enabled`, `capabilities`. A Telegram chat, group or channel. At most one enabled destination per identity. |
| `recipients` | `rcp_`, `enabled`. The logical person. |
| `contact_points` | `rct_`, `recipient_id`, transport, `platform_identity`, delivery identity (shared row), `enabled`, `opted_out_at`. Partial `UNIQUE(recipient_id, transport) WHERE enabled` (R3); at most one enabled contact point per identity (S1). A future SMS or email point is a row. |
| `delivery_identities` | `UNIQUE(transport, delivery_identity)`, shared by every endpoint that normalizes to it — at most one enabled destination and one enabled contact point (R3, S1). |
| `location_members` | location → recipient |
| `audiences`, `audience_members` | `cau_`; a member is exactly one of location, audience, destination, recipient |
| `campaigns` | `cmp_`, title, `lifecycle`, draft content (canonical/fa/en body, links, media descriptors `{sha256, mime, name, size}`), targets, options, current generation |
| `generations` | `gen_`, campaign, `created_at`, `send_at`, frozen content, snapshot digest, `status` (`active` / `discarded`). Immutable once written, except `status`. |
| `delivery_jobs` | `djb_`, generation, transport, `delivery_identity`, `idempotency_key UNIQUE`, frozen payload + digest, `state`, `attempt_count` (no endpoint ref: S10) |
| `job_origins` | job, ordered origin path (§5.3), its endpoint ref — which must resolve to the job's transport and identity (S10) |
| `delivery_attempts` | `dat_`, job, `attempt_no`, `started_at`, `finished_at`, `outcome`, `provider_message_ref` |
| `provider_events` | transport, `provider_event_ref`, `provider_message_ref`, job, attempt, reported status, disposition (`applied`, `recorded`, `refused`, `pending_match`) |
| `campaign_events` | the event log (§9) |

**Delivery identity (R3).** Each transport supplies a pure `normalize(platform_identity) -> delivery_identity`: the canonical identity of the thing a message is actually sent to on that transport. For Telegram it is the **marked** target chat ID: a user and their private chat are both `N`, a basic group is `-N`, a channel `-100N`, so a user and their private chat normalize equal and a group sharing the raw number does not (S2); for WhatsApp it is the E.164 number. Creating an endpoint reuses the identity row if one exists (S1). It is computed once when the endpoint is created and stored; it is immutable (R11).

**Operator authorization is not recipient eligibility.** Removing Touch ID removed a ceremony, not opt-outs: a disabled or opted-out contact point is never sendable, whatever the operator commands.

**Audiences** form a DAG; a write creating a cycle is refused, and resolution re-checks (fail closed). An unknown ref fails closed. "All locations" is an ordinary audience over the configured locations.

## 4. Transport adapter interface

```python
class DeliveryTransport(Protocol):
    name: str  # "telegram" | "whatsapp"

    def normalize(self, platform_identity: str) -> str: ...
    def prepare(self, intent: DeliveryIntent, send_at: datetime) -> PreparedPayload | Skip: ...
    def still_valid(self, payload: PreparedPayload, now: datetime) -> bool | str: ...
    def deliver(self, delivery: FrozenDelivery) -> DeliveryResult: ...
```

- **`normalize`, `prepare`, `still_valid` are pure computation**: no I/O of any kind — no network, no filesystem read or write, no other database, no RPC, no subprocess, no `await` — and deterministic for their inputs. They may run inside a transaction (R18, S3). Anything needing a live provider check belongs in `deliver`.
- **`prepare`** renders the transport payload from the frozen content **for the send time** (R19), decides locally knowable eligibility, and returns the payload (opaque bytes to the core) with its SHA-256, or a `Skip` with a fixed reason code.
- **`still_valid`** says at claim time whether the frozen payload is still permitted (for example, the WhatsApp 24-hour window closed). A falsy answer → `SKIPPED_REVALIDATION`. **Never re-render.**
- **`deliver`** is the only external side-effect boundary. It receives one **`FrozenDelivery`** (R10) — the exact 5d contract: job ref, generation ref, transport, delivery identity, frozen payload and digest, idempotency key, attempt number. It cannot change audience or content. It returns a `DeliveryResult` classified by §7.2.
- What is previewed is what is frozen: the snapshot digest (§5.4) covers every payload digest, so a rendering change between scheduling and execution cannot change what sends.

## 5. Send and schedule (source §10, §32)

**Rule (R18, pinned by test):** no external side effect, no network I/O, no `await` and no filesystem write happens inside a campaign-database transaction. Pure adapter computation may. A transport double records whether `deliver` or any I/O hook ran with a transaction open.

### 5.1 Freeze — one transaction

```text
BEGIN IMMEDIATE
  load campaign; require lifecycle READY (send or schedule)
  resolve targets through the audience DAG, recording every origin path (§5.3)
  keep only sendable endpoints (§5.3 rules, evaluated now)
  group by (transport, delivery_identity)  → one candidate per identity, all origin paths merged (R3)
  prepare(intent, send_at) each candidate  → PENDING job, or SKIPPED_PLATFORM_POLICY job
  if no job would be PENDING               → ROLLBACK, NO_ELIGIBLE_ENDPOINTS (R8)
  create generation gen_ (active); write jobs, origins, snapshot digest (§5.4)
  campaign → SENDING (send) or SCHEDULED (schedule); append event (§9)
COMMIT
```

Deduplication is within a transport, never across: one person may receive one Telegram copy and one WhatsApp copy.

### 5.2 Execute — the external-effect boundary

```text
BEGIN IMMEDIATE
  claim by compare-and-set: UPDATE job SET state='IN_FLIGHT'
      WHERE id=? AND state='PENDING' AND campaign lifecycle = 'SENDING'   (rowcount must be 1)
  revalidate (§5.3); on failure → SKIPPED_REVALIDATION instead
  insert attempt (attempt_no = attempt_count + 1); append event if any
COMMIT
deliver(frozen_delivery)                      ← external side effect; no transaction open
BEGIN IMMEDIATE
  reduce(job, attempt, result) (§7.3); recompute summary (§6.3); append events
COMMIT
```

Only the holder of the `ExecutorLease` (the single-runtime lock) may claim, execute or recover (R20). Transports are isolated: each runs its own loop, and a persisted outcome on one — failure, unknown, or an exception raised **by `deliver`**, which is `OUTCOME_UNKNOWN` — never stops another (source §29). Every other exception (database, revalidation, reducer, a programming error) propagates and fails closed; it is never converted into a provider outcome (S7).

### 5.3 Origin paths and revalidation (R9, H1)

Every job stores its **origin paths**: each is the ordered chain of refs by which the endpoint was reached, from a campaign target to the endpoint — for example `cau_A → cau_B → loc_X → rcp_P → rct_Q`, or `dst_D` targeted directly. A job has at least one.

A path is **valid now** if every node on it is enabled (location, destination, recipient, contact point, not opted out) and every edge still exists (audience membership, location membership, contact point ownership).

At claim time a job is kept only if **at least one of its frozen paths is still valid** and the adapter's `still_valid` holds. Otherwise it becomes `SKIPPED_REVALIDATION`. Consequences:

- removing a person from the targeted audience after scheduling suppresses them;
- disabling a location suppresses everyone reached only through it;
- **nothing is ever added**: no job is created after the freeze, so the execution set is always a subset of the frozen set.

### 5.4 Snapshot digest (R14)

```text
snapshot_digest = SHA256("comms-campaign-snapshot/v1\0" ‖ TG-JCS-v1({
  "campaign": cmp_, "generation": gen_, "send_at": <UTC>,
  "content_digest": SHA256(TG-JCS-v1(frozen content)),
  "transports": [sorted],
  "jobs": [sorted by (transport, delivery_identity):
           {"transport", "delivery_identity", "payload_digest", "initial_state", "skip_reason"}]
}))
```

The stability test fixes the clock and proves byte-identical digests across runs and processes, and a second test changes each field in turn and proves the digest moves.

## 6. State machines (R4, C3, D5)

### 6.1 Campaign lifecycle — what the campaign is doing

```text
DRAFT ⇄ READY                  (validate / edit)
READY → SCHEDULED              (schedule: freeze, §8)
SCHEDULED → READY              (unschedule: generation discarded, §8)
READY → SENDING                (send: freeze)
SCHEDULED → SENDING            (run_due, when due)
SENDING → COMPLETE             (no job PENDING or IN_FLIGHT; same transaction as the last job change)
COMPLETE → SENDING             (retry_failed returns jobs to PENDING)
DRAFT | READY | SCHEDULED → CANCELLED
```

- Content is editable only in `DRAFT`; `READY` returns to `DRAFT` to edit; `SCHEDULED` refuses every edit (D5).
- Nothing in `SENDING` or `COMPLETE` returns to `DRAFT`, `READY` or `SCHEDULED`, and the frozen generation of a sent campaign never changes.

### 6.2 Cancel

- `DRAFT`, `READY` → `CANCELLED`.
- `SCHEDULED`: the generation → `discarded`, its `PENDING` jobs → `CANCELLED` (kept, not deleted; skipped jobs stay skipped), the current generation cleared, campaign → `CANCELLED`, one transaction (S8).
- `SENDING`: **job** cancellation by compare-and-set: `PENDING → CANCELLED`; `IN_FLIGHT` and terminal jobs unchanged. Returns counts `cancelled_before_send`, `already_sent` (accepted or delivered) and `currently_in_flight`. The lifecycle becomes `COMPLETE` once nothing is pending or in flight; the summary says what happened.

### 6.3 Delivery summary — what actually happened (derived, R4, R7, R8)

The summary is a pure function of the generation's jobs, recomputed **in the same transaction as every job change** and stored for reading. The job set is never empty (R8). Over the jobs:

| Condition, in order | Summary |
|---|---|
| any job `PENDING` or `IN_FLIGHT` | `IN_PROGRESS` |
| any job `OUTCOME_UNKNOWN` | `INDETERMINATE` |
| every job `ACCEPTED` or `DELIVERED` | `SENT` |
| at least one `ACCEPTED`/`DELIVERED` | `PARTIAL` |
| no successes, at least one `CANCELLED`, and no attempt was ever made | `CANCELLED` |
| otherwise (no successes) | `FAILED` |

"Every job" means every job of the generation, skipped and cancelled included (so 372 accepted + 12 skipped = `PARTIAL`, source §14). Because the summary is derived, a late provider failure can move `SENT` to `PARTIAL`, and a late confirmation can move `INDETERMINATE` to `SENT`, **without** touching the lifecycle. **Unknown ≠ failed.**

### 6.4 Job

```text
PENDING → IN_FLIGHT → ACCEPTED | DELIVERED | FAILED_TRANSIENT | FAILED_PERMANENT | OUTCOME_UNKNOWN
PENDING → CANCELLED | SKIPPED_REVALIDATION
(frozen as)  SKIPPED_PLATFORM_POLICY
FAILED_TRANSIENT → PENDING         (retry_failed: new attempt, same job and key)
OUTCOME_UNKNOWN → ACCEPTED | FAILED_TRANSIENT    (operator resolution, §7.2)
```

## 7. Idempotency, attempts and outcomes

### 7.1 One logical job per frozen delivery (C4, R6)

```text
idempotency_key = SHA256("comms-delivery-idem/v1\0" ‖ TG-JCS-v1({
    "campaign": cmp_, "generation": gen_, "transport": t, "delivery_identity": id }))
```

`UNIQUE` for all time. A retry is a new **attempt** on the same job with the same key. A new generation (edit and reschedule) has new keys, so one key never describes two payloads. Discarded generations and their jobs are kept (as `discarded` / `CANCELLED`), never deleted, so a key is never reused. The platform identity enters the key only inside the hash, and the key itself appears only inside the encrypted database and the `FrozenDelivery` handed to the adapter.

### 7.2 Result classification, retry and resolution (R5)

`deliver` must classify, and 5d's adapter contract tests enforce it:

- **`FAILED_TRANSIENT`** only when (a) the adapter can prove no provider-side send occurred (for example, the connection was refused before any byte was written), or (b) the provider's idempotency mechanism makes resending this exact `FrozenDelivery` under this key safe.
- **`OUTCOME_UNKNOWN`** for everything ambiguous: timeouts, connection loss after writing, ambiguous HTTP responses, process failure mid-request, uncertain acknowledgement, and any exception.
- **`FAILED_PERMANENT`** when the provider definitively refused.

`retry_failed(cmp)` returns only `FAILED_TRANSIENT` jobs below the attempt cap (default 5) to `PENDING` (lifecycle `COMPLETE → SENDING`); a job at the cap is reported `retry_exhausted`. It never touches `OUTCOME_UNKNOWN`.

`resolve_outcome(job, "sent" | "not_sent")` is an operator statement about an `OUTCOME_UNKNOWN` job, recorded as an event: `sent` → `ACCEPTED`; `not_sent` → `FAILED_TRANSIENT`, which `retry_failed` may then pick up. Nothing resolves an unknown automatically except provider evidence (§7.3).

### 7.3 One reducer for every job-state write (R4)

Every write of a job's state — claim, `deliver`'s result, a provider update, an operator resolution, a cancel, a revalidation skip, a retry — goes through **one function**, `reduce(job, attempt, evidence)`, which records the evidence on its attempt and applies:

- **Rank:** `PENDING < IN_FLIGHT < {FAILED_TRANSIENT, OUTCOME_UNKNOWN} < ACCEPTED < DELIVERED`; `FAILED_PERMANENT`, `CANCELLED` and the skips are terminal.
- **Success evidence** (`ACCEPTED`, `DELIVERED`) from **any** attempt of the job raises the job's state if higher (a message delivered is delivered). `DELIVERED` never regresses.
- **Failure evidence** changes the job only if it concerns the job's **current** attempt; a late failure from an earlier attempt is recorded on that attempt and does not touch the job.
- A synchronous `ACCEPTED` arriving after a webhook's `DELIVERED` is recorded on the attempt and ignored for the job.
- `ACCEPTED → FAILED_PERMANENT` is allowed only on provider evidence for the current attempt.
- Anything else is refused and recorded (`delivery.provider_update_refused`).

`record_provider_update(transport, provider_event_ref, provider_message_ref, status)` is idempotent on `(transport, provider_event_ref)` (R13): an identical duplicate is a no-op; a duplicate with different contents is a conflict, refused and recorded with reason `DUPLICATE_CONFLICT` (S11). It resolves the attempt from `provider_message_ref`. An update that matches no attempt yet is recorded `pending_match` (S4): the provider may report before the synchronous result has persisted the reference. When a result binds a `provider_message_ref` to an attempt, the same transaction applies every `pending_match` update for that `(transport, provider_message_ref)` through the reducer, in arrival order. A reference matching two attempts is ambiguous and refused. 5d wires the webhooks; 5b-4 owns the reducer.

## 8. Scheduling (source §19, D5, C6, R6, R16)

- `schedule(cmp, at)`: the §5.1 freeze with `send_at = at`, lifecycle → `SCHEDULED`. Jobs are durable and the content is frozen at scheduling time.
- `unschedule(cmp)`: generation → `discarded`, its `PENDING` jobs → `CANCELLED` (S8), current generation cleared, lifecycle → `READY`, event `campaign.unscheduled` — one transaction. The next `schedule` creates a new generation with new keys.
- `run_due(lease, now)`: for each `SCHEDULED` campaign whose generation's `send_at ≤ now`, **first** moves the lifecycle to `SENDING` in its own transaction, then executes (§5.2) with revalidation (§5.3). No re-authorization.
- **Time gate:** a `PENDING` job is claimable only while its campaign's lifecycle is `SENDING` (enforced inside the claim's compare-and-set), so a restart on Thursday cannot send Friday's campaign.
- **Time:** every stored time is a UTC instant in ISO-8601 `Z`. Naive datetimes are refused at the API; offset-bearing inputs are normalized to UTC before persistence; every comparison is between UTC instants.

## 9. Campaign event log (H4, R15)

`campaign_events(event_seq INTEGER PRIMARY KEY, event_id cev_, campaign_ref, event_type, ts, payload)` is an **append-only operational event journal inside the encrypted `comms.db`. It is not the tamper-evident audit chain.** 5c integrates these events with the chain-backed audit system. `event_seq` is one global, monotonically increasing sequence.

**Every event is appended in the same transaction as the state change it records**: freeze + `campaign.send_started`/`campaign.scheduled`; unschedule + `campaign.unscheduled`; retry + `campaign.retry_started`; resolution + `delivery.outcome_resolved`; the lifecycle reaching `COMPLETE` + `campaign.completed` carrying the summary; summary changes after completion + `campaign.summary_changed`.

Events: `campaign.created`, `.modified`, `.validated`, `.scheduled`, `.unscheduled`, `.send_started`, `.transport_completed`, `.retry_started`, `.completed`, `.summary_changed`, `.cancelled`, `delivery.outcome_resolved`, `delivery.provider_update_refused`.

The payload may hold refs, digests, counts, transport and timestamps. Every digest is taken over opaque refs only, never over a platform identity. Each payload key has a fixed type and a finite domain (S6): refs of a named kind, 64-hex digests, non-negative counts, one of the two transports, a lifecycle or summary value, an ISO-Z time, or a member of a closed reason-code set. There is no free-form string.

Three commitments, each with its own domain (S5):

```text
target_digest    = SHA256("comms-campaign-target/v1\0"     ‖ TG-JCS-v1({"targets": {kind: [sorted refs]}, "transports": [sorted]}))
recipient_digest = SHA256("comms-campaign-recipients/v1\0" ‖ TG-JCS-v1([sorted by job ref: {"job", "transport", "endpoints": [sorted refs], "state"}]))
snapshot_digest  = §5.4
``` It must never hold a message body, phone number, raw Telegram ID, credential or private recipient data; a test plants each and proves absence from events, return values, exceptions and log records.

## 10. Recovery (source §32, R7, R20)

`recover(lease, now)` on startup, under the `ExecutorLease`, in one transaction per campaign:

1. every `IN_FLIGHT` job → `OUTCOME_UNKNOWN`, and its open attempt gets `outcome = OUTCOME_UNKNOWN`, `finished_at = now`;
2. every campaign in `SENDING` with no `PENDING` or `IN_FLIGHT` job → recompute the summary and move the lifecycle to `COMPLETE` (never stranded);
3. campaigns in `SENDING` that still have `PENDING` jobs are **returned** as resumable; the caller executes them (S9). `SCHEDULED` campaigns wait for `run_due`.

`ACCEPTED`/`DELIVERED` are never resent; `FAILED_PERMANENT` stays failed; `FAILED_TRANSIENT` only via `retry_failed`; `OUTCOME_UNKNOWN` only via `resolve_outcome` or provider evidence.

| Crash point | Truth after restart |
|---|---|
| before the freeze commits | nothing exists; nothing sent |
| after freeze, before claim | `PENDING`: safe to run |
| after claim, before `deliver` | `OUTCOME_UNKNOWN`: conservative |
| during `deliver` | `OUTCOME_UNKNOWN` |
| provider accepted, before the outcome commits | `OUTCOME_UNKNOWN`: never blind resend |
| after the last outcome commits | summary and lifecycle already committed with it; recovery re-derives them regardless |
| during unschedule / cancel / retry / resolution | the transaction either happened or did not |

A crash-injection seam (a constructor argument, dead in production, like the coordinator's `crash_at`) drives every row, and each row is a test.

## 11. Fakes and tests

- **Fake transports** (in `tests/`, not `src`): scriptable per delivery identity — accept, deliver, transient-proven-unsent, permanent, raise, ineligible at prepare, window-closes, crash after accept, late webhook, duplicate webhook — recording every call and whether a transaction was open.
- **Tests:** the source §31 list in full; R1/R2/R12 SQLCipher behaviours; R3 same-person-two-endpoints and two-WhatsApp-points; R4 webhook-before-return and late-attempt events; R5 classification; R6 edit-and-reschedule gives new keys; R7 the stranded-`SENDING` crash; R8 zero eligible endpoints; R9 membership removed after scheduling; R13 cross-provider event IDs; R14 digest stability and sensitivity; R15 journal atomicity under crash; R16 offsets and naive datetimes; R20 claim/cancel and double-claim races, lease required; R21 import guard; schema constraints (each `CHECK`/`UNIQUE`/`RESTRICT`/immutability trigger violated once).
- **Bounded model (required, R17):** an exhaustive exploration over campaigns of ≤ 3 jobs and ≤ 2 attempts per job, over every operation (freeze, claim, deliver results, provider updates including late and duplicate, cancel, retry, resolve, unschedule, reschedule, crash, recover, run_due with the clock before and after `send_at`), proving on every reachable state:
  - no job's state ever falls below `DELIVERED` once there;
  - an `OUTCOME_UNKNOWN` job gains another attempt only after an explicit `RESOLVE_NOT_SENT` (S12);
  - no job ever gains a second job for the same `(generation, transport, delivery_identity)`; no idempotency key ever names two payloads;
  - no scheduled campaign executes before `send_at`;
  - no endpoint appears after the freeze (execution set ⊆ frozen set);
  - no campaign is `SENDING` with only terminal jobs after `recover`;
  - an empty generation never exists; the summary is never `SENT` unless every job succeeded;
  - unknown never yields `FAILED`.

  Each property is mutation-tested (the guard it protects is removed and the exploration must find a violation), as in 5b-3's formal model.
- **Freeze size bound (R22):** a freeze of 5,000 endpoints completes inside a measured budget, recorded in the evidence; the plan fixes the number from a measurement, not a guess.

## 12. Out of scope

The CLI and admin commands, AI drafting tools and `.claude` rules (5e); real adapters, webhooks and adapter contract tests for §7.2 classification (5d); chain-backed audit, rotation and retention (5c); media byte storage (descriptors only); a daemon scheduler loop (5b-4 provides `run_due`).

## 13. Sequencing (one plan)

1. Move `canonical` and `opaque` into core; replace the empty-core guard; prefix registry; import guard.
2. `comms.db`: SQLCipher open, migrations, schema constraints, R1/R2/R12 tests.
3. Domain model, delivery identities, audience DAG.
4. Adapter interface and fake transports.
5. Freeze (send and schedule), origin paths, snapshot digest, the no-I/O rule.
6. Execute: claim, revalidation, isolation, the reducer.
7. Summary, cancel, retry, resolution, provider updates.
8. Scheduling, generations, recovery, the crash table.
9. Event log and privacy canaries.
10. Bounded model with mutation tests; freeze size bound.
11. Evidence: the source §31 checklist mapped to tests; full gate.
