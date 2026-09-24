# WhatsVault Phase 5 — Scheduler, Templates, Capabilities & Status UX Implementation Plan

> **For agentic workers:** superpowers:subagent-driven-development / executing-plans. `- [ ]` steps.
> **Dependency:** Phase 4 (approval chain). Scheduler/capability/status logic is TDD-testable now against fakes; template *sync* against the live WABA is Phase-0-gated (V10/V12).

**Goal:** Add unattended draft preparation (scheduler), the signed `mark_read` capability, template send support, and delivery/status reconciliation UX — every one still gated behind the same hardware approval boundary, none granting authority to the model or scheduler.

**Architecture:** A local APScheduler prepares drafts on a schedule (never approves). A signed `WHATSVAULT-CAPABILITY-V1` grant lets `mark_read` proceed without per-message Face ID, but the grant is minted only on-device/CLI. Template sends reuse the Phase 4 approval path. Status reconciliation consumes `MESSAGE_STATUS` events (Phase 3) and correlates via `biz_opaque_callback_data`.

**Tech Stack:** Python (APScheduler), builds on 1a+3+4.

**Spec:** §5.6 (P1–P7), §5.7 (mark_read capability, N1), §6.6 (reconciliation), §11 (privacy — no autonomous bot).

## Global Constraints

- **The scheduler prepares, never approves.** It inherits the full approval gate; `coalesce=true`, `misfire_grace_time` set, and it **re-validates preconditions** (window still open? conversation already replied? still morning?) before a stale draft is surfaced.
- **No autonomous messaging bot** (§11): no auto-send based on incoming content; the scheduler only *prepares* — a human still signs each send.
- **`mark_read` capability (§5.7):** a signed, domain-separated `WHATSVAULT-CAPABILITY-V1` grant (`capability_id`, `device_id`, `account_id`, `conversation_id`, `action=MARK_READ`, `created_at_ms`, `expires_at_ms`, `max_actions`, `nonce`), default finite duration. The MCP may *use* a grant; it can never create/extend/renew/re-scope one. Typing indicators are a **separate** action. N1 side-channel (read timing reflects automation) surfaced at grant time.
- **Templates:** only APPROVED templates send; the local catalogue is synced via a management credential kept **out of the runtime** (`whatsvault templates sync`, CLI). `list_templates` reads the local catalogue only.
- Reconciliation: exact `biz_opaque_callback_data` or known `wamid` → automatic; recipient+time+conversation → `POSSIBLE_MATCH` only, human-resolved.

---

### Task 1: Signed capability grants + verification

**Files:** Create `src/whatsvault/approval/capabilities.py`; Test `tests/test_capabilities_grant.py`.

**Interfaces:**
- `capabilities.encode_grant(fields) -> bytes` — `"WHATSVAULT-CAPABILITY-V1\n"` + fixed-order length-prefixed fields.
- `capabilities.verify_and_consume(control_conn, action, conversation_id, now_ms) -> bool` — finds an ACTIVE grant matching action+conversation, signature valid against a pinned device key, not expired, `used_count < max_actions`; increments `used_count` atomically. No creation path here.
- Grant creation lives only in `devices.mint_grant(...)` invoked by the CLI/on-device flow.

- [ ] **Step 1: Failing test** — a valid MARK_READ grant permits N `verify_and_consume` calls then refuses the (N+1)th (`max_actions`); an expired grant refuses; a grant for conversation A does not authorise action in B; a grant signed by a REVOKED device refuses; there is no code path by which the MCP layer can mint a grant.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** `capabilities.py`. **Step 4: PASS.** **Step 5: Commit** `feat: signed mark_read capability grants (use-only from MCP)`.

---

### Task 2: `mark_read` action via sender

**Files:** Modify `src/whatsvault/approval/sender.py`; Test extend `tests/test_sender.py`.

**Interfaces:**
- `sender.mark_read(vault_conn, control_conn, provider, *, conversation_id, wamid, now_ms) -> dict` — proceeds if a valid capability (Task 1) consumes, else requires an individual signed approval; refuses on missing authority.

- [ ] **Step 1: Failing test** — with a valid capability, `mark_read` calls the provider and decrements the grant budget; without one and without an approval, it refuses (`AUTHORIZATION_MISSING`); a typing-indicator action is **not** authorised by a MARK_READ grant.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write.** **Step 4: PASS.** **Step 5: Commit** `feat: mark_read via capability or individual approval`.

