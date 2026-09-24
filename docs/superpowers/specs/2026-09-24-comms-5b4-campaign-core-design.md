# Comms 5b-4 Design: the Campaign Core (fake transports only)

**Status:** Revision 1. The owner approved the design on 2026-09-24 with six required corrections and further hardening (§0A), all folded in here.
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

## 1. Placement and the first shared-core extraction

- **`comms.core` gains domain code.** The empty-core guard (`test_core_has_no_production_implementation`) is replaced by the rule that matters: core never imports `comms.transports.*`, `telegram_mcp` or `whatsvault`, statically or dynamically, and holds no transport path strings. The transitive-closure and planted-violation guards stay.
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

- SQLCipher (`sqlcipher3` 0.6.2, already pinned). `open_comms_db(path, key: bytes)` takes a 32-byte key from the caller. Foreign keys on; immediate transactions for every write.
- Append-only numbered migrations, the Telegram pattern (`Migration(version, statements)`), in `comms/core/storage/`.
- Platform identities (E.164 numbers, Telegram peer IDs) are stored as plain values **inside** the encrypted file, so `UNIQUE(transport, platform_identity)` works. Everywhere outside the database — return values, events, logs, errors — they appear only as opaque refs.
- **Tests (H5):** a fresh file has no SQLite plaintext header; opening without a key fails closed; opening with a wrong key fails closed; a planted canary (a phone number and a message body) is absent from the raw file bytes; `PRAGMA cipher_version` is printed into the evidence.

## 3. Domain model (source §§4–7)

| Table | Holds |
|---|---|
| `locations` | `loc_`, name, `enabled` |
| `destinations` | `dst_`, `location_id`, transport (`telegram`), `platform_identity`, `display_name`, `enabled`, `capabilities`. A Telegram chat, group or channel. |
| `recipients` | `rcp_`, `enabled`. The logical person. |
| `contact_points` | `rct_`, `recipient_id`, transport, `platform_identity`, `enabled`, `opted_out_at`. `UNIQUE(transport, platform_identity)`. A future SMS or email point is a row, not a column. |
| `location_members` | location → recipient |
| `audiences`, `audience_members` | `cau_`; a member is exactly one of location, audience, destination, recipient |
| `campaigns` | `cmp_`, title, state, draft content (canonical/fa/en body, links, media descriptors `{sha256, mime, name, size}`), targets (audiences, locations, transports), options, `scheduled_at`, snapshot digest |
| `delivery_jobs` | `djb_`, campaign, transport, `endpoint_ref` (a `dst_` or `rct_`), `idempotency_key UNIQUE`, frozen prepared payload + digest, state, `attempt_count` |
| `delivery_attempts` | `dat_`, job, `attempt_no`, `started_at`, `finished_at`, outcome, provider message ref |
| `provider_events` | `provider_event_ref UNIQUE`, job, reported status |
| `campaign_events` | the event log (§9) |

**Operator authorization is not recipient eligibility.** Removing Touch ID removed a ceremony, not opt-outs: a contact point that is disabled or opted out is never sendable, whatever the operator commands.

**Audiences** form a DAG. A write that would create a cycle is refused; resolution re-checks and refuses a cycle too (fail closed). An unknown ref fails closed. "All locations" is an ordinary audience whose members are the configured locations.

## 4. Transport adapter interface (H2, C1)

```python
class DeliveryTransport(Protocol):
    name: str  # "telegram" | "whatsapp"

    def prepare(self, intent: DeliveryIntent, now: datetime) -> PreparedDelivery | SkippedDelivery: ...
    def deliver(self, delivery: PreparedDelivery) -> DeliveryResult: ...
```

- **`prepare`** is pure and local: no network, no filesystem side effect, deterministic for its inputs. It renders the transport-specific payload from the frozen content, decides locally knowable eligibility, and returns the payload (opaque bytes to the core) with its SHA-256. A skip carries a fixed reason code.
- **`deliver`** is the only side-effect boundary. It receives an already frozen job and cannot change audience or content. It returns `ACCEPTED`, `DELIVERED`, `FAILED_TRANSIENT` or `FAILED_PERMANENT` (plus an optional provider message ref). A provider refusing the message is a delivery outcome, not an eligibility check. An exception means the outcome is unknown.
- The core never interprets the payload. What is previewed is what is frozen: the snapshot digest covers every prepared payload digest, so a rendering change between scheduling and execution cannot alter what sends.
- `DeliveryJob` (what `deliver` receives) is the contract 5d freezes for the real adapters.

## 5. Send (source §10, §32)

### 5.1 Freeze — one transaction, no I/O (C1)

