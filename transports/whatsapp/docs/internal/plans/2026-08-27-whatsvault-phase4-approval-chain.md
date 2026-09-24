# WhatsVault Phase 4 — Approval Chain Implementation Plan

> **For agentic workers:** superpowers:subagent-driven-development / executing-plans. `- [ ]` steps.
> **Dependency split:** the canonical encoding, signature verification, sender state machine, and policy engine are fully TDD-testable now (fixture keypair, fake Meta) on Phases 1a+3. The **iOS app**, **Secure Enclave signing**, **device sealing over Tunnel**, and **live Meta sends** are Phase-0-gated (V1–V3, V8, V12) and need a paid Apple Developer account.

**Goal:** Implement the load-bearing security boundary: no WhatsApp write without a fresh, hardware-backed, biometric-authorised P-256 signature over the exact immutable draft — verified server-side before every send, under current policy.

**Architecture:** MCP `prepare_message` writes an immutable draft (nonce, expiry, P7-bound fields). The iPhone fetches device-sealed draft detail over Tunnel+Access, renders it (with a confusables/bidi guard), and on Face ID signs `WHATSVAULT-DRAFT-DECISION-V1` with a Secure Enclave P-256 key. `whatsvault-meta` verifies the signature + policy inside the nonce-consuming transaction, then sends. Approval triggers send; the model has no dispatch verb.

**Tech Stack:** Python (encoding, verify, sender, policy) + Swift/iOS (approval app). Builds on 1a+2+3.

**Spec:** §1 (INV-APPROVAL/SIGNATURE/HARDWARE/DISPLAY), §5.2/5.6/5.7, §6 in full.

## Global Constraints (spec §6)

- **INV-SIGNATURE / INV-HARDWARE** — a DB state is never proof of approval; every send needs a valid, unexpired, single-use P-256 signature over the exact recipient + immutable payload, from a Secure Enclave key inaccessible to the Mac/MCP/sender. Strength claim: hardware-backed non-exportable key gated by enrolled biometrics — **not** "root can't approve".
- **INV-DISPLAY** — the signature binds bytes; the human approves glyphs. A body with hidden/spoofed content (bidi, confusables, zero-width) must be flagged before the one-tap path (C1/R1 residual named).
- **Canonical `WHATSVAULT-DRAFT-DECISION-V1`** — length-prefixed binary; `decision` (`APPROVE`/`REJECT`) near the front; raw 32-byte nonce/hashes; sign the payload bytes directly (CryptoKit `signature(for:)` hashes internally; Python verifies with `ec.ECDSA(SHA256)`, not Prehashed); signature transported raw `r||s` (64B), Python reconstructs DER via `encode_dss_signature`; domain-separation prefix mandatory; replay identity is `device_id + nonce`, never the signature bytes.
- **Permission-to-transmit transaction** (`BEGIN IMMEDIATE`): verify ECDSA over freshly recomputed payload → `decision==APPROVE` → device ACTIVE now → body_sha256 match → recipient match → account/phone binding (P7) → not expired (Mac clock) → **re-evaluate P1–P7** → consume nonce (UNIQUE) → open send_attempt → COMMIT → then POST. All HTTP auto-retries disabled.
- **No `send_prepared_message` tool.** Approval triggers dispatch. `whatsvault-meta` IPC = `execute_write(signed_envelope)` (self-authenticating) + `materialise_media(attachment_id)` (caller restricted to ingest).
- **Clock integrity (H2):** NTP required; refuse sends on clock discontinuity.
- **Enrolment (H3/R3):** CLI-only device pinning; highest-trust local op.
- `biz_opaque_callback_data = "wv1:<atm_id>"` (Phase-0 V8); INDETERMINATE never auto-retried; `ABANDONED_INDETERMINATE` terminal.

---

### Task 1: Canonical encoding + cross-language golden vectors

**Files:** Create `src/whatsvault/approval/canonical.py`, `tests/golden/vectors.json`; Test `tests/test_canonical.py`.

