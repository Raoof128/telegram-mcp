# WhatsVault Phase 3 — Sealed Ingest LOCAL CORE (STANDALONE EXECUTION PLAN)

> **Generated just-in-time** from spec §2/§7, the master roadmap, the **Corrections Ledger #1–#4, #35, #37–#42 (INV-CIPHERTEXT/INV-ACK cluster)**, and the **actual repo state** (crypto + schema + `doctor.advance_window` verified 2026-08-27, after Phase 2a `e9cd852`). Supersedes the roadmap's Phase 3 reference draft.

> **Scope:** the **local core** is fully TDD now against fakes/fixtures. The **Cloudflare Worker (TS), live Queue, and R2 spill are Phase-0-gated** (structured, not executed). ACK never waits on indexing.

> **For agentic workers:** superpowers:executing-plans. TDD iron law.

**Goal:** Durably ingest sealed WhatsApp webhook events into `vault.db` with ACK-after-commit, Mac-side fan-out, family dedupe, a key-health-aware poison/transient/systemic taxonomy, a two-tier DLQ, and idempotent control-projection reconciliation — Cloudflare holding only ciphertext.

## Bound to actual repo state (verified this session)

- **#1 envelope verified empirically:** `X25519 → HKDF-SHA256 → AES-256-GCM`, wire = `MAGIC(WVE1) || env_ver(1) || alg(1) || crypto_version(1) || recipient_key_id(4be) || event_id_hash(32) || ephemeral_pub(32) || nonce(12) || ct_len(4be) || ct‖tag`; **AAD = the header prefix through `event_id_hash`** (all AAD fields are ON the wire, fixing the original undecryptable bug). Round-trip, AAD-tamper→`InvalidTag`, no-key→`KeyUnavailable`, wrong-key→`InvalidTag` all confirmed.
- `doctor.advance_window(control_conn, conversation_id, incoming_provider_ms) -> int` exists: idempotent monotonic MAX via `ON CONFLICT` — this IS the #40 projection reconciler.
- `ingest.dedupe.message_key(provider, phone_number_id, wamid)` / `status_key(...)` exist; `ingest_events(semantic_event_key UNIQUE, family, raw_payload, ...)` is the dedup ledger (vault 0001).
- `ingest.status.reduce_status(events)` exists. `search.index.index_message(conn, id, text)` exists (#35). `ingest.normalise` does NOT exist yet.
- vault lane at version 4 → Phase 3 ingest = **vault migration 0005**.

## Ledger corrections folded in (binding)
- **#1** explicit envelope with all AAD fields on the wire + cross-impl golden vectors.
- **#2** fan-out is **Mac-side after decrypt**: `split_webhook(decrypted) -> list[AtomicEvent]`; each child has its own family/semantic_key/disposition; the queue message ACKs only after ALL children are durable.
- **#37** decrypt classification uses **key health**, not batch-cohort size: a lone `InvalidTag` on a key with no successful decrypt → SYSTEMIC (circuit-break, no ACK), never poison.
- **#38** local DLQ carries `recipient_key_id, crypto_version, envelope_version, ciphertext_sha256`; **no `payload_sha256`** on the decrypt-failure path (no plaintext exists).
- **#40** live `MESSAGE_INBOUND` advances `control.conversation_windows` via `advance_window` (idempotent, crash-recoverable, doctor-rebuildable) AFTER the evidence commit.
- **#41** circuit-breaker is concrete persisted state (`ingest_state`), tripped by SYSTEMIC/disk-full, reset by CLI; `doctor.check_ingest` reports it.
- **#42** `SYSTEM_EVENT` lives **solely in immutable `ingest_events`** (no separate domain row) — stated explicitly, tested.
- **#35** live-committed messages are indexed post-commit; ACK does not wait on indexing.
- **#3/#4** (R2 spill, Queue/DLQ config) are Worker-side, Phase-0-gated (Task 6, structured).

## Global constraints (§7)
- **INV-CIPHERTEXT/INV-EDGE-AAD**: Cloudflare holds only ciphertext; the header is AEAD AAD.
- **INV-ACK**: ACK iff durable local terminal disposition (ingested / deduped-as-seen / DLQ-quarantined). ACK ≠ "went well".
- `window_eligible=1` set ONLY by the live `MESSAGE_INBOUND` normaliser (never echo/history/import).
- DLQ diagnostics structured + bounded — never raw exception text, never payload excerpts.

---

### Task 1: Sealed-envelope crypto + golden vectors (#1)
**Files:** Create `src/whatsvault/crypto/sealed.py`; Test `tests/test_sealed.py`, `tests/golden/sealed_vectors.json`.
**Interfaces:** `sealed.seal(recipient_pub, plaintext, *, recipient_key_id, event_id_hash, crypto_version=1, algorithm_id=1) -> bytes`; `sealed.open_sealed(envelope, key_lookup) -> tuple[bytes, dict]` raising `KeyUnavailable`/`AeadAuthFailed`/`BadEnvelope` distinctly; `sealed.parse_header(envelope) -> dict` (no key needed — for DLQ metadata #38). Constants `MAGIC`, `ALG_X25519_HKDF_AESGCM=1`.
- [ ] Failing test: roundtrip recovers plaintext + header; flip any AAD byte → `AeadAuthFailed`; unknown key_id → `KeyUnavailable`; wrong key present → `AeadAuthFailed`; truncated/bad magic → `BadEnvelope`; a checked-in golden vector (fixed keys/nonce via injected RNG) decrypts to a fixed plaintext. → implement → commit `feat(3): sealed-envelope crypto with all AAD fields on the wire + golden vector (#1)`.

### Task 2: Webhook fan-out + six-family normaliser (#2, #42)
**Files:** Create `src/whatsvault/ingest/normalise.py`; Test `tests/test_ingest_normalise.py` + fixtures.
**Interfaces:** `normalise.split_webhook(payload: dict) -> list[dict]` (atomic events from nested `entry[].changes[].value.messages[]/statuses[]`); `normalise.classify(atomic) -> str` (`MESSAGE_INBOUND/MESSAGE_ECHO/MESSAGE_STATUS/HISTORY_EVENT/SYSTEM_EVENT/UNKNOWN_SUPPORTED`); `normalise.semantic_key(atomic) -> tuple[str,str]` `(family, dedupe_key)`; `normalise.to_rows(atomic) -> dict` (domain rows; `MESSAGE_INBOUND`→messages `origin='cloud_api'`,`window_eligible=1`, ts from provider **seconds**; `MESSAGE_ECHO`→`window_eligible=0`; `MESSAGE_STATUS`→status-event row; `SYSTEM_EVENT`/`HISTORY_EVENT`/`UNKNOWN`→no domain row, ingest_events only, #42).
- [ ] Failing test (provisional fixtures marked `PROVISIONAL` until Phase-0 V4/V7): one POST with a message + two statuses → 3 atomic events; only `MESSAGE_INBOUND` yields `window_eligible=1`; status → status-event row no message FK; system → no domain row; ts unit seconds→ms. → implement → commit `feat(3): Mac-side webhook fan-out + six-family normaliser (#2,#42)`.

### Task 3: Local DLQ + key-health decrypt taxonomy (vault migration 0005, #37, #38, #41)
**Files:** Create `src/whatsvault/db/migrations/vault/0005_ingest_ops.sql`; Modify `migrations/__init__.py`; Create `src/whatsvault/ingest/dlq.py`; Test `tests/test_ingest_dlq.py`.
**Migration:** `ingest_dlq(id, event_id_hash, envelope BLOB, failure_class, failure_code, pipeline_stage, recipient_key_id, crypto_version, envelope_version, ciphertext_sha256, parser_version, attempt_count, sanitised_detail, first_seen_ms, last_attempt_ms)`; `ingest_state(id INTEGER PRIMARY KEY CHECK(id=1), circuit_state TEXT NOT NULL DEFAULT 'CLOSED' CHECK (circuit_state IN ('CLOSED','OPEN')), tripped_at_ms, reason)` seeded one row.
**Interfaces:** `dlq.classify_decrypt_error(exc, *, key_healthy: bool) -> str` (`KEY_UNAVAILABLE`/`AEAD_AUTH_FAILED_ISOLATED`/`AEAD_AUTH_FAILED_SYSTEMIC`/`POISON_MALFORMED`); `dlq.quarantine(vault_conn, envelope, header, *, failure_class, failure_code, pipeline_stage, detail, now_ms)` (bounded `sanitised_detail`, no payload, ciphertext_sha256 from envelope); `dlq.trip/reset/state(vault_conn)`; `dlq.retry(vault_conn, key_lookup)`.
- [ ] Failing test: a lone `AeadAuthFailed` with `key_healthy=False` → SYSTEMIC (single-message-batch case, #37); with `key_healthy=True` → ISOLATED; `KeyUnavailable`→KEY_UNAVAILABLE; malformed→POISON; `quarantine` stores key_id + ciphertext_sha256 and NO payload hash, `sanitised_detail` ≤ cap with no payload substring; trip/reset toggles `ingest_state`. → implement → commit `feat(3): local DLQ + key-health decrypt taxonomy + circuit-breaker state (#37,#38,#41)`.

### Task 4: Pull-consumer drain loop — ACK-after-commit, fan-out, projection, index (#2,#35,#40,#41)
**Files:** Create `apps/ingest/consumer.py`, `apps/ingest/queue_client.py` (`FakeQueue`); Test `tests/test_ingest_consumer.py`.
**Interfaces:** `queue_client.QueueClient` Protocol (`lease/ack/to_dlq`) + `FakeQueue`; `consumer.drain_once(queue, vault_conn, control_conn, key_lookup, *, key_health:set, now_ms) -> dict`. Per message: open_sealed → (on success) `split_webhook` → per child `BEGIN IMMEDIATE` insert `ingest_events`(UNIQUE) → duplicate? commit-seen : normalise + domain writes → COMMIT → post-commit: `MESSAGE_INBOUND` → `advance_window` + `index_message`. ACK only after ALL children durable. Decrypt failures: KEY_UNAVAILABLE→no ACK; poison/isolated→DLQ+ACK; SYSTEMIC→trip breaker, stop leasing, no ACK. Breaker OPEN → drain refuses to lease.
- [ ] Failing test: valid inbound → committed+indexed+window advanced+ACKed; duplicate → deduped, ACKed, no 2nd row; message+2-statuses in one queue msg → all 3 durable before ACK; KEY_UNAVAILABLE → not ACKed (redelivered); crash-after-commit (raise before ACK) → redelivered duplicate absorbed by ledger; a lone wrong-key msg with cold key → SYSTEMIC, breaker OPEN, not ACKed. → implement → commit `feat(3): pull-consumer ACK-after-commit with fan-out, projection reconciliation, post-commit index (#2,#35,#40)`.

### Task 5: Key-retire safety + retention monitor + doctor (#39)
**Files:** Create `src/whatsvault/ingest/retention.py`, `src/whatsvault/keys.py`; Modify `doctor.py`; Test `tests/test_keys_retention.py`.
**Interfaces:** `keys.retire(vault_conn, recipient_key_id, *, edge_clear:bool) -> None` (raises `KeyStillReferenced` while local DLQ references the key or `edge_clear` is False — time/state-based, no queue scan, #39); `retention.assess(oldest_ms, now_ms, retention_days=14) -> str` (OK/WARNING/HIGH/CRITICAL); `doctor.check_ingest(vault_conn) -> list[dict]` (DLQ depth, oldest unresolved age, circuit-breaker state).
- [ ] Failing test: `retire` raises while local DLQ holds a ciphertext for that key; `assess` CRITICAL at 13/14 days; `check_ingest` reports breaker + DLQ depth. → implement → commit `feat(3): key-retirement safety, retention alerting, ingest doctor checks (#39,#41)`.

### Task 6: cf-webhook Worker — STRUCTURED, Phase-0-gated (#3, #4)
**Files:** `apps/cf-webhook/{src/index.ts,wrangler.jsonc,package.json}` (not run in CI).
- HMAC-verify raw body (constant-time) before parse; GET verify_token; POST-only, size gate. Seal with WebCrypto (X25519→HKDF→AES-GCM, header AAD) matching `sealed`; **>128 KB sealed body → encrypted R2 object + enqueue pointer** `{r2_object_id, ciphertext_sha256, recipient_key_id, crypto_version, event_id_hash}` (#3); normal path enqueues inline. Queue config: retry/DLQ on the **consumer**, explicit edge-DLQ drainer for the 14-day net (#4). **Build gate:** Miniflare probe confirms Worker WebCrypto X25519 (else P-256 fallback) + a TS-seal→Python-open cross-vector.
- [ ] Structured only; a `docs` note records the gate. No CI execution.

### Task 7: Full-suite gate + CHANGELOG
- [ ] `.venv/bin/pytest -q` + `pip check` green; CHANGELOG; commit `test: close out Phase 3 ingest local core (edge/R2/live Phase-0-gated)`.

## Self-review (consistency + ambiguity gate — required before execution)

- **SR-1 (Task 1):** golden vector is open-direction (`recipient_priv` + `envelope` → plaintext); `seal` stays randomized (cross-lang TS-seal→Py-open vector is the Phase-0-gated Task 6 gate).
- **SR-2 (Task 2/4):** `to_rows` is pure (family-tagged normalised fields); the consumer does get-or-create conversation/contact, insert, `advance_window`, post-commit index.
- **SR-3 (Task 4):** `key_health` is a session set — empty at cold start ⇒ conservative SYSTEMIC on unknown-key failures (never a false poison-ACK); post-commit index wrapped in try/except so it never blocks ACK.
- **SR-4 (Task 2):** SYSTEM_EVENT/HISTORY_EVENT/UNKNOWN_SUPPORTED write only `ingest_events`, no domain row (#42).

**Gate: PASS** — local core is execution-safe. Begin Task 1.
