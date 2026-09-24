# WhatsVault Phase 4 — Approval Chain LOCAL CORE (STANDALONE EXECUTION PLAN)

> **Generated just-in-time** from spec §1/§6, the master roadmap, the **Corrections Ledger #5–#17 + #43 (INV-HARDWARE/INV-SENDPOLICY/INV-DISPLAY)**, and the **actual repo state** (control schema + P-256 crypto verified 2026-08-27, after Phase 3 core). Supersedes the roadmap's Phase 4 reference draft.

> **Scope:** the local core (canonical encoding, verify, policy engine, two-key device identity, display guard, device seal, relay, sender + ClockGuard + crash recovery, drafts) is fully TDD now against fixtures + FakeMeta. The **iOS Secure-Enclave app, live Meta daemon (#43), and live sends** are Phase-0/Apple-gated (structured, not executed).

> **For agentic workers:** superpowers:executing-plans. TDD iron law.

**Goal:** The load-bearing security boundary — no WhatsApp write without a fresh, hardware-backed, biometric-authorised P-256 signature over the exact immutable draft, verified server-side under current policy, with the model holding no dispatch verb.

## Bound to actual repo state (verified this session)
- **P-256 ECDSA verified:** sign payload bytes directly (SHA256 internal, CryptoKit-compatible), raw `r‖s` (64B) ↔ DER via `encode/decode_dss_signature`, SEC1-uncompressed (65B) public key, tamper→`InvalidSignature`, ECDSA is randomized (so #16: never assert two sigs differ).
- **P-256 ECDH device-seal verified:** ephemeral P-256 → ECDH with the device agreement key → HKDF-SHA256(info=`WHATSVAULT-DEVICE-SEAL-V1`) → AES-256-GCM round-trips (#5 agreement path).
- control `0001`: `approval_devices` has ONE `public_key` (needs the #5 agreement key); `drafts` has `body_sha256/template_params_sha256/attachments_digest/reply_to_wamid/nonce/expires_at_ms` but **no `target_message_wamid`** (#10); `approvals UNIQUE(draft_id,device_id,decision,nonce)` (the #14 slot); `send_attempts` state includes `SUBMITTING` (#13); `approval_nonces` PK; signatures BLOB len 64.
- control lane at version 1 → **Phase 4 = control migration 0002** (Phase 5 templates shifts to 0003; master migration-numbering note updated).
- `whatsvault.crypto.sealed` (X25519 edge seal) exists; device seal is a new P-256 module.

## Ledger corrections folded in (binding)
- **#5** device identity = TWO SE P-256 keys: `signing_public_key` (ECDSA approvals) + `agreement_public_key` (ECDH device seal), both pinned to one device. Migration adds `agreement_public_key`/`agreement_key_algorithm`.
- **#6** enrolment = QR + mutual challenge: device signs `DOMAIN‖pairing_id‖challenge‖signing_pub‖agreement_pub`; the pin verifies that signature binding both keys. No MCP path.
- **#10** canonical adds a distinct `target_message_wamid` (never overload `reply_to_wamid`); migration adds `drafts.target_message_wamid`.
- **#11** ONE shared `approval/policy.py` (P1–P7); `drafts.prepare` and `sender.execute_write` both call it; send re-evaluates and is authoritative.
- **#12** sender owns clock trust via an internal `ClockGuard` (no `clock_ok` caller argument).
- **#13** startup crash recovery: `SUBMITTING` send_attempts → `INDETERMINATE`; unprocessed valid approval envelopes rescanned.
- **#14** relay does cheap structural verification (known device? signature parses? draft exists? signed draft-id matches?) before occupying the uniqueness slot — **defence-in-depth**, not a claimed-closed hole (the nonce is device-sealed/secret); sender re-verifies everything.
- **#15** display guard does NOT blanket-flag ZWNJ: `U+200C` allowed contextually in Persian; bidi overrides high-risk; other invisibles warn.
- **#16** never assert two ECDSA sigs differ; assert replay identity = `device_id + nonce`.

## Global constraints (§6)
- **INV-SIGNATURE/INV-HARDWARE**: DB state is never proof; every send needs a valid, unexpired, single-use P-256 signature over the exact recipient + immutable payload from a key inaccessible to the Mac.
- Permission-to-transmit transaction (`BEGIN IMMEDIATE`): verify ECDSA over freshly recomputed payload → decision==APPROVE → device ACTIVE → body_sha256 match → recipient match → P7 → not expired → **re-evaluate P1–P7** → consume nonce (UNIQUE) → open send_attempt → COMMIT → then POST. HTTP retries disabled.
- No `send_prepared_message` tool. Approval triggers dispatch.

---

### Task 1: Canonical encoding + golden vectors (#10)
**Files:** Create `src/whatsvault/approval/__init__.py`, `src/whatsvault/approval/canonical.py`, `tests/golden/decision_vectors.json`; Test `tests/test_canonical.py`.
**Interfaces:** `canonical.encode(fields) -> bytes` — `"WHATSVAULT-DRAFT-DECISION-V1\n"` + `version(u16be)` then per-field `u32be(len)||bytes` in fixed order incl. **`target_message_wamid`** distinct from `reply_to_wamid`; absent optionals = zero-length slot (never omitted). `canonical.attachments_digest(items) -> bytes`.
- [ ] Failing test: fixed field set → fixed bytes (checked-in vector); absent optional = zero-length slot; two drafts differing only in `decision` differ; `target_message_wamid` and `reply_to_wamid` are independent slots (mutating one changes bytes, not the other). → commit `feat(4): WHATSVAULT-DRAFT-DECISION-V1 canonical encoding + target_message_wamid + vectors (#10)`.

### Task 2: P-256 verification + test signer (#16)
**Files:** Create `src/whatsvault/approval/verify.py`; Test `tests/test_verify.py`.
**Interfaces:** `verify.verify(payload, signature_rs, public_key_sec1) -> bool` (raw `r‖s`→DER→`ec.ECDSA(SHA256)`); `verify.sign_for_test(payload, private_key) -> bytes` (software P-256, tests only).
- [ ] Failing test: sign→verify roundtrip; payload/recipient/expiry/decision mutations all fail; different keypair fails; two sigs over one payload both verify (NO assertion they differ); replay identity is `device_id+nonce` (a resubmit is rejected by that, not by signature bytes). → commit `feat(4): P-256 verification with raw r||s; replay-identity framing (#16)`.

### Task 3: Shared P1–P7 policy engine (#11)
**Files:** Create `src/whatsvault/approval/policy.py`; Test `tests/test_policy.py`.
**Interfaces:** `policy.evaluate(ctx: dict, *, phase: str) -> PolicyResult(ok, failed:list[str])` over P1 recipient_bound, P2 window_or_template (free-form text needs an open window), P3 account_binding, P4 not_expired, P5 device_active, P6 rate_ok, P7 recipient_policy (no group). Both prepare and send call it; send is authoritative.
- [ ] Failing test: a ctx passing at prepare, then window closes → send `evaluate(phase='send')` fails P2; an expired ctx fails P4; a revoked device fails P5; a group recipient fails P7; the same module is the single source (import identity). → commit `feat(4): shared P1-P7 approval policy engine (#11)`.

### Task 4: Two-key device identity + enrolment challenge (#5, #6) — control migration 0002
**Files:** Create `src/whatsvault/db/migrations/control/0002_device_agreement.sql`; Modify `migrations/__init__.py`; Create `src/whatsvault/approval/devices.py`; Test `tests/test_devices.py`.
**Migration:** `ALTER TABLE approval_devices ADD COLUMN agreement_public_key BLOB`; `ADD COLUMN agreement_key_algorithm TEXT`; `ALTER TABLE drafts ADD COLUMN target_message_wamid TEXT`.
**Interfaces:** `devices.verify_enrolment(*, pairing_id, challenge, signing_pub, agreement_pub, signature) -> bool` (device signs `DOMAIN‖pairing_id‖challenge‖signing_pub‖agreement_pub`); `devices.enroll(control_conn, name, *, signing_pub, agreement_pub) -> str` (pins BOTH, ACTIVE; CLI-only); `devices.revoke`; `devices.active_signing_key`/`active_agreement_key`.
- [ ] Failing test: a valid enrolment signature binding both keys verifies, a substituted agreement key fails (#6 MITM); enroll pins both keys; `active_agreement_key` returns None for a revoked device; a wrong-challenge signature fails. → commit `feat(4): two-key device identity + enrolment challenge (#5,#6)`.

### Task 5: Display guard — Persian-aware (#15)
**Files:** Create `src/whatsvault/approval/display_guard.py`; Test `tests/test_display_guard.py`.
**Interfaces:** `display_guard.scan(text) -> {"safe":bool,"reasons":[...]}` — flags bidi overrides/isolates (high), ZWSP/word-joiner/other invisibles (warn), Latin/Cyrillic confusable mixes; **does NOT flag `U+200C` ZWNJ inside Persian-script runs**. Never mutates.
- [ ] Failing test: plain body safe; a legit Persian body with ZWNJ (می‌روم) is safe; an RTL-override injection flagged; a Latin/Cyrillic homoglyph mix flagged; input unchanged. → commit `feat(4): Persian-aware confusables/bidi display guard (#15)`.

### Task 6: Device seal (P-256 ECDH) + relay structural pre-check (#14)
**Files:** Create `src/whatsvault/crypto/device_seal.py`, `src/whatsvault/approval/relay.py`; Test `tests/test_device_seal.py`, `tests/test_relay.py`.
**Interfaces:** `device_seal.seal(agreement_pub_sec1, plaintext, aad=b"") -> bytes` / `open_sealed(agreement_priv, envelope, aad=b"") -> bytes` (ephemeral P-256 → ECDH → HKDF → AES-256-GCM); `relay.sealed_draft_detail(control_conn, draft_id, device_id) -> bytes` (sealed to the device agreement key — ciphertext over the Tunnel, INV-DEVICE-SEAL); `relay.accept_envelope(control_conn, envelope_bytes, *, structural_check) -> str` (structural pre-check #14 before the UNIQUE slot; idempotent; never writes APPROVED state).
- [ ] Failing test: device-seal roundtrip + sentinel absent from ciphertext; sealed draft detail decrypts on the device side; a structurally-invalid envelope is refused before occupying the slot; the same envelope twice persists once. → commit `feat(4): P-256 device seal + relay structural pre-check (#14, INV-DEVICE-SEAL)`.

### Task 7: Sender — ClockGuard + permission-to-transmit + §6.6 matrix + crash recovery (#12,#13)
**Files:** Create `src/whatsvault/approval/clockguard.py`, `src/whatsvault/approval/sender.py`, `src/whatsvault/providers/base.py`, `src/whatsvault/providers/fake_meta.py`; Test `tests/test_sender.py`.
**Interfaces:** `clockguard.ClockGuard(now_fn, monotonic_fn).can_authorise(now) -> bool` (refuses on backward wall jump / stale NTP); `providers.base.WhatsAppProvider` Protocol + `FakeMeta` (2xx+wamid / 4xx / 5xx / timeout-after-send / connect-fail); `sender.execute_write(vault_conn, control_conn, provider, signed_envelope, clock_guard) -> dict` (full BEGIN IMMEDIATE predicate incl. verify + re-eval P1–P7 + nonce consume + attempt open, then send; maps §6.6 outcomes; **no `clock_ok` argument**); `sender.recover_startup(control_conn, now_ms) -> dict` (SUBMITTING→INDETERMINATE, #13).
- [ ] Failing test: valid APPROVE in open window → SUBMITTED, nonce consumed; replay same envelope → `APPROVAL_ALREADY_CONSUMED`; `decision=REJECT` never sends; body swapped after signing → `PAYLOAD_CHANGED`; window closed post-prepare → `WINDOW_CLOSED`; REVOKED device → denied; timeout-after-send → `INDETERMINATE`, not auto-retried; a backward clock jump → refused (ClockGuard, no caller override); `recover_startup` moves a stranded SUBMITTING to INDETERMINATE. → commit `feat(4): sender permission-to-transmit + ClockGuard + §6.6 matrix + crash recovery (#12,#13)`.

### Task 8: Draft preparation (P1–P7 at prepare) (#11) + suite gate
**Files:** Create `src/whatsvault/approval/drafts.py`; Test `tests/test_drafts.py`; CHANGELOG.
**Interfaces:** `drafts.prepare(control_conn, *, conversation_id, account_id, phone_number_id, recipient_wa_id, text, kind='text', now_ms, window_open) -> dict` (runs the shared policy at prepare; mints a 32-byte nonce; sets expiry; state `PENDING_APPROVAL`; idempotent by body hash). No dispatch verb.
- [ ] Failing test: prepare into an open window → PENDING_APPROVAL with nonce + P7 fields bound; prepare of free-form into a closed window (no template) → refused (P2); identical repeat prepare → same draft id. Then `.venv/bin/pytest -q` + `pip check` green; CHANGELOG. → commit `test: close out Phase 4 local core (iOS + live Meta + daemon Phase-0/Apple-gated)`.

### Task 9 (structured, gated): iOS Secure-Enclave app + `whatsvault-meta` daemon (#43)
- Recorded contract only (not built): Secure Enclave two-key generation (`.privateKeyUsage,.biometryCurrentSet`), canonical encoder reproducing `decision_vectors.json` byte-for-byte, `Swift-sign→Python-verify` + `Python-sign→Swift-verify` gate; `apps/meta/daemon.py` sole token holder over a Unix-domain socket exposing `execute_approved_write`/`materialise_media`/`health` (#43). A `docs`/README note records it.

## Self-review (consistency + ambiguity gate — required before execution)

- **SR-1 (Task 4):** control migration 0002 DROPs+recreates `trg_draft_freeze` including `target_message_wamid` (signed field; SQLite has no ALTER TRIGGER).
- **SR-2 (Task 3):** P1–P7 is a pragmatic, stable-reason-code instantiation of §5.6; the binding ledger property (#11) is the single shared module imported by both prepare and send.
- **SR-3 (Task 6/7):** relay structural check = device known+ACTIVE / signature 64B / draft exists / decision valid; full ECDSA verification + policy re-eval stay in the sender (#14 defence-in-depth; nonce is device-sealed/secret).
- **SR-4 (Task 7):** the sender recomputes canonical bytes from the draft and verifies against the device signing key; `approval_nonces` UNIQUE is the replay gate (#16), never signature bytes.
- **Consistency checks passed:** ALTER TABLE ADD COLUMN needs no default here (nullable); send_attempts is mutable (state machine); device seal (P-256) is a separate module from the X25519 edge seal; both crypto paths empirically verified.

**Gate: PASS** — local core is execution-safe. Begin Task 1.