**Interfaces:**
- `canonical.encode(fields: dict) -> bytes` — `"WHATSVAULT-DRAFT-DECISION-V1\n"` + per-field `uint32be(len)||bytes` in the fixed order: `version(uint16be)`, `decision`, `draft_id`, `account_id`, `phone_number_id`, `recipient_wa_id`, `body_sha256`(32), `kind`, `template_id`, `template_params_sha256`(32/empty), `reply_to_wamid`, `attachments_digest`(32), `nonce`(32), `created_at_ms`(uint64be), `expires_at_ms`(uint64be), `device_id`. Absent optionals = zero-length (never omitted); required zero-length rejected.
- `canonical.attachments_digest(items: list[dict]) -> bytes` — `SHA256("WHATSVAULT-ATTACHMENTS-V1\n" || per-item canonical(ordinal, content_sha256, mime, size[, filename]))`; defined empty-list constant.

- [ ] **Step 1: Failing test** — a fixed field set encodes to a fixed byte string (checked into `vectors.json`); an absent optional produces a zero-length slot (not omission); two drafts differing only in `decision` encode differently; the empty attachments digest equals the defined constant. Include vectors for ASCII, Persian, mixed, emoji, quotes/newlines, max-length, attachment, template, reply.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** `canonical.py`. **Step 4: PASS.** **Step 5: Commit** `feat: WHATSVAULT-DRAFT-DECISION-V1 canonical encoding + golden vectors`.
- **Swift parity (Task 8):** the same `vectors.json` is the cross-language gate — the Swift encoder must reproduce every vector byte-for-byte.

---

### Task 2: P-256 signature verification (fixture keypair)

**Files:** Create `src/whatsvault/approval/verify.py`; Test `tests/test_verify.py`.

**Interfaces:**
- `verify.verify(payload: bytes, signature_rs: bytes, public_key_sec1: bytes) -> bool` — raw `r||s` (64B) → `encode_dss_signature` → verify with `ec.ECDSA(hashes.SHA256())`; public key from SEC1 uncompressed point.
- `verify.sign_for_test(payload: bytes, private_key) -> bytes` — a **software** P-256 signer (tests only; production key is Secure Enclave) producing raw `r||s`. NOTE: `cryptography`'s ECDSA is randomised — signing the same payload twice yields different signatures (verified). That is *why* signature bytes are not a replay key; replay identity is `device_id + nonce`. The golden vectors (Task 1) fix the *payload* bytes, never the signature.

- [ ] **Step 1: Failing test** — sign_for_test → verify roundtrip passes; a one-byte payload mutation, a recipient mutation, an expiry mutation, and a `decision=REJECT`-vs-`APPROVE` swap all fail verification; a signature from a different keypair fails; the same payload signed twice yields different signatures (ECDSA randomised) yet both verify — so signature bytes are NOT a replay key.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** `verify.py`. **Step 4: PASS.** **Step 5: Commit** `feat: P-256 signature verification with raw r||s handling`.

---

### Task 3: Display guard (confusables / bidi / zero-width)

**Files:** Create `src/whatsvault/approval/display_guard.py`; Test `tests/test_display_guard.py`.

**Interfaces:**
- `display_guard.scan(body_text: str) -> dict` — `{"safe": bool, "reasons": [...]}`; flags bidi controls (U+202A–202E, U+2066–2069, U+200E/200F), zero-width chars, and TR39 confusable skeletons that collide with a benign appearance. Display-only; never mutates bytes. (This is the Python reference the Swift guard mirrors.)

- [ ] **Step 1: Failing test** — a plain body is `safe`; a body with a RTL override, or a zero-width joiner injection, or a Latin/Cyrillic homoglyph mix is flagged `safe=False` with the reason. Assert `scan` never alters the input.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** `display_guard.py`. **Step 4: PASS.** **Step 5: Commit** `feat: confusables/bidi/zero-width display guard (INV-DISPLAY)`.

---

### Task 4: Device enrolment + pinning (CLI-only)

**Files:** Create `src/whatsvault/approval/devices.py`; Test `tests/test_devices.py`.

**Interfaces:**
- `devices.enroll(control_conn, name, public_key_sec1) -> str` — pins a device ACTIVE (returns `dev_` id); the CLI wraps this behind a QR + mutual-challenge flow. No MCP path.
- `devices.revoke(control_conn, device_id) -> None` — sets REVOKED; historical approvals stay valid evidence (I4a), but an unsent approval from a now-REVOKED device is unexecutable (I4b).
- `devices.active_key(control_conn, device_id) -> bytes | None`.

