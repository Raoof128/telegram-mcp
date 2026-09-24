# Comms 5b-4 Design: the Campaign Core (fake transports only)

**Status:** Revision 2. The owner approved the design on 2026-09-24 with six required corrections and further hardening (§0A). Revision 2 folds in the line-by-line gauntlet (§0B), whose SQLCipher findings were measured, not argued.
**Date:** 2026-09-24 (Australia/Sydney)
**Parent:** [`comms-consolidation-design.md`](2026-09-24-comms-consolidation-design.md) rev 2, §4.2; governed by [`docs/comms-spec-v0.2.md`](../../comms-spec-v0.2.md).
**Source requirements:** the owner's *Persian Society Communications Gateway v0.1*, §§4–19 and §32 (recovered verbatim into [`docs/provenance/comms-gateway-v0.1.md`](../../provenance/comms-gateway-v0.1.md)).

## Controlling statement

The campaign core turns "send this campaign" into durable, deduplicated, idempotent delivery jobs, and executes them against transports without ever resending blindly. **The campaign the operator can inspect is the campaign that will execute.** An operator command is the authorization; there is no second approval anywhere. Nothing here talks to Telegram or Meta: every transport in 5b-4 is a fake.

## 0. Owner decisions

| # | Decision |
|---|---|
| D1 | Storage: a new `comms.db`, SQLCipher. Core accepts a key; it never obtains one. 5e supplies it. |
| D2 | Code lives in `comms.core`. The "core stays empty" guard is retired for transport-neutral domain code; the one-way dependency rule is unchanged. |
| D3 | Surface: a Python library, fake transports and tests. The CLI, admin commands and AI drafting tools stay in 5e. |
| D4 | Lifecycle events go to a plain append-only journal now; 5c puts them on the tamper-evident chain. |
| D5 | `SCHEDULED` is immutable. To edit: unschedule, edit, validate, schedule again (deviation from source §19, approved). |

## 0A. Owner corrections and hardening (all adopted)

| # | Correction | Where |
|---|---|---|
| C1 | No transport operation inside a database transaction; `prepare()` is pure and local | §4, §5 |
| C2 | `INDETERMINATE` campaign outcome: unknown ≠ failed | §6.3 |
| C3 | Cancel and retry act on jobs; one explicit campaign state machine | §6 |
| C4 | One logical job per `(campaign, transport, endpoint)` forever; attempts in their own table | §3, §7 |
| C5 | Recipient (the person) is separate from contact points (per-transport endpoints) | §3 |
| C6 | Scheduled jobs are time-gated; recovery never runs a job early | §8 |
| H1 | Execution-time revalidation may remove, never add: execution set ⊆ frozen set | §5.3 |
| H2 | Adapter interface is `prepare` / `deliver`; the prepared payload and its digest are frozen | §4 |
| H3 | Asynchronous provider updates: idempotent, monotonic | §7.3 |
| H4 | "Campaign event log", explicitly not the audit chain | §9 |
| H5 | SQLCipher: no key and wrong key fail closed; a canary is absent from raw bytes; `cipher_version` recorded | §2 |
| H6 | `canonical.py` keeps the TG-JCS-v1 identifier; a central prefix registry prevents namespace collisions | §1 |
| H7 | Unscheduling destroys the frozen unsent snapshot and its jobs in one transaction | §6.2 |

## 0B. Gauntlet findings (revision 2)

