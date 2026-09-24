# WhatsVault Phase 3 — Sealed Edge Relay + Ingest Implementation Plan

> **For agentic workers:** superpowers:subagent-driven-development / executing-plans. `- [ ]` steps.
> **Dependency split:** *implementation* depends on Phase 1a (fixture-driven, buildable now); *production activation* depends on Phase 0 (V4/V7/V14). Build and fully test the Python ingest core against fixture webhooks now; deploy the Worker and connect the live Queue only after Phase 0 passes.

**Goal:** Durably ingest sealed WhatsApp webhook events (inbound, echoes, status, history, system, unknown) into `vault.db` with ACK-after-commit, family-specific dedupe, a poison/transient/systemic failure taxonomy, and a two-tier DLQ.

**Architecture:** A Cloudflare Worker (`cf-webhook`, TS) verifies Meta's HMAC over the raw body, seals the payload (X25519→HKDF→AES-256-GCM, header as AAD), and enqueues ciphertext. A local pull-consumer daemon (`apps/ingest`, Python) drains the queue, decrypts with the Keychain private key, classifies, dedupes, and commits — then ACKs. Cloudflare persists only ciphertext (INV-CIPHERTEXT).

**Tech Stack:** Python (ingest, crypto) + TypeScript/Wrangler (Worker). Builds on Phase 1a.

**Spec:** §2.1–2.4, §3.4, §7, INV-CIPHERTEXT, INV-EDGE-AAD, INV-ACK.

## Global Constraints (spec §7, §2.4)

- **INV-CIPHERTEXT / INV-EDGE-AAD** — Cloudflare persistent storage holds only ciphertext sealed to a key whose private half is Keychain-only; the envelope header (`recipient_key_id`, `crypto_version`, `event_id_hash`) is bound as AEAD AAD.
- **INV-ACK** — ACK iff durable local terminal disposition (ingested / deduped-as-seen / DLQ-quarantined). ACK ≠ "went well".
- Decrypt failures split three ways: `KEY_UNAVAILABLE` (transient, no ACK), `AEAD_AUTH_FAILED_ISOLATED` (poison → local DLQ → ACK), `AEAD_AUTH_FAILED_SYSTEMIC` (circuit-break, no ACK).
- Failure taxonomy: duplicate→ACK; supported→commit→ACK; unknown-well-formed→`UNKNOWN_SUPPORTED` (exact decrypted JSON in SQLCipher)→ACK; poison→DLQ-commit→ACK; DB-locked→retry; disk-full→circuit-break; retry-exhaustion→Cloudflare DLQ (alert).
- Dedupe key inside the same `BEGIN IMMEDIATE` as domain writes (no pre-transaction dedupe race).
- Six event families from day one: `MESSAGE_INBOUND`, `MESSAGE_ECHO`, `MESSAGE_STATUS`, `HISTORY_EVENT`, `SYSTEM_EVENT`, `UNKNOWN_SUPPORTED`.
- DLQ diagnostics are **structured and bounded** — never raw exception text, never payload excerpts.
- `window_eligible = 1` is set **only** by the live `MESSAGE_INBOUND` normaliser (never echoes, history, imports).
- Key retirement refuses while any of {main queue, edge DLQ, local DLQ} still reference the key.

---

### Task 1: Sealed-envelope crypto (Python open side + AAD binding)

**Files:** Create `src/whatsvault/crypto/sealed.py`; Test `tests/test_sealed.py`.

**Interfaces:**
- `sealed.seal(recipient_pub: bytes, plaintext: bytes, header: dict) -> bytes` — X25519 ephemeral → HKDF-SHA256 → AES-256-GCM; envelope = `magic || ver || recipient_key_id || ephemeral_pub || nonce || ct||tag`; the serialised header (`recipient_key_id`, `crypto_version`, `event_id_hash`) is the GCM AAD. (Python impl mirrors what the Worker will do; used for tests and as the reference.)
- `sealed.open_sealed(recipient_priv: bytes, envelope: bytes, key_lookup) -> tuple[bytes, dict]` — raises `KeyUnavailable`, `AeadAuthFailed`, or `BadEnvelope` distinctly.

- [ ] **Step 1: Failing test** — seal→open roundtrip recovers plaintext + header; flipping a header byte (e.g. `recipient_key_id`) fails the tag (`AeadAuthFailed`) — proves AAD binding; an envelope whose `recipient_key_id` has no private key raises `KeyUnavailable` (transient), distinct from a tag failure (poison).
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** `sealed.py` (`cryptography` X25519 + HKDF + AESGCM). **Step 4: PASS.** **Step 5: Commit** `feat: sealed-envelope crypto with header AAD binding`.

---

### Task 2: Webhook normaliser (six families)

