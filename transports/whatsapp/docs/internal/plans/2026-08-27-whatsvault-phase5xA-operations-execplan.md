# WhatsVault Phase 5x-A — CLI + macOS Service Operations (STANDALONE EXECUTION PLAN)

> **JIT** from the roadmap, **Corrections Ledger #56 (CLI), #57 (launchd/service supervision), #52 (fs hardening)**, and the actual repo state (after Phase 5 core). **5x-B (backup/DR, #58) is NOT in scope** — it stays a frozen Deferred-Decision (DD3): *if the Mac + Keychain are lost, is the vault recoverable?* — resolved before any backup code.

> superpowers:executing-plans. TDD iron law. **Shell-gate discipline (Raouf):** never pipe the verification command that controls a commit; run it alone and check the exit code (see memory `shell-gate-exit-code-discipline`).

**Goal:** Make the whole local system operable — start, stop, recover, inspect — via a `whatsvault` CLI and launchd services, with strict filesystem/credential boundaries, and tests that operations can NEVER create approval authority (that stays phone-only).

## Bound to actual repo state
- No path convention exists yet → 5x-A introduces `whatsvault.ops.paths` (`WHATSVAULT_HOME`, default `~/.whatsvault`: `vault.db`, `control.db`, `blobs/`, `run/`, `logs/`).
- Reusable operations already exist: `doctor.check_vault/check_search/check_ingest`, `importers.whatsapp_export`, `approval.devices`, `ingest.dlq`, `keys`, `templates`, `approval.reconcile`, `apps.scheduler.scheduler`, `approval.sender.recover_startup`.
- No console entry point → add `[project.scripts] whatsvault = "whatsvault.cli.main:main"`.

## Ledger corrections folded in
- **#56** the `whatsvault` CLI with the verbs the architecture assumes (doctor, import/undo/reparse, devices enroll/revoke/list, dlq list/show/retry, keys list/retire, templates list, reconcile list/resolve/dismiss, scheduler list/enable/disable, health/status). Privileged ops are CLI-only; none creates approval authority.
- **#57** launchd definitions for ingest consumer, dispatcher, scheduler, local MCP (and later `whatsvault-meta`); crash/restart (KeepAlive), sleep/wake recovery, health checks, structured content-free logs.
- **#52** filesystem hardening: dirs `0700`, sensitive files `0600`, `umask 077`.

## Non-negotiable invariants (tested)
- **Operations cannot create approval authority.** No CLI verb / launchd service can produce a valid approval envelope, mint a capability, or trigger a send. Approval authority is the phone's Secure Enclave signature (Phase 4). `devices enroll` pins a device only via the device's own signed challenge.
- **Credential topology.** Exactly ONE process (`whatsvault-meta`) may hold the Meta token; MCP/ingest/scheduler/dispatcher hold none. No process can approve.
- **Startup recovery is conservative.** Stale `SUBMITTING` → `INDETERMINATE` (never blind resend); scheduler state reloads; queued-but-unconsumed approvals are surfaced, never auto-dispatched.

---

### Task 1: Path convention + filesystem hardening (#52)
**Files:** Create `src/whatsvault/ops/__init__.py`, `src/whatsvault/ops/paths.py`, `src/whatsvault/ops/fsperms.py`; Test `tests/test_ops_fsperms.py`.
**Interfaces:** `paths.Paths(home)` (properties `vault_db/control_db/blobs_dir/run_dir/logs_dir`); `paths.from_env(env=None)` (`WHATSVAULT_HOME` or `~/.whatsvault`); `fsperms.harden_umask()` (sets `077`, returns previous); `fsperms.ensure_dir(path, mode=0o700)`; `fsperms.ensure_secret_file(path, mode=0o600)`; `fsperms.check(paths) -> list[dict]` (flags any dir not `0700` / secret file not `0600`).
- [ ] Failing test: `ensure_dir` creates a `0700` dir even under a permissive umask; `ensure_secret_file` yields `0600`; `check` flags a loosened dir; `from_env` honours `WHATSVAULT_HOME`. → commit `feat(5xA): path convention + filesystem hardening (0700/0600/umask 077) (#52,#57)`.

### Task 2: Startup recovery orchestration
**Files:** Create `src/whatsvault/ops/recovery.py`; Test `tests/test_ops_recovery.py`.
**Interfaces:** `recovery.run_startup(vault_conn, control_conn, now_ms) -> dict` — calls `sender.recover_startup` (SUBMITTING→INDETERMINATE), counts reloadable `scheduled_jobs`, reports the ingest circuit state, and counts queued approval envelopes whose nonce is NOT yet consumed (surfaced for the dispatcher, never auto-sent).
- [ ] Failing test: a stranded SUBMITTING attempt becomes INDETERMINATE and is counted; scheduler jobs are counted; an accepted-but-unconsumed approval is reported as `pending_approvals` without any send. → commit `feat(5xA): conservative startup recovery orchestration`.