| # | Sev | Finding | Receipt | Fix |
|---|---|---|---|---|
| G1 | 🔴 | A wrong SQLCipher key is **accepted silently** by `PRAGMA key`; failure appears only at the first read. | Measured on 4.12.0: `PRAGMA key` with a wrong key returns; `SELECT … FROM sqlite_master` then raises `file is not a database`. | `open_comms_db` proves the key inside open (a read of `sqlite_master`) and raises a fixed error before returning. |
| G2 | 🔴 | A **new file opened without a key is created as plaintext** SQLite; an empty key raises. | Measured: keyless create → header `SQLite format 3\0`. | `open_comms_db` refuses a key that is not exactly 32 bytes before `connect`, sets it first, and after creating a file asserts the header is not plaintext; `cipher_version` must be non-empty (proves the library really is SQLCipher). |
| G3 | 🔴 | **Same person, two Telegram endpoints.** A Telegram private chat's ID equals the user's ID, so a destination and a Telegram contact point can name one identity, and ref-based dedup sends twice. | Telegram Bot API: a private chat's `id` is the user's `id`. | Dedup keys on `(transport, platform_identity)`, not on refs. `UNIQUE(transport, platform_identity)` spans destinations **and** contact points (one `endpoints` identity table, §3). |
| G4 | 🔴 | Outcome states are not final: an `ACCEPTED` job can later report `FAILED_PERMANENT`, turning `SENT` into `PARTIAL`; revision 1's machine had no such edge. | §7.3's own lattice allows `ACCEPTED → FAILED_PERMANENT`. | The outcome (`SENT`/`PARTIAL`/`FAILED`/`INDETERMINATE`/`CANCELLED`-after-send) is a **pure function of the job states**, recomputed after every job change; re-aggregation may move between outcomes (§6.3). |
| G5 | 🟠 | A webhook can arrive **before `deliver` returns**; recording the synchronous result afterwards could regress `DELIVERED → ACCEPTED`. | Ordering is not guaranteed by any provider. | Every job-state write — `deliver` result, provider update, resolution — goes through one monotonic transition function; a lower-rank result on a higher-rank job is recorded on the attempt and ignored for the job. |
| G6 | 🟠 | Claim is only safe as **compare-and-set**. A cancel and a claim, or two executors, race on one `PENDING` job. | — | `UPDATE … SET state='IN_FLIGHT' WHERE job=? AND state='PENDING' AND <campaign SENDING>`; `rowcount == 1` or skip. Cancel is the same CAS. |
| G7 | 🟠 | `recover()` turns every `IN_FLIGHT` into `OUTCOME_UNKNOWN`; a **second live executor** mid-`deliver` would be misclassified. | — | `recover()` and execution require the caller to hold the single-runtime lock (5e wires it; 5b-4 takes an `ExecutorLease` object and the tests assert it is required). |
| G8 | 🟠 | **WhatsApp eligibility is time-dependent** (the 24-hour customer-service window decides free-form vs template), so a payload prepared Tuesday for Friday can be invalid on Friday. | Source §13; WhatsVault `conversation_windows`. | `prepare(intent, send_at)` renders for the **send time** (`scheduled_at`, or now). The adapter adds a pure `still_valid(prepared, now) -> bool | reason`, checked at claim; failure → `SKIPPED_REVALIDATION`. Never re-render: removal only. |
| G9 | 🟠 | Revision 1 left the **disabled-location rule** ambiguous for destinations reached directly through an audience. | Source §31 "disabled location excluded". | A destination is sendable only if it **and its location** are enabled, however it was reached. A disabled location contributes no members. A recipient is included only via an enabled path, and only if the recipient and the contact point are enabled and not opted out. |
| G10 | 🟠 | **Nothing stops an MCP surface importing the send library.** 5b-3's guard covers tool names, not imports. | `tests/security/test_ai_boundary.py` checks registries only. | New guard: nothing under `comms/transports/*/{server,dispatch,sensitive_dispatch,mcp}` or WhatsVault's `apps/mcp` imports `comms.core.campaigns` / `comms.core.delivery`. |
| G11 | 🟠 | "Optional bounded model, decided at plan time" was an unsigned IOU. | Doctrine: no IOU without a name. | Adopted: an exhaustive state-product test enumerates every multiset of job states for campaigns of ≤ 3 jobs and every operation, asserting aggregation, monotonicity and the unknown ≠ failed rule on each (§11). |
| G12 | 🟠 | Snapshot digest stability is ill-defined: `prepare` takes a time. | — | The snapshot digest covers the send time explicitly; the stability test fixes the clock and proves byte-identical digests across runs and processes. |
| G13 | 🟡 | `DeliveryJob` vs `PreparedDelivery` named the same thing twice. | §4. | One name: `deliver(job: DeliveryJob)`; `DeliveryJob` carries the frozen `PreparedDelivery`, the idempotency key and the attempt number. |
| G14 | 🟡 | "Every applicable job succeeded" — "applicable" undefined. | §6.3. | "Every job". A skipped or cancelled job makes the outcome at best `PARTIAL`, matching source §14 (372 accepted + 12 skipped = `PARTIAL`). |
| G15 | 🟡 | Retry past the cap was undefined. | §7.2. | At the cap a job stays `FAILED_TRANSIENT`, is excluded from `retry_failed`, and is reported as `retry_exhausted`. |
| G16 | 🟡 | Digests in the event log could be brute-forced if taken over phone numbers. | Source §20 allows `audience_digest`. | Every digest in events is over opaque refs only; a test proves no digest input contains a platform identity. |
| G17 | 🟡 | Times: source §19 uses a `+10:00` offset. | — | All stored times are UTC ISO-8601 `Z`; naive datetimes are refused at the API. |