- [ ] **Step 1: Failing test** — enroll pins a key; a second key can be enrolled (multiple ACTIVE); revoke flips state; `active_key` returns None for a revoked device; an approval referencing a REVOKED device is rejected by the sender (Task 5) while an already-consumed one remains in the audit trail.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** `devices.py`. **Step 4: PASS.** **Step 5: Commit** `feat: CLI-only device enrolment/revocation with I4a/I4b semantics`.

---

### Task 5: Sender — permission-to-transmit transaction + state machine (fake Meta)

**Files:** Create `src/whatsvault/approval/sender.py`, `src/whatsvault/providers/base.py`, `src/whatsvault/providers/fake_meta.py`; Test `tests/test_sender.py`.

**Interfaces:**
- `providers.base.WhatsAppProvider` (Protocol): `send_text/send_media/send_template/mark_read/materialise_media/health`; `FakeMeta` simulates 2xx+wamid, 4xx, 5xx, timeout-after-send, and connect-failure-before-send.
- `sender.execute_write(vault_conn, control_conn, provider, signed_envelope, now_ms, clock_ok) -> dict` — runs the full `BEGIN IMMEDIATE` predicate (Task 2 verify + policy re-eval + nonce consume + attempt open) then the send; maps outcomes to `SUBMITTED`/`FAILED`/`INDETERMINATE` per the §6.6 matrix; disables HTTP retries; refuses on `clock_ok=False`.

- [ ] **Step 1: Failing test** — (a) a valid APPROVE envelope inside an open window sends → `SUBMITTED`, nonce consumed; (b) replaying the same envelope → denied (`APPROVAL_ALREADY_CONSUMED`), no second send; (c) a `decision=REJECT` envelope never sends; (d) a body swapped after signing → `PAYLOAD_CHANGED`; (e) window closed between prepare and send → `WINDOW_CLOSED` despite valid signature; (f) a REVOKED device → denied; (g) timeout-after-send → `INDETERMINATE`, nonce stays consumed, not auto-retried; (h) `clock_ok=False` → refused.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** `sender.py` + `fake_meta.py`. **Step 4: PASS.** **Step 5: Commit** `feat: sender permission-to-transmit transaction with full §6.6 failure matrix`.
- **Phase-0 gate:** the real `MetaCloudProvider` (Task 9) and `biz_opaque_callback_data` reconciliation are activated only after V8/V12.

---

### Task 6: Draft preparation + MCP prepare tools (local-only)

**Files:** Create `src/whatsvault/approval/drafts.py`; Modify `apps/mcp/server.py` (add `prepare_message`, `prepare_template_message`, `cancel_draft`, `get_draft_status` — all `openWorldHint:false`); Test `tests/test_drafts.py`, extend `tests/test_mcp_surface.py`.

**Interfaces:**
- `drafts.prepare(control_conn, vault_conn, *, conversation_id, text, reply_to=None, now_ms) -> dict` — resolves recipient (bound, never re-resolved), runs P1–P7 at prepare, mints a 32-byte nonce, sets expiry, returns the draft summary (no send). Idempotent: identical pending prep returns the existing draft (dedupe by body hash).
- The four MCP tools stay local; `get_draft_status` returns the state enum (never `APPROVED`).

- [ ] **Step 1: Failing test** — prepare into an open window yields a `PENDING_APPROVAL` draft with a nonce and P7 fields bound; prepare into a closed window without a template is rejected; identical repeated prepare returns the same draft id; the MCP surface still passes the negative-surface assertion (no `send_prepared_message`, four new local tools all `openWorldHint:false`).
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** `drafts.py` + wire tools. **Step 4: PASS.** **Step 5: Commit** `feat: local draft preparation + prepare MCP tools (no dispatch verb)`.

---

### Task 7: Approval relay + device-sealed detail + dispatcher

**Files:** Create `apps/approval-relay/server.py`, `src/whatsvault/approval/relay.py`, `apps/dispatcher/dispatch.py`; Test `tests/test_relay_dispatch.py`.