---

### Task 3: Scheduler (prepare-only, re-validating)

**Files:** Create `apps/scheduler/scheduler.py`; Modify `pyproject.toml` (APScheduler dep); Test `tests/test_scheduler.py`.

**Interfaces:**
- `scheduler.build_job(prepare_fn, precondition_fn)` — a job that, when it fires (possibly late after a misfire), first runs `precondition_fn` (window open? not already answered? still relevant?) and only then calls `prepare_fn` to create a `PENDING_APPROVAL` draft. Never approves, never sends.
- Configured `coalesce=True`, `misfire_grace_time`, and a re-validation hook.

- [ ] **Step 1: Failing test** — a job firing when the precondition is stale (window closed, or the conversation already got a reply) produces **no** draft; a job firing when preconditions hold produces exactly one `PENDING_APPROVAL` draft; a coalesced misfire produces at most one draft, not a backlog.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** `scheduler.py`. **Step 4: PASS.** **Step 5: Commit** `feat: prepare-only scheduler with precondition re-validation`.

---

### Task 4: Template catalogue + send

**Files:** Create `src/whatsvault/templates.py`, `src/whatsvault/db/migrations/control/0002_templates.sql`; Test `tests/test_templates.py`.

**Interfaces:**
- Migration adds `templates(template_id, meta_template_id, name, language, category, status, schema, synced_at)`.
- `templates.upsert_from_sync(control_conn, rows)` — CLI-driven sync using management authority (passed in, not held by runtime).
- `templates.prepare_template(control_conn, *, conversation_id, template_id, params) -> dict` — validates the template is APPROVED and params match the schema; builds a draft bound with `template_params_sha256` (Phase 4 canonical).

- [ ] **Step 1: Failing test** — a non-APPROVED template refuses; a param set mismatching the schema refuses; an APPROVED template with valid params prepares a draft with a non-empty `template_params_sha256`; `list_templates` (MCP) reads only the local catalogue.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write.** **Step 4: PASS.** **Step 5: Commit** `feat: local template catalogue and template-message preparation`.
- **Phase-0 gate:** live `templates sync` against the WABA needs V10/V12; until then the catalogue is populated from fixtures.

---

### Task 5: Delivery/status reconciliation UX

**Files:** Create `src/whatsvault/approval/reconcile.py`; Test `tests/test_reconcile.py`.

**Interfaces:**
- `reconcile.on_status_event(vault_conn, control_conn, status_event) -> dict` — if the event carries `biz_opaque_callback_data = "wv1:<atm_id>"` or a known `wamid`, links it to the `send_attempt` deterministically (INDETERMINATE → SUBMITTED); otherwise records a `POSSIBLE_MATCH` for human resolution, never auto-resolving.
- MCP `get_message_status` already surfaces the reduced lattice (Phase 2).

- [ ] **Step 1: Failing test** — a status event with a matching callback id resolves an INDETERMINATE attempt to SUBMITTED; an event with only recipient+time yields `POSSIBLE_MATCH` and leaves state unchanged; two same-minute sends to one recipient are not auto-attributed.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** `reconcile.py`. **Step 4: PASS.** **Step 5: Commit** `feat: deterministic status reconciliation with POSSIBLE_MATCH fallback`.

---

### Task 6: Full-suite gate + changelog

- [ ] `.venv/bin/pytest -q` green. Adversarial gate (spec §9 Phase 5): stale-draft misfire (Task 3), capability scope/expiry abuse (Task 1), template param injection (Task 4). Commit `test: close out Phase 5 (live template sync + APNs pending Phase 0)`.

## Self-Review

- Coverage: capability grants (use-only) → Tasks 1,2; prepare-only scheduler → Task 3; templates → Task 4; reconciliation → Task 5. Every send still passes the Phase 4 approval gate; the scheduler and MCP gain no authority. N1 side-channel and the no-autonomous-bot posture are honoured.
- Adversarial gate: capability abuse, stale misfire, template injection — Tasks 1,3,4.
- **Honest calibration:** all logic is TDD-testable now against fakes; live `templates sync` and APNs are Phase-0/Apple-gated. Scheduler correctness rests on `precondition_fn` — the test proves a stale precondition yields no draft, which is the whole safety property.