## 1. Placement and the first shared-core extraction

- **`comms.core` gains domain code.** The empty-core guard (`test_core_has_no_production_implementation`) is replaced by the rule that matters: core never imports `comms.transports.*`, `telegram_mcp` or `whatsvault`, statically or dynamically, and holds no transport path strings. The transitive-closure and planted-violation guards stay.
- **No AI surface can reach the send library (G10).** No module under `comms/transports/*/{server,dispatch,sensitive_dispatch}.py`, any `mcp` package, or WhatsVault's `apps/mcp` may import `comms.core.campaigns` or `comms.core.delivery`, statically or dynamically. The operator plane (5e) is the only caller of `send`, `schedule`, `retry_failed`, `resolve_outcome` and `cancel`.
- **Two single-copy moves, before any campaign code** (same method as 5b-3 Task 1: AST equality plus byte vectors):
  - `transports/telegram/canonical.py` → `comms/core/canonical.py`. It still implements **TG-JCS-v1** byte for byte; moving the package does not rename the wire identifier.
  - `transports/telegram/opaque.py` → `comms/core/opaque.py`. Telegram imports both from core.
- **Prefix registry.** `comms/core/refs.py` declares the core's prefixes. A test requires them to be disjoint from Telegram's `REF_PREFIXES` (including tombstoned `tgu_`) and from WhatsVault's `ids.PREFIXES`. The source spec's example `aud_` and `job_` collide with WhatsVault's audit-log and scheduled-job IDs, so:

| Object | Prefix |
|---|---|
| Location | `loc_` |
| Destination | `dst_` |
| Recipient | `rcp_` |
| Contact point | `rct_` |
| Audience | `cau_` |
| Campaign | `cmp_` |
| Delivery job | `djb_` |
| Delivery attempt | `dat_` |
| Campaign event | `cev_` |

## 2. Storage: `comms.db`

- SQLCipher (`sqlcipher3` 0.6.2, already pinned; measured `cipher_version` `4.12.0 community`). `open_comms_db(path, key: bytes)` takes a 32-byte key from the caller and is the only way to open the file:
  1. refuse any key that is not exactly 32 bytes, before `connect` (G2);
  2. `connect`; set `PRAGMA key` as a raw hex key first, before any other statement;
  3. require a non-empty `PRAGMA cipher_version`;
  4. prove the key with a read of `sqlite_master`; a wrong key raises a fixed `CommsDbKeyError` (G1);
  5. after creating a new file, assert its first 16 bytes are not the plaintext SQLite header (G2);
  6. foreign keys on; every write in `BEGIN IMMEDIATE`.
- Append-only numbered migrations, the Telegram pattern (`Migration(version, statements)`), in `comms/core/storage/`.
- Platform identities (E.164 numbers, Telegram peer IDs) are stored as plain values **inside** the encrypted file, so `UNIQUE(transport, platform_identity)` works. Everywhere outside the database — return values, events, logs, errors — they appear only as opaque refs.
- **Tests (H5):** a fresh file has no SQLite plaintext header; opening without a key fails closed; opening with a wrong key fails closed; a planted canary (a phone number and a message body) is absent from the raw file bytes; `PRAGMA cipher_version` is printed into the evidence.

## 3. Domain model (source §§4–7)