**Files:** Create `src/whatsvault/ingest/normalise.py`; Test `tests/test_ingest_normalise.py` + fixtures `tests/fixtures/webhooks/*.json`.

**Interfaces:**
- `normalise.classify(payload: dict) -> str` — one of the six families (or `UNKNOWN_SUPPORTED`).
- `normalise.to_rows(payload: dict) -> dict` — returns the domain rows to write: for `MESSAGE_INBOUND` a `messages` row with `origin='cloud_api'`, `window_eligible=1`, `ts` from provider **seconds**; for `MESSAGE_ECHO` `origin='business_app_echo'`, `window_eligible=0`; for `MESSAGE_STATUS` a `message_status_events` row; for `HISTORY_EVENT` `origin='history_sync'`, `window_eligible=0`; for `SYSTEM_EVENT` a system record; for unknown, the exact decrypted JSON bytes retained.
- `normalise.semantic_key(payload: dict) -> tuple[str, str]` — `(family, dedupe_key)` via `ingest.dedupe`.

- [ ] **Step 1: Failing test** — fixture payloads (captured shapes; until Phase 0 V4/V7 confirm the real shapes, use documented example payloads and mark the fixtures `PROVISIONAL`): each family classifies correctly; only `MESSAGE_INBOUND` yields `window_eligible=1`; a status payload maps to a status-event row with no message FK; an unrecognised-but-valid payload → `UNKNOWN_SUPPORTED`.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** `normalise.py`. **Step 4: PASS.** **Step 5: Commit** `feat: six-family webhook normaliser (window_eligible only for live inbound)`.
- **Phase-0 gate:** re-verify fixtures against real captured payloads (V4/V7) before production activation; timestamp unit **must** be confirmed seconds (V4).

---

### Task 3: Local DLQ + failure taxonomy

**Files:** Create `src/whatsvault/db/migrations/vault/0004_ingest_dlq.sql` (+ register); Create `src/whatsvault/ingest/dlq.py`; Test `tests/test_ingest_dlq.py`.

**Interfaces:**
- Migration adds `ingest_dlq(id, event_id_hash, ciphertext, failure_class, failure_code, pipeline_stage, exception_type, parser_version, crypto_version, payload_sha256, attempt_count, sanitised_detail, first_seen_ms, last_attempt_ms)`.
- `dlq.quarantine(vault_conn, *, event_id_hash, ciphertext, failure_class, ...) -> None` — commits a DLQ row; the structured diagnostics only (asserted: `sanitised_detail` is length-capped and contains no payload).
- `dlq.classify_decrypt_error(exc, cohort_ok: int) -> str` — maps to `KEY_UNAVAILABLE` / `AEAD_AUTH_FAILED_ISOLATED` / `AEAD_AUTH_FAILED_SYSTEMIC` using whether sibling envelopes in the batch decrypted.
- `dlq.retry(vault_conn, key_lookup) -> dict` — re-drive stored ciphertext through the pipeline after a parser upgrade / Keychain fix.

- [ ] **Step 1: Failing test** — a schema-invalid decrypted payload quarantines with a bounded `sanitised_detail` (assert no payload substring, length ≤ cap); `classify_decrypt_error` returns `SYSTEMIC` when the whole batch failed vs `ISOLATED` when one of many failed; `dlq.retry` re-processes a fixed row after the parser is taught the family.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** migration + `dlq.py`. **Step 4: PASS.** **Step 5: Commit** `feat: local DLQ with poison/transient/systemic taxonomy and bounded diagnostics`.

---

### Task 4: Pull-consumer ingest loop (ACK-after-commit)

**Files:** Create `apps/ingest/consumer.py`, `apps/ingest/queue_client.py` (with a `FakeQueue` for tests); Test `tests/test_ingest_consumer.py`.

**Interfaces:**
- `queue_client.QueueClient` (Protocol): `lease(max) -> list[LeasedMsg]`, `ack(lease_ids)`, `to_dlq(lease_ids)`; plus `queue_client.FakeQueue` for tests and (Phase-0-gated) `queue_client.CloudflarePullConsumer`.
- `consumer.drain_once(queue, vault_conn, key_lookup) -> dict` — per event: decrypt → schema → classify → `BEGIN IMMEDIATE` → insert `ingest_events` (UNIQUE) → duplicate?commit-seen : normalise+domain-writes+reconcile → COMMIT → collect ACK; poison → DLQ-commit → ACK; transient → no ACK; systemic → stop leasing (circuit-break). ACK only after commit.