```text
BEGIN IMMEDIATE
  load campaign; validate state (READY, or due SCHEDULED)
  resolve audiences and locations (skip disabled)
  resolve per-transport endpoints: telegram → destinations (+ telegram contact points);
                                   whatsapp → whatsapp contact points
  deduplicate within each transport (never across transports)
  prepare() each endpoint locally      → job, or SKIPPED_PLATFORM_POLICY job
  freeze snapshot: content + prepared payload digests; compute snapshot, audience and recipient digests
  write every delivery job (PENDING or SKIPPED_PLATFORM_POLICY)
  campaign → SENDING (or SCHEDULED, §8); event
COMMIT
```

**Rule, pinned by test:** no network I/O, no `await`, no filesystem side effect and no provider call happens between `BEGIN` and `COMMIT` of any campaign-database transaction. A transport double that records whether its `deliver` or any I/O hook was called while a transaction is open proves it.

### 5.2 Execute — the external-effect boundary

```text
BEGIN IMMEDIATE; claim: PENDING → IN_FLIGHT, new attempt row; COMMIT
deliver(job)                                    ← external side effect, no transaction open
BEGIN IMMEDIATE; record outcome on job and attempt; COMMIT
```

Transports are isolated: each transport's jobs run in their own loop, and an exception or failure in one never stops another (source §29).

### 5.3 Revalidation at claim time (H1)

Before claiming, the job's endpoint is re-checked against current state (destination/contact point enabled, not opted out, location enabled). A job that fails becomes `SKIPPED_REVALIDATION`. **Revalidation may only remove.** No job is ever created after the freeze, so the execution set is always a subset of the frozen set.

## 6. State machines (C2, C3, D5)

### 6.1 Campaign

```text
DRAFT ⇄ READY                 (validate / edit)
READY → SCHEDULED             (schedule: freezes, §8)
SCHEDULED → READY             (unschedule: destroys the frozen snapshot and jobs, H7)
READY → SENDING               (send)
SCHEDULED → SENDING           (run_due, when due)
SENDING → SENT | PARTIAL | FAILED | INDETERMINATE | CANCELLED   (aggregation, §6.3)
PARTIAL | FAILED | INDETERMINATE → SENDING                      (retry_failed / resolve_outcome, §7.2)
DRAFT | READY | SCHEDULED → CANCELLED
```

- Content is editable only in `DRAFT`; `READY` returns to `DRAFT` to edit. `SCHEDULED` refuses every edit.
- No state after `SENDING` ever returns to `DRAFT`, `READY` or `SCHEDULED`.

### 6.2 Cancel

- `DRAFT`, `READY`: → `CANCELLED`.
- `SCHEDULED`: the frozen snapshot and its jobs are deleted and the campaign → `CANCELLED`, in one transaction.
- `SENDING`: **job** cancellation. `PENDING → CANCELLED`; `IN_FLIGHT` unchanged; terminal jobs unchanged. Returns counts `cancelled_before_send`, `already_sent` (accepted or delivered) and `currently_in_flight`, then re-aggregates. A campaign that delivered 40 and cancelled 60 ends `PARTIAL`, never `CANCELLED`.

### 6.3 Aggregation — only when no job is `PENDING` or `IN_FLIGHT`

| Condition, in order | Campaign |
|---|---|
| any job `OUTCOME_UNKNOWN` | `INDETERMINATE` |
| every applicable job succeeded (`ACCEPTED`/`DELIVERED`) | `SENT` |
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

## 7. Idempotency, retry and outcomes (C4)

### 7.1 One logical job forever

`idempotency_key = SHA256("comms-delivery-idem/v1\0" ‖ JCS({"campaign": cmp_, "endpoint": dst_/rct_, "transport": t}))`, `UNIQUE` on `delivery_jobs` — for all time, not only while "active". Built from opaque refs, it can never reveal a phone number. A retry is a new **attempt** on the same job; it is never a new job. The key is what 5d passes to providers that accept one.

### 7.2 Retry and operator resolution

- `retry_failed(cmp)`: only `FAILED_TRANSIENT` jobs, under an attempt cap (default 5), return to `PENDING`; the campaign returns to `SENDING`; content stays frozen. `ACCEPTED`, `DELIVERED`, `FAILED_PERMANENT`, skipped, cancelled and `OUTCOME_UNKNOWN` jobs are never retried by it.
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

The payload may hold refs, digests, counts, transport and timestamps. It must never hold a message body, phone number, raw Telegram ID, credential or private recipient data; a test plants each and proves absence from events, return values and log records.

## 10. Recovery (source §32)

`recover(now)` on startup:

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
- **Tests:** the source §31 list in full; the §10 crash table; every §6 transition, allowed and refused (schedule/cancel/retry/`OUTCOME_UNKNOWN` combinations exhaustively over the state product); audience DAG property tests (random DAGs resolve; any added back-edge is refused); snapshot-digest stability; execution set ⊆ frozen set under randomized revalidation; provider-update monotonicity and duplicate-webhook idempotence; the no-I/O-in-transaction rule; SQLCipher H5; privacy canaries; the prefix registry.
- A small bounded model of the job/campaign state machine is an optional hardening, decided at plan time.

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