| Table | Holds |
|---|---|
| `locations` | `loc_`, name, `enabled` |
| `endpoint_identities` | `(transport, platform_identity)` `UNIQUE`, owned by exactly one destination **or** one contact point (G3). |
| `destinations` | `dst_`, `location_id`, transport (`telegram`), identity, `display_name`, `enabled`, `capabilities`. A Telegram chat, group or channel. |
| `recipients` | `rcp_`, `enabled`. The logical person. |
| `contact_points` | `rct_`, `recipient_id`, transport, identity, `enabled`, `opted_out_at`. A future SMS or email point is a row, not a column. |
| `location_members` | location → recipient |
| `audiences`, `audience_members` | `cau_`; a member is exactly one of location, audience, destination, recipient |
| `campaigns` | `cmp_`, title, state, draft content (canonical/fa/en body, links, media descriptors `{sha256, mime, name, size}`), targets (audiences, locations, transports), options, `scheduled_at`, snapshot digest |
| `delivery_jobs` | `djb_`, campaign, transport, `endpoint_ref` (a `dst_` or `rct_`), `idempotency_key UNIQUE`, frozen prepared payload + digest, state, `attempt_count` |
| `delivery_attempts` | `dat_`, job, `attempt_no`, `started_at`, `finished_at`, outcome, provider message ref |
| `provider_events` | `provider_event_ref UNIQUE`, job, reported status |
| `campaign_events` | the event log (§9) |

**Operator authorization is not recipient eligibility.** Removing Touch ID removed a ceremony, not opt-outs: a contact point that is disabled or opted out is never sendable, whatever the operator commands.

**Sendability (G9).** A destination is sendable only if it and its location are enabled, however it was reached. A disabled location contributes no members. A contact point is sendable only if it, its recipient and at least one enabled path to it are enabled, and it is not opted out.

**Audiences** form a DAG. A write that would create a cycle is refused; resolution re-checks and refuses a cycle too (fail closed). An unknown ref fails closed. "All locations" is an ordinary audience whose members are the configured locations.

## 4. Transport adapter interface (H2, C1)

```python
class DeliveryTransport(Protocol):
    name: str  # "telegram" | "whatsapp"

    def prepare(self, intent: DeliveryIntent, send_at: datetime) -> PreparedDelivery | SkippedDelivery: ...
    def still_valid(self, prepared: PreparedDelivery, now: datetime) -> bool | str: ...
    def deliver(self, job: DeliveryJob) -> DeliveryResult: ...
```

- **`prepare`** is pure and local: no network, no filesystem side effect, deterministic for its inputs. It renders **for the send time** (`scheduled_at`, or now for an immediate send), because WhatsApp eligibility depends on it (G8). It renders the transport-specific payload from the frozen content, decides locally knowable eligibility, and returns the payload (opaque bytes to the core) with its SHA-256. A skip carries a fixed reason code.
- **`still_valid`** is pure and local: at claim time it says whether the frozen payload is still permitted (for example, the 24-hour window closed). `False` or a reason → `SKIPPED_REVALIDATION`. The payload is never re-rendered.
- **`deliver`** is the only side-effect boundary. It receives an already frozen job and cannot change audience or content. It returns `ACCEPTED`, `DELIVERED`, `FAILED_TRANSIENT` or `FAILED_PERMANENT` (plus an optional provider message ref). A provider refusing the message is a delivery outcome, not an eligibility check. An exception means the outcome is unknown.
- The core never interprets the payload. What is previewed is what is frozen: the snapshot digest covers every prepared payload digest, so a rendering change between scheduling and execution cannot alter what sends.
- `DeliveryJob` (what `deliver` receives: the frozen `PreparedDelivery`, the idempotency key, the attempt number) is the contract 5d freezes for the real adapters (G13).

## 5. Send (source §10, §32)

### 5.1 Freeze — one transaction, no I/O (C1)

```text
BEGIN IMMEDIATE
  load campaign; validate state (READY, or due SCHEDULED)
  resolve audiences and locations (skip disabled)
  resolve per-transport endpoints: telegram → destinations (+ telegram contact points);
                                   whatsapp → whatsapp contact points
  deduplicate within each transport on (transport, platform_identity) (G3; never across transports)
  prepare() each endpoint locally      → job, or SKIPPED_PLATFORM_POLICY job
  freeze snapshot: content + prepared payload digests; compute snapshot, audience and recipient digests
  write every delivery job (PENDING or SKIPPED_PLATFORM_POLICY)
  campaign → SENDING (or SCHEDULED, §8); event
COMMIT
```