- [ ] **Step 1: Failing test** — feed a `FakeQueue` with: a valid inbound (→ committed + ACKed), a duplicate of it (→ deduped, ACKed, no second row), a schema-invalid one (→ DLQ + ACK), a `KEY_UNAVAILABLE` one (→ **not** ACKed, redelivered next drain), and a status-before-message pair (status stored unreconciled, then reconciled when the message lands). Assert ACK-after-commit: simulate a crash between commit and ACK (raise after commit) and confirm the redelivered duplicate is absorbed by the dedupe ledger, not double-written.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** `consumer.py` + `FakeQueue`. **Step 4: PASS.** **Step 5: Commit** `feat: pull-consumer ingest loop with ACK-after-commit and dedupe-absorbed retries`.

---

### Task 5: cf-webhook Worker (TypeScript) — STRUCTURED, Phase-0-gated

**Files:** Create `apps/cf-webhook/{src/index.ts,wrangler.jsonc,package.json}`; Tests via `vitest` + Miniflare.

**Deliverables (not runnable against live Meta until Phase 0):**
- Worker verifies `X-Hub-Signature-256` HMAC over the **raw** body (constant-time) **before** parse; GET handshake validates `verify_token`; enforces POST-only, `Content-Type` allowlist, body ≤ 1 MB.
- Seals the payload with `sealed`-equivalent WebCrypto (X25519→HKDF→AES-256-GCM, header as AAD), enqueues to the bound Queue; never logs plaintext. **Build gate:** confirm the deployed Workers runtime's WebCrypto exposes X25519 `deriveBits` + HKDF + AES-GCM (the Python side is verified; a Miniflare/`wrangler dev` probe must confirm the Worker side before relying on it — if X25519 is unavailable, fall back to an ECDH P-256 sealed envelope, which is universally supported).
- `wrangler.jsonc`: main Queue producer binding + `max_retries: 100` + `dead_letter_queue: whatsvault-ingest-dlq`; 14-day retention (Phase-0 V14).
- Optional metadata-only daily ingress counter (D1), no content.

- [ ] **Tasks:** (5.1) HMAC verify + hardening with Miniflare tests (forged sig rejected, oversized rejected); (5.2) WebCrypto seal matching the Python `open_sealed` (cross-impl vector test: seal in TS, open in Python); (5.3) queue produce + wrangler config; (5.4) deploy dry-run (`wrangler deploy --dry-run`).
- **Phase-0 gate:** live subscription, real webhook delivery, and retention are activated only after V4/V7/V14. Do not point Meta at this Worker before then.

---

### Task 6: Key-rotation safety + retention monitor + doctor

**Files:** Create `src/whatsvault/ingest/retention.py`, `src/whatsvault/keys.py`; Modify `doctor.py`; Test `tests/test_keys_retention.py`.

**Interfaces:**
- `keys.retire(vault_conn, key_id, queue_refs_fn) -> None` — refuses (`KeyStillReferenced`) while main queue / edge DLQ / local DLQ reference the key.
- `retention.assess(oldest_message_ms, now_ms, retention_days=14) -> str` — `OK`/`WARNING`(50%)/`HIGH`(75%)/`CRITICAL`(90%).
- `doctor.check_ingest(vault_conn) -> list[dict]` — DLQ depth, oldest unresolved age, circuit-breaker state.

- [ ] **Step 1: Failing test** — `retire` raises while the local DLQ holds a ciphertext sealed to that key; `assess` returns CRITICAL at 13/14 days. **Step 2: FAIL. Step 3: Write. Step 4: PASS.** **Step 5: Commit** `feat: key-retirement safety, retention alerting, ingest doctor checks`.

---

### Task 7: Full-suite gate + changelog

- [ ] `.venv/bin/pytest -q` (Python core green). Note in CHANGELOG which tasks are Phase-0-gated for production. Commit `test: close out Phase 3 ingest core (edge deploy pending Phase 0)`.

## Self-Review

- Spec §7 coverage: sealed crypto + AAD → Task 1; six families + window_eligible discipline → Task 2; DLQ taxonomy (3 decrypt classes, bounded diagnostics) → Task 3; ACK-after-commit + dedupe-absorbed retries + status-before-message → Task 4; Worker hardening + cross-impl seal vector → Task 5; key-retire safety + retention → Task 6.
- Adversarial gate (spec §9 Phase 3): forged webhook sig (Task 5.1), replay/duplicate (Task 4), AAD tamper (Task 1), DLQ recovery (Task 3), key-rotation (Task 6), retention (Task 6).
- **Honest calibration:** Tasks 1–4, 6 are fully TDD and buildable now against fixtures/fakes. Task 5 (TS Worker) and all *live* activation are explicitly Phase-0-gated — fixtures are marked `PROVISIONAL` until real payloads (V4/V7) and retention (V14) are confirmed. This is deliberate: writing production tests against unverified provider behaviour is the anti-pattern the design forbids.