### Task 3: Health/status aggregation (structured, no content)
**Files:** Create `src/whatsvault/ops/health.py`, `src/whatsvault/ops/structlog.py`; Test `tests/test_ops_health.py`.
**Interfaces:** `health.status(vault_conn, control_conn) -> dict` (`{"ok":bool,"checks":[...],"summary":{dlq_depth,circuit_state,...}}` aggregating `doctor.check_vault/check_search/check_ingest`); `structlog.event(fields: dict) -> dict` (returns a log record; RAISES if any field name/value carries message content — keys `text/body/caption` or an `_wv_untrusted` payload are forbidden).
- [ ] Failing test: on a clean vault `status()["ok"]` is True with all checks; after `dlq.trip`, `ok` is False and the circuit shows OPEN; `structlog.event({"tool":"search"})` is fine but `structlog.event({"body":"secret"})` raises. → commit `feat(5xA): health/status aggregation + content-free structured logging`.

### Task 4: Process/credential topology (#43-adjacent)
**Files:** Create `src/whatsvault/ops/topology.py`; Test `tests/test_ops_topology.py`.
**Interfaces:** `topology.PROCESSES` (list of `{name, holds_meta_token, holds_vault_key, can_approve}`); `topology.check_invariants() -> list[dict]` (exactly one process holds the Meta token; no process `can_approve`; MCP/ingest/scheduler hold no Meta token).
- [ ] Failing test: exactly one process (`whatsvault-meta`) holds the Meta token; `mcp/ingest/scheduler/dispatcher` hold none; no process can approve; `check_invariants` returns all-OK for the real table and would flag a violation. → commit `feat(5xA): process/credential topology + invariants`.

### Task 5: `whatsvault` CLI + negative-authority assertion (#56)
**Files:** Create `src/whatsvault/cli/__init__.py`, `src/whatsvault/cli/commands.py`, `src/whatsvault/cli/main.py`; Modify `pyproject.toml` (`[project.scripts]`); Test `tests/test_cli.py`.
**Interfaces:** `commands.COMMANDS: dict[str, callable]` (handlers taking `(ctx, args)`), `commands.FORBIDDEN_VERBS: frozenset` (`approve, send, sign, dispatch, mint_capability, create_capability, send_message, get_credentials, export_vault`); verbs: `doctor, health, devices-list, devices-revoke, dlq-list, dlq-show, keys-list, templates-list, reconcile-list, reconcile-resolve, scheduler-list, scheduler-enable, scheduler-disable, import, import-undo, import-reparse`; `main.run(argv, ctx) -> int`.
- [ ] Failing test: `COMMANDS` keys are disjoint from `FORBIDDEN_VERBS` (#56 no authority); `doctor`/`health` handlers return check lists; `devices-list` lists enrolled devices; `dlq-list` lists DLQ rows; `scheduler-disable` flips `enabled`; `reconcile-resolve` transitions a candidate; there is NO handler that calls `sender.execute_write` or `capabilities.store_grant` with self-fabricated authority. → commit `feat(5xA): whatsvault CLI with operations verbs + negative-authority assertion (#56)`.

### Task 6: launchd service definitions + validator (#57)
**Files:** Create `apps/launchd/{ingest,dispatcher,scheduler,mcp}.plist`, `src/whatsvault/ops/launchd.py`; Test `tests/test_ops_launchd.py`.
**Interfaces:** `launchd.validate(plist_path) -> list[dict]` — parses the plist (`plistlib`), requires `Label/ProgramArguments/RunAtLoad/KeepAlive/StandardOutPath/StandardErrorPath`, asserts NO inline secret (no key whose value looks like a token/hex-key/`*_TOKEN`), and that the log paths are under a `logs/` dir.
- [ ] Failing test: each shipped plist validates (has KeepAlive for crash-restart, RunAtLoad, log paths, no inline secret); a crafted plist with an inline `META_TOKEN` is flagged. → commit `feat(5xA): launchd service definitions + plist validator (#57)`.

### Task 7: Full-suite gate + CHANGELOG + 5x-B deferred-decision note
**Files:** `docs/internal/findings/2026-08-27-5xB-backup-dr-deferred.md`; CHANGELOG.
- [ ] Record the frozen DD3 fork (recoverable vs permanent-loss) as an explicit open decision; `.venv/bin/pytest -q` (exit checked, not piped) + `pip check` green; commit `test: close out Phase 5x-A operations; 5x-B backup/DR remains a frozen deferred decision (#58)`.

## Self-review (consistency + ambiguity gate — required before execution)

- **SR-1:** ensure_dir chmods after makedirs (umask-independent). **SR-2:** keys-list surfaces DLQ-referenced recipient_key_ids (keys are Keychain-only). **SR-3:** check_invariants(processes=None) defaults to PROCESSES; a synthetic violation proves it bites. **SR-4:** CLI tests call main.run/handlers directly.

**Gate: PASS** — begin Task 1.
