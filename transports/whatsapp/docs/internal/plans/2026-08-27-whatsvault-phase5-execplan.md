# WhatsVault Phase 5 — Scheduler/Capabilities/Templates/Status LOCAL CORE (STANDALONE PLAN)

> **JIT** from spec §5.6/§5.7/§6.6/§11, the roadmap, **Corrections Ledger #7–#9, #17, #45–#48, #59–#60**, and the **actual repo state** (schemas + apscheduler 3.11.3 verified 2026-08-27, after Phase 4 core). Supersedes the roadmap draft. Live template sync + APNs are Phase-0/Apple-gated.

> superpowers:executing-plans. TDD iron law.

**Goal:** Add unattended draft preparation (persistent, prepare-only), the phone-signed `mark_read` capability, template send support, and delivery/status reconciliation — every send still behind the Phase-4 hardware gate; no authority to the model or scheduler.

## Bound to actual repo state
- `capability_grants` (control 0001) already has `device_id/action/conversation_id/max_actions/used_count/nonce(32)/signature(64)/status` — #7/#8 need only encode + phone-signature verify + consume (NO Mac minting).
- `send_attempts.biz_opaque_callback_data` exists; `message_status_events` has **no** callback column → vault migration 0006 adds it (#60, PHASE-0-CONTINGENT).
- control at v2 → **Phase 5 control = 0003** (templates + scheduled_jobs + job_runs + reconciliation_candidates); vault at v5 → **0006** (status callback).
- `apscheduler==3.11.3` pinned (#46). `approval.devices.active_signing_key`, `approval.verify.verify`, `approval.canonical`, `approval.sender` exist.

## Ledger corrections folded in
- **#7** capability is **phone-signed** (Face ID → SE signing key); the Mac verifies + stores, never mints. Remove any Mac `mint_grant`.
- **#8** `WHATSVAULT-CAPABILITY-V1` cross-language golden vectors.
- **#9** `mark_read` binds the target BEFORE consuming a grant: wamid exists, belongs to `conversation_id`, `direction=='in'`, account matches.
- **#17** `WHATSVAULT-TEMPLATE-PARAMS-V1` explicit canonical encoding + vectors; bind template name+language+definition-version+params digest.
- **#45** persistent `scheduled_jobs` + `job_runs`; restart never drops schedules.
- **#46** APScheduler pinned. **#47** V1 policy: static/template drafts only, **no autonomous LLM generation**.
- **#59** `POSSIBLE_MATCH` persisted in `reconciliation_candidates` with a human resolve/dismiss workflow; never auto-attributed.
- **#60** callback column on the normalised status schema; contingent on Phase 0 V8, manual-only fallback if absent.

## Global constraints
- Scheduler PREPARES, never approves; re-validates preconditions before surfacing a stale draft. No autonomous messaging bot (§11).
- `mark_read` capability is use-only from MCP; the MCP can never create/extend/renew/re-scope a grant.
- Only APPROVED templates send; the catalogue is CLI-synced with a management credential kept OUT of the runtime.

---

### Task 1: Phone-signed capability protocol + golden vectors (#7, #8)
**Files:** Create `src/whatsvault/approval/capabilities.py`, `tests/golden/capability_vectors.json`; Test `tests/test_capabilities_grant.py`.
**Interfaces:** `capabilities.encode_grant(fields) -> bytes` (`WHATSVAULT-CAPABILITY-V1\n` + fixed-order length-prefixed fields); `capabilities.store_grant(control_conn, fields, signature, *, device_signing_key) -> str` (verify phone signature over the canonical bytes, then INSERT ACTIVE; raises `GrantRejected` on bad signature — NO minting); `capabilities.verify_and_consume(control_conn, action, conversation_id, now_ms) -> bool` (find ACTIVE grant matching action+conversation, signature valid vs pinned device signing key, not expired, `used_count < max_actions`; increment atomically).
- [ ] Failing test: a valid MARK_READ grant permits N consumes then refuses the (N+1)th; expired refuses; a grant for conversation A does not authorise B; a grant signed by a REVOKED device refuses; `store_grant` rejects a bad signature; there is NO code path minting a grant on the Mac; a golden vector encodes byte-for-byte + one-byte scope/action/expiry mutation each rejects. → commit `feat(5): phone-signed capability protocol + golden vectors (#7,#8)`.

### Task 2: `mark_read` target binding (#9)
**Files:** Modify `src/whatsvault/approval/sender.py`; Test `tests/test_mark_read.py`.
**Interfaces:** `sender.mark_read(vault_conn, control_conn, provider, *, conversation_id, wamid, account_id, now_ms) -> dict` — binds the target (wamid exists, belongs to conversation, `direction=='in'`, account matches) THEN consumes a capability (Task 1); else `AUTHORIZATION_MISSING`. Consumption + provider call are one path.
- [ ] Failing test: a grant for conversation A + a wamid from B → rejected, no consumption; an outbound wamid → rejected; a valid inbound target with a grant → provider called + grant decremented; no grant → `AUTHORIZATION_MISSING`; a typing-indicator action is not authorised by a MARK_READ grant. → commit `feat(5): mark_read target binding + capability consumption (#9)`.

### Task 3: Phase-5 schema (control 0003) + template canonicalisation (#17)
**Files:** Create `src/whatsvault/db/migrations/control/0003_phase5.sql`; Modify `migrations/__init__.py`; Create `src/whatsvault/templates.py`, `tests/golden/template_params_vectors.json`; Test `tests/test_templates.py`.
**Migration:** `templates(template_id, meta_template_id, name, language, category, status, definition_version, schema, synced_at)`; `scheduled_jobs(...)`; `job_runs(...)`; `reconciliation_candidates(...)`.
**Interfaces:** `templates.params_digest(template_name, language, definition_version, params) -> bytes` (`WHATSVAULT-TEMPLATE-PARAMS-V1` canonical + binds name/language/definition-version); `templates.upsert_from_sync(control_conn, rows)` (CLI, management authority passed in); `templates.prepare_template(control_conn, *, conversation_id, template_id, params, now_ms) -> dict` (APPROVED only; params match schema; binds `template_params_sha256`).
- [ ] Failing test: a non-APPROVED template refuses; param mismatch refuses; APPROVED + valid params prepares a draft with a non-empty `template_params_sha256`; identical params under different `definition_version` produce different digests (#17); a golden `WHATSVAULT-TEMPLATE-PARAMS-V1` vector reproduces byte-for-byte. → commit `feat(5): Phase-5 schema + WHATSVAULT-TEMPLATE-PARAMS-V1 canonicalisation (#17)`.

### Task 4: Persistent prepare-only scheduler (#45, #46, #47)
**Files:** Create `apps/scheduler/scheduler.py`; Test `tests/test_scheduler.py`.
**Interfaces:** `scheduler.persist_job(control_conn, job) -> str` / `scheduler.load_jobs(control_conn) -> list` (survives restart, #45); `scheduler.fire(control_conn, job_id, *, precondition_fn, prepare_fn, now_ms) -> dict` — records a `job_runs` row; runs `precondition_fn` (window open? not already answered? still relevant?) and only then `prepare_fn` (one PENDING_APPROVAL draft); never approves/sends; a stale precondition → no draft; `generation_mode` is `static`/`template` only in V1 (`AI` rejected, #47).
- [ ] Failing test: a persisted job reloads after a fresh `load_jobs` (#45); a fire with a stale precondition produces no draft but records a job_run; a fire with preconditions holding produces exactly one draft; a job with `generation_mode='ai'` is rejected in V1 (#47). → commit `feat(5): persistent prepare-only scheduler with precondition re-validation (#45,#47)`.

### Task 5: Status reconciliation + POSSIBLE_MATCH persistence (#59, #60) + vault 0006
**Files:** Create `src/whatsvault/db/migrations/vault/0006_status_callback.sql`; Modify `migrations/__init__.py`; Create `src/whatsvault/approval/reconcile.py`; Test `tests/test_reconcile.py`.
**Migration:** `ALTER TABLE message_status_events ADD COLUMN biz_opaque_callback_data TEXT` (#60).
**Interfaces:** `reconcile.on_status_event(vault_conn, control_conn, status_event, *, now_ms) -> dict` — exact `biz_opaque_callback_data`=`wv1:<atm>` or known `wamid` → deterministically resolve the send_attempt (INDETERMINATE→SUBMITTED); else record a `reconciliation_candidates` row (POSSIBLE_MATCH), never auto-resolving; `reconcile.resolve(control_conn, candidate_id, *, decision)` (human resolve/dismiss).
- [ ] Failing test: a status event with a matching callback resolves an INDETERMINATE attempt to SUBMITTED; recipient+time only → a durable POSSIBLE_MATCH candidate, attempt unchanged; two same-minute sends → not auto-attributed; `resolve` transitions a candidate. → commit `feat(5): status reconciliation + POSSIBLE_MATCH persistence + callback column (#59,#60)`.

### Task 6: Full-suite gate + CHANGELOG + push note (#48)
**Files:** `apps/push/README.md` (structured, gated); CHANGELOG.
- [ ] `.venv/bin/pytest -q` + `pip check` green; a structured content-free push + rate-limit contract recorded (#48, APNs/ntfy Phase-0/Apple-gated); commit `test: close out Phase 5 core (live sync + APNs Phase-0/Apple-gated)`.

## Self-review (consistency + ambiguity gate — required before execution)

- **SR-1:** store_grant looks up the device signing key internally; verify_and_consume re-verifies against the current ACTIVE device key (revoked device ⇒ grant fails).
- **SR-2:** FakeMeta gains mark_read; provider Protocol includes it.
- **SR-3:** scheduler tested surface is persist/load/fire (pure); live APScheduler loop is a thin gated wrapper (pinned, not run in CI).
- **SR-4:** control 0003 creates all Phase-5 control tables; vault 0006 adds the callback column (set at insert, not frozen).

**Gate: PASS** — local core execution-safe. Begin Task 1.