**Rule, pinned by test:** no network I/O, no `await`, no filesystem side effect and no provider call happens between `BEGIN` and `COMMIT` of any campaign-database transaction. A transport double that records whether its `deliver` or any I/O hook was called while a transaction is open proves it.

### 5.2 Execute — the external-effect boundary

```text
BEGIN IMMEDIATE; claim by compare-and-set (G6): PENDING → IN_FLIGHT only if still PENDING
                and the campaign is SENDING; revalidate (§5.3); new attempt row; COMMIT
deliver(job)                                    ← external side effect, no transaction open
BEGIN IMMEDIATE; record outcome on job and attempt; COMMIT
```

Only a holder of the `ExecutorLease` (the single-runtime lock, G7) may claim, execute or recover. Transports are isolated: each transport's jobs run in their own loop, and an exception or failure in one never stops another (source §29).

### 5.3 Revalidation at claim time (H1)

Inside the claim transaction, the job's endpoint is re-checked against current sendability (§3, G9) and the adapter's pure `still_valid` (G8). A job that fails becomes `SKIPPED_REVALIDATION`. **Revalidation may only remove.** No job is ever created after the freeze, so the execution set is always a subset of the frozen set.

## 6. State machines (C2, C3, D5)

### 6.1 Campaign

```text
DRAFT ⇄ READY                 (validate / edit)
READY → SCHEDULED             (schedule: freezes, §8)
SCHEDULED → READY             (unschedule: destroys the frozen snapshot and jobs, H7)
READY → SENDING               (send)
SCHEDULED → SENDING           (run_due, when due)
SENDING → SENT | PARTIAL | FAILED | INDETERMINATE | CANCELLED   (aggregation, §6.3)
outcome ⇄ outcome                                              (re-aggregation after a job change, G4)
PARTIAL | FAILED | INDETERMINATE → SENDING                      (retry_failed puts jobs back to PENDING)
DRAFT | READY | SCHEDULED → CANCELLED
```

- Content is editable only in `DRAFT`; `READY` returns to `DRAFT` to edit. `SCHEDULED` refuses every edit.
- No state after `SENDING` ever returns to `DRAFT`, `READY` or `SCHEDULED`.

### 6.2 Cancel

- `DRAFT`, `READY`: → `CANCELLED`.
- `SCHEDULED`: the frozen snapshot and its jobs are deleted and the campaign → `CANCELLED`, in one transaction.
- `SENDING`: **job** cancellation. `PENDING → CANCELLED`; `IN_FLIGHT` unchanged; terminal jobs unchanged. Returns counts `cancelled_before_send`, `already_sent` (accepted or delivered) and `currently_in_flight`, then re-aggregates. A campaign that delivered 40 and cancelled 60 ends `PARTIAL`, never `CANCELLED`.

### 6.3 Aggregation — a pure function of the job states (G4)

While any job is `PENDING` or `IN_FLIGHT` the campaign is `SENDING`. Otherwise the outcome is recomputed from the jobs after **every** job change (outcome, provider update, resolution, cancel), so `SENT` can become `PARTIAL` if an accepted message later fails, and `INDETERMINATE` can become `SENT` when a webhook confirms.

| Condition, in order | Campaign |
|---|---|
| any job `OUTCOME_UNKNOWN` | `INDETERMINATE` |
| every job succeeded (`ACCEPTED`/`DELIVERED`) (G14) | `SENT` |
| at least one success, and at least one failure, skip or cancellation | `PARTIAL` |
| zero attempts ever left `PENDING` and every job is `CANCELLED` or skipped | `CANCELLED` if any job was cancelled, else `FAILED` (no eligible endpoints) |
| zero successes otherwise | `FAILED` |

**Unknown ≠ failed.** A campaign with three unknown jobs and nothing else is `INDETERMINATE`, not `FAILED`.

### 6.4 Job