**Interfaces:**
- `relay.sealed_draft_detail(control_conn, draft_id, device_pub) -> bytes` — the draft detail (recipient, body) **sealed to the enrolled device's public key** (INV-DEVICE-SEAL), so the Tunnel carries ciphertext.
- `relay.accept_envelope(control_conn, envelope_bytes) -> None` — stores the exact received bytes idempotently (`UNIQUE(approval_id)` + `UNIQUE(draft_id, device_id, decision, nonce)`); never re-encodes; never writes authoritative APPROVED state.
- `dispatch.on_envelope(...)` — wakes `sender.execute_write`; the model is not in this path.

- [ ] **Step 1: Failing test** — draft detail sealed to a device pubkey is ciphertext (sentinel absent from the bytes) and decrypts on the device side; the same envelope POSTed twice persists once and dispatches once; a valid stored envelope drives a send without any model/tool call.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** relay + dispatcher. **Step 4: PASS.** **Step 5: Commit** `feat: device-sealed approval relay and envelope-triggered dispatcher`.
- **Phase-0 gate:** Cloudflare Access-at-origin JWT validation is activated with the live Tunnel; local tests use a fake auth context.

---

### Task 8: iOS approval app — STRUCTURED, Apple-Developer-gated

**Files:** `ios/WhatsVaultApproval/*` (Swift).

**Deliverables (need a paid Apple Developer account; APNs optional, ntfy/Pushover fallback):**
- Secure Enclave P-256 key with access control `[.privateKeyUsage, .biometryCurrentSet]`, fresh `LAContext` per signature, reuse duration 0, no passcode fallback (E2).
- Canonical encoder that reproduces `tests/golden/vectors.json` **byte-for-byte** (gate: a CI check runs Swift-encode over the vectors).
- Fetches device-sealed draft detail, decrypts locally, renders body through the confusables/bidi guard (Task 3 logic), shows recipient masked from the signed `recipient_wa_id`, offers **Approve & Send** only when the guard is clear (else the two-step raw view).
- QR enrolment; content-free push receipt.

- [ ] **Tasks:** (8.1) Secure Enclave keygen + biometric signing; (8.2) canonical encoder + Maestro/XCTest vector-parity test against `vectors.json`; (8.3) `Swift sign → Python verify` and `Python fixture-sign → Swift verify` cross tests; (8.4) UI + display guard + masked recipient; (8.5) enrolment + push.
- **Gate:** cannot ship without the golden-vector parity green in both directions.

---

### Task 9: MetaCloudProvider (real) — STRUCTURED, Phase-0-gated

**Files:** `src/whatsvault/providers/meta_cloud.py`.

- Implements `WhatsAppProvider` against `graph.facebook.com/{PHONE_NUMBER_ID}/messages`; holds the `whatsapp_business_messaging` token only (no management scope); attaches `biz_opaque_callback_data`; `materialise_media` re-fetches media URLs (V9); HTTP auto-retries disabled.
- **Gate:** activated only after Phase 0 V8/V12/V13; until then `FakeMeta` (Task 5) is the provider.

---

### Task 10: Full-suite gate + adversarial pass + changelog

- [ ] `.venv/bin/pytest -q` (Python core green). Adversarial gate: forged/expired/modified/replayed approvals denied with exact reason codes; `decision=REJECT` cannot send; wrong-device key rejected; double-send race; clock-jump refusal; sealed-detail-over-Tunnel is ciphertext (Tasks 2,5,7). Commit `test: close out Phase 4 approval-chain core (iOS + live Meta gated)`.

## Self-Review

- Spec §6 coverage: canonical encoding + golden vectors → Task 1; verify → Task 2; display guard → Task 3; enrolment/revocation → Task 4; sender transaction + §6.6 matrix → Task 5; local drafts + prepare tools (no dispatch) → Task 6; device-sealed relay + dispatcher → Task 7; iOS Secure Enclave + parity → Task 8; real Meta → Task 9.
- Falsifiable core: the sender denies forge/replay/payload-change/wrong-device/window-closed with named reason codes (Task 5) — a hostile reviewer attacks here.
- **Honest calibration:** Tasks 1–7, 10 are fully TDD now (fixture keypair, FakeMeta, fake auth). Tasks 8 (iOS/Secure Enclave, needs Apple Developer) and 9 (live Meta, needs Phase 0 V8/V12) are structured and gated. The **golden vectors (Task 1) are the contract** that lets the Python core be built and verified before the Swift app exists — the cross-language parity is a hard gate, not an afterthought.