```text
PENDING → IN_FLIGHT → ACCEPTED | DELIVERED | FAILED_TRANSIENT | FAILED_PERMANENT | OUTCOME_UNKNOWN
PENDING → CANCELLED | SKIPPED_REVALIDATION
(frozen as)  SKIPPED_PLATFORM_POLICY
FAILED_TRANSIENT → PENDING               (retry_failed: new attempt, same job and key)
OUTCOME_UNKNOWN → operator resolution    (§7.2)
provider updates                          (§7.3)
```

**One transition function (G5).** Every job-state write — `deliver`'s result, a provider update, an operator resolution, a cancel, a claim — goes through a single monotonic function over the lattice of §7.3. A result that would lower a job's rank (the synchronous `ACCEPTED` arriving after a webhook's `DELIVERED`) is recorded on the attempt and ignored for the job.

## 7. Idempotency, retry and outcomes (C4)

### 7.1 One logical job forever

`idempotency_key = SHA256("comms-delivery-idem/v1\0" ‖ JCS({"campaign": cmp_, "endpoint": dst_/rct_, "transport": t}))`, `UNIQUE` on `delivery_jobs` — for all time, not only while "active". Built from opaque refs, it can never reveal a phone number. A retry is a new **attempt** on the same job; it is never a new job. The key is what 5d passes to providers that accept one.

### 7.2 Retry and operator resolution

- `retry_failed(cmp)`: only `FAILED_TRANSIENT` jobs, under an attempt cap (default 5), return to `PENDING` (a job at the cap stays `FAILED_TRANSIENT` and is reported `retry_exhausted`, G15); the campaign returns to `SENDING`; content stays frozen. `ACCEPTED`, `DELIVERED`, `FAILED_PERMANENT`, skipped, cancelled and `OUTCOME_UNKNOWN` jobs are never retried by it.
- `resolve_outcome(job, "sent" | "not_sent")`: an operator statement about an `OUTCOME_UNKNOWN` job, recorded as an event. `sent` → `ACCEPTED`; `not_sent` → `FAILED_TRANSIENT`, which `retry_failed` may then pick up. Nothing resolves an unknown automatically. (5e surfaces it.)

### 7.3 Provider updates (H3)

`record_provider_update(job, provider_event_ref, status)` is idempotent on `provider_event_ref` (a duplicate is a no-op) and monotonic:

```text
IN_FLIGHT | OUTCOME_UNKNOWN → ACCEPTED | DELIVERED | FAILED_PERMANENT
ACCEPTED → DELIVERED | FAILED_PERMANENT
DELIVERED: terminal (no regression to ACCEPTED)
```

Anything else is refused and recorded. An update can move a campaign out of `INDETERMINATE` on re-aggregation. 5d wires the webhooks; 5b-4 owns the state machinery.

## 8. Scheduling (source §19, D5, C6)

- `schedule(cmp, at)`: runs the §5.1 freeze with the campaign → `SCHEDULED` and `scheduled_at = at`. Jobs are written `PENDING` at scheduling time, so the snapshot is durable and frozen.
- `unschedule(cmp)`: deletes the unsent snapshot and jobs, campaign → `READY`, event `campaign.unscheduled`, in one transaction (H7).
- `run_due(now)`: for each `SCHEDULED` campaign with `scheduled_at ≤ now` (injected clock), **first** transitions it to `SENDING` in its own transaction, then executes (§5.2 with §5.3 revalidation). No re-authorization.
- **Time gate, pinned by test:** a `PENDING` job is runnable only if its campaign is `SENDING`. Recovery never runs a job whose campaign is still `SCHEDULED`, so a restart on Thursday cannot send Friday's campaign.

## 9. Campaign event log (H4, source §20)

`campaign_events(event_id cev_, event_seq, campaign_ref, event_type, ts, payload)` is an **append-only operational event journal inside the encrypted `comms.db`. It is not the tamper-evident audit chain.** 5c integrates these events with the chain-backed audit system.

Events: `campaign.created`, `.modified`, `.validated`, `.scheduled`, `.unscheduled`, `.send_started`, `.transport_completed`, `.partial`, `.indeterminate`, `.completed`, `.failed`, `.cancelled`, `.retry_started`, `delivery.outcome_resolved`, `delivery.provider_update_refused`.

The payload may hold refs, digests, counts, transport and timestamps. Every digest is taken over opaque refs only, never over a platform identity (G16). All stored times are UTC ISO-8601 `Z`; naive datetimes are refused (G17). It must never hold a message body, phone number, raw Telegram ID, credential or private recipient data; a test plants each and proves absence from events, return values and log records.

## 10. Recovery (source §32)

`recover(lease, now)` on startup, under the `ExecutorLease` (G7):

| Found | Becomes |
|---|---|
| `IN_FLIGHT` | `OUTCOME_UNKNOWN` (the request may have escaped) |
| `PENDING`, campaign `SENDING` | resumes |
| `PENDING`, campaign `SCHEDULED` | untouched until due (§8) |
| `ACCEPTED`, `DELIVERED` | never resent |
| `FAILED_PERMANENT` | stays failed |
| `FAILED_TRANSIENT` | only via `retry_failed` |
| `OUTCOME_UNKNOWN` | only via `resolve_outcome` or a provider update |

**Crash semantics:**

| Crash point | Truth after restart |
|---|---|
| before the freeze commits | nothing exists; nothing sent |
| after freeze, before claim | `PENDING`: safe to run |
| after claim, before `deliver` | `OUTCOME_UNKNOWN`: conservative |
| during `deliver` | `OUTCOME_UNKNOWN` |
| provider accepted, before the outcome commits | `OUTCOME_UNKNOWN`: never blind resend |
| after the outcome commits | the recorded truth |

A crash-injection seam (constructor argument, dead in production, as the coordinator's `crash_at`) drives every row, and each is a test.

## 11. Fakes and tests

- **Fake transports** (`tests/`, not `src`): scriptable per endpoint — accept, deliver, transient, permanent, raise, ineligible at prepare, crash after accept — recording every call with whether a transaction was open.
- **Tests:** the source §31 list in full; the G1/G2 SQLCipher behaviours (wrong key, keyless create, empty key, header, `cipher_version`); the G3 same-person-two-endpoints case; G5 webhook-before-return; G6 cancel/claim and double-claim races; G7 lease required; G8 window closing between schedule and run; G10 import guard; the §10 crash table; every §6 transition, allowed and refused (schedule/cancel/retry/`OUTCOME_UNKNOWN` combinations exhaustively over the state product); audience DAG property tests (random DAGs resolve; any added back-edge is refused); snapshot-digest stability; execution set ⊆ frozen set under randomized revalidation; provider-update monotonicity and duplicate-webhook idempotence; the no-I/O-in-transaction rule; SQLCipher H5; privacy canaries; the prefix registry.
- **Exhaustive state-product test (G11):** every multiset of job states for campaigns of ≤ 3 jobs × every operation (claim, outcome, provider update, cancel, retry, resolve, recover) — asserting the transition function's monotonicity, the aggregation table, and that `OUTCOME_UNKNOWN` never yields `FAILED` or triggers a send. Snapshot-digest stability under a fixed clock (G12).

## 12. Out of scope

The CLI and admin commands, AI drafting tools and `.claude` rules (5e); real adapters and webhooks (5d); chain-backed audit, rotation and retention (5c); media byte storage (descriptors only); a daemon scheduler loop (5b-4 provides `run_due`).

## 13. Sequencing (one plan)

1. Move `canonical` and `opaque` into core (single copy; AST + bytes); replace the empty-core guard; prefix registry.
2. `comms.db`: SQLCipher open, migrations, H5 tests.
3. Domain model and audience DAG.
4. Adapter interface and fake transports.
5. Freeze (send and schedule) with the no-I/O rule.
6. Execute, isolation and revalidation.
7. Aggregation, cancel, retry, resolution, provider updates.
8. Scheduling and recovery; the crash table.
9. Event log and privacy canaries.
10. Evidence: §31 checklist mapped to tests; full gate.

Hammer hardest in the gauntlet: every crash point between `PENDING → IN_FLIGHT → external call → durable outcome`, and every transition combining schedule, cancel, retry and `OUTCOME_UNKNOWN`.
