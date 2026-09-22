# Telegram MCP Phase 2a Implementation Plan — On-Demand Runtime, Keys, Consent Broker, Authority, Storage, IPC

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the session-scoped privileged Python runtime: lifecycle/bootstrap, key registry, consent broker, DisclosureGate seam, central authority, SQLite migrations, admin IPC/leases, install scripts, and doctor — all testable headless with fake adapters and a stub signer.

**Architecture:** Single `telegram-mcp-runtime` process per start (service account `telegram-mcpd`), modular authority package, consent broker speaking the frozen challenge-wire contract to the Swift agent (Plan 2b), fake Telegram adapter seam for Phase 4. No thread does authorization and disclosure in one step; Phase 2 ends at consent-verified.

**Tech Stack:** Python 3.12+, asyncio, existing Phase-1 foundation (`contract`, `validation`, `config`, `dispatch`, `results`, `server`), SQLite (stdlib), `cryptography` (Ed25519, HMAC-SHA256, P-256 verify), Unix-domain sockets, macOS `dscl`/`launchctl` (install, platform-gated), pytest/pytest-asyncio.

**Spec:** [Phase-2 design](../specs/2026-09-22-telegram-mcp-phase-2-design.md) argues from [V0.1.10 engineering specification](../../../telegram-mcp-v0.1.10-final-engineering-spec.md) (SHA-256 `36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a). Read with the [release roadmap](2026-09-22-telegram-mcp-release-roadmap.md), Phase 2 row.

**Status:** Draft for review. No implementation has started. Commands and code below are instructions for the implementation stage, not evidence of completed tests.

## Global Constraints

- Python: 3.12 or newer. Existing pins stand (`mcp==2.2.0`, `Telethon==1.45.0` unused, `cryptography`, `pydantic>=2.12,<3`).
- Consent-wait default: 45 seconds; Telegram execution deadline: 15 seconds (Phase 4 consumes it); client timeout guidance: ≥75 seconds.
- Lease: lifetime ≤60 seconds; clock skew ≤5 seconds; `tgml1` wire exactly per spec §9.7.1.
- Cursor TTL: 15 minutes; GC at startup, opportunistically at most hourly, best-effort at shutdown.
- Challenge nonce: 128 bits; `runtime_id`: 128 bits, memory-only, fresh per start.
- Ports: 8766 (loopback bearer), 8767 (loopback mTLS, ChatGPT modes only). Sockets: `/private/var/run/telegram-mcp/` 0770, `consent.sock`/`admin.sock` 0660, group `telegram-mcp-admin`.
- Refs: `prefix + 26 chars [a-z2-7]`, 130-bit CSPRNG, non-sequential, SQLite-mapped.
- Key IDs recomputed on load (`ed25519:`/`p256:`/`spki:sha256` + 64 lowercase hex). No private material in SQLite, logs, or YAML/`.env`/argv.
- DRAINING new calls: `INTERNAL_ERROR`. Stop while OFF: no-op success. Shutdown never calls Telegram `log_out()`.
- Display digest: `SHA256("telegram-mcp-display-v1" || JCS(display_payload))`, lowercase hex, constant-time compare.
- JCS rule: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`; challenge objects contain only str/int/bool/None (floats raise).
- HMAC domains: cursor/query binding `b"telegram-mcp-cursor/v1\0"`, scope digests `b"telegram-mcp-scope/v1\0"`, leases `b"telegram-mcp-local-lease/v1\0"`.
- No production deployment, no real Telegram login, no remote push in this plan.

## Review Focus

1. Stale-socket start with a live owner must never delete another runtime's socket — Task 1 pins it, own the liveness proof.
2. Duplicate JSON keys in admin-socket payloads must fail before dispatch — Task 8 pins it via the strict decoder.
3. New sensitive calls arriving during DRAINING must get bounded `INTERNAL_ERROR`, never hang or half-execute — Task 1 pins it.
4. Lease acceptance at exactly ±5s skew and `exp == iat+60` must behave per spec (accept inside, reject outside) — Task 8 pins the edges.
5. Non-UTF8 bytes on the admin socket must fail closed without logging payload bytes — Task 8 pins it.

## Scope and execution preparation

Work in `/Users/raoof.r12/Desktop/Raouf/Telegram`. Read `AGENT.md`, `CHANGELOG.md`, the Phase-2 design doc, and `docs/verification/phase-1.md`. The workspace is a Git repository on `main`; do not push or create remotes. Service-user creation, LaunchAgent loading, and Touch ID tests are platform-gated (macOS + admin + hardware) and run interactively; everything else runs headless in CI style. Swift agent work belongs to Plan 2b; this plan consumes only the frozen challenge-wire contract (§4 of the design) and writes the `tests/fixtures/consent/jcs_vectors.json` artifact that Plan 2b asserts against.

Every task ends with its targeted tests and an explicit-path commit. A dependency/API mismatch is a recorded failed prerequisite, never a reason to weaken a check.

## File responsibilities

Paths relative to the workspace.

| Files | Responsibility |
|---|---|
| `src/telegram_mcp/runtime/__init__.py`, `runtime/lifecycle.py`, `runtime/lock.py` | States OFF/STARTING/READY/STOPPING, `runtime_id`, ordered startup, drain shutdown, PID lockfile |
| `src/telegram_mcp/runtime/bootstrap.py` | `start`/`stop`/`status`, `--local`/`--chatgpt`/`--all` modes, child PID tracking |
| `src/telegram_mcp/keys/registry.py`, `keys/store.py` | Normative key table, Phase-2 provisioning only, `0600` files, fingerprint recompute |
| `src/telegram_mcp/keys/pairing.py` | Pin export/import, on-screen fingerprint compare helpers |
| `src/telegram_mcp/consent/challenge.py` | JCS bytes, display digest, daemon sign/verify, agent-sig verify |
| `src/telegram_mcp/consent/broker.py` | In-memory `tgu_` map, exact-once consume, 45s timeout, rate limits, invalidation |
| `src/telegram_mcp/consent/gate.py` | `DisclosureGate` protocol + `SyntheticDisclosureGate` |
| `src/telegram_mcp/authority/policy.py` | Central scope service, intersection, double evaluation, egress minimum |
| `src/telegram_mcp/authority/refs.py`, `authority/cursors.py`, `authority/epochs.py` | Mint/validate refs, cursor binding/GC/triggers, epoch ops + lock |
| `src/telegram_mcp/storage/db.py`, `storage/migrations.py`, `storage/settings.py` | Open/pragmas/checks, 19 tables + C2/C4 + triggers + indexes, closed settings registry |
| `src/telegram_mcp/ipc/admin.py`, `ipc/leases.py`, `ipc/rendezvous.py` | Admin socket + peer creds + §33 commands, `tgml1` mint/verify, consent.sock server + handshake |
| `src/telegram_mcp/audit_seam.py` | `AuditEvent` model, attribution fields, allowlist emission (no chain) |
| `src/telegram_mcp/doctor.py` | Checks incl. `--production` and `--off` probe |
| `scripts/install_service_users.sh`, `scripts/install_paths.sh` | One-time privileged install (interactive admin auth, platform-gated); consent LaunchAgent plist content defined in Plan 2b, installed by the launcher here |
| `src/telegram_mcp/cli.py` (extend) | Wire new verbs; keep `demo` untouched |
| `tests/fixtures/consent/jcs_vectors.json` | Frozen JCS vectors + digests + test-key material for Plan 2b |
| `docs/verification/phase-2a.md` | Actual run evidence and unresolved gates |

## Task 1: Runtime lifecycle, lock, bootstrap

**Files:** Create `src/telegram_mcp/runtime/__init__.py`, `src/telegram_mcp/runtime/lock.py`, `src/telegram_mcp/runtime/lifecycle.py`, `src/telegram_mcp/runtime/bootstrap.py`, `tests/unit/test_runtime.py`, `tests/integration/test_runtime_off.py`.

**Interfaces:** Consumes nothing new. Produces `RuntimeContext(runtime_id: bytes, started_at: float)`, `acquire_lock(path) -> LockHandle` (raises `RuntimeActive` if a live owner holds it, reclaims stale), `run_lifecycle(config, *, mode) -> None` (ordered steps 1–17, DRAINING on stop), `bootstrap_status() -> dict` (works while OFF), `request_stop(timeout) -> None` (socket shutdown, TERM fallback, no-op while OFF).

- [ ] **Step 1: Write the failing OFF-assertion test.**

```python
import socket

from telegram_mcp.runtime.bootstrap import bootstrap_status


def test_off_means_nothing_listens():
    status = bootstrap_status()
    assert status["state"] == "OFF"
    for port in (8766, 8767):
        with socket.socket() as sock:
            sock.settimeout(1)
            try:
                sock.connect(("127.0.0.1", port))
            except OSError:
                continue
            raise AssertionError(f"port {port} accepts while OFF")
```

- [ ] **Step 2: Run `uv run pytest tests/integration/test_runtime_off.py -q`; expect missing-module failure.** Implement `bootstrap_status()` reading only the PID lockfile (absent/stale → `OFF`, live → query admin socket with 1s timeout, unreachable → `STALE`).
- [ ] **Step 3: Write the lock tests.** Live-owner start fails `RuntimeActive`; stale lockfile (dead PID) is reclaimed; a live Unix socket with no lockfile owner blocks deletion (prove by holding a bound socket in the test and asserting the sweeper leaves it).

```python
import os

from telegram_mcp.runtime.lock import RuntimeActive, acquire_lock


def test_double_start_fails_closed(tmp_path):
    first = acquire_lock(tmp_path / "runtime.lock")
    try:
        with pytest.raises(RuntimeActive):
            acquire_lock(tmp_path / "runtime.lock")
    finally:
        first.release()
```

- [ ] **Step 4: Implement `lock.py`.** Lockfile format: `pid:<int>\nstarted:<rfc3339>\n`. Liveness: `os.kill(pid, 0)` (ESRCH → stale; EPERM → live). Release removes only if the file still names our PID/start. Never delete a socket path unless the lockfile proves no live owner AND no process holds the socket (attempt `bind`; EADDRINUSE → refuse).
- [ ] **Step 5: Write the DRAINING test.** Start lifecycle with a fake adapter that blocks; call `request_stop`; assert new sensitive dispatch during drain returns `INTERNAL_ERROR` and in-flight work is cancelled after the 5s grace (use a short grace override parameter, default 5.0).

```python
async def test_draining_rejects_new_calls():
    from telegram_mcp.runtime.lifecycle import RuntimeContext, drain

    ctx = RuntimeContext(runtime_id=b"\x00" * 16, started_at=0.0)
    result = await drain(ctx, new_call=lambda: "INTERNAL_ERROR")
    assert result == "INTERNAL_ERROR"
```

- [ ] **Step 6: Implement `lifecycle.py`.** `RuntimeContext` holds `runtime_id = secrets.token_bytes(16)` minted once per start. `drain(ctx, *, grace=5.0, new_call)` is the drain-phase unit: mark DRAINING, route any `new_call` to the fixed `INTERNAL_ERROR` result, cancel queued work, give in-flight calls `grace` seconds, then cancel the remainder. Ordered `startup()` runs steps 1–17 with seams (`load_secrets`, `open_db`, `handshake_consent`, `open_listeners`, `start_tunnel`) injected as callables so tests run headless; each step raises a fixed safe error on failure and aborts before READY. `shutdown()` calls `drain()`, then closes Telegram (disconnect-only; assert no `log_out` symbol reachable from the fake adapter interface), closes SQLite, removes sockets, releases the lock, stops the consent UI.
- [ ] **Step 7: Implement `bootstrap.py`.** `start --local/--chatgpt/--all` escalates through interactive sudo (admin password or Touch ID if install enabled pam_tid; never passwordless) to `launchctl kickstart system/<label>`; refuses double start. `stop` sends admin-socket shutdown with timeout, then interactive-sudo `launchctl stop system/<label>`, then SIGTERM via the same path as last resort, then reaps; no-op success while OFF. `status` (OFF/STARTING/READY/STOPPING + mode + pid, never secrets) needs no privilege. Child PIDs (tunnel-client, consent UI) tracked in a runtime-owned PID file; at start a live PID with exact cmdline-plus-UID match is terminated and started fresh.
- [ ] **Step 8: Run `uv run pytest tests/unit/test_runtime.py tests/integration/test_runtime_off.py -q` and the full suite; commit `feat: add on-demand runtime lifecycle`.**

## Task 2: Key registry and file store

**Files:** Create `src/telegram_mcp/keys/__init__.py`, `src/telegram_mcp/keys/registry.py`, `src/telegram_mcp/keys/store.py`, `src/telegram_mcp/keys/pairing.py`, `tests/unit/test_keys.py`.

**Interfaces:** Consumes `RuntimeContext` (keys are loaded per start). Produces `KeySpec` (frozen dataclass: `algorithm: str`, `owner: str`, `persistent: bool`, `required_phase: int`), `KEY_REGISTRY: dict[str, KeySpec]` (12 rows), `provision_missing(store_dir, *, phases=(2,)) -> list[str]` (creates only Phase-2 rows), `load_key(name) -> bytes` (verifies `0600`, recomputes fingerprint), `key_id(name) -> str`.

- [ ] **Step 1: Write the failing registry test.**

```python
from telegram_mcp.keys.registry import KEY_REGISTRY


def test_registry_has_twelve_purposes_phase_split():
    assert len(KEY_REGISTRY) == 12
    phase2 = {n for n, s in KEY_REGISTRY.items() if s.required_phase == 2}
    assert "disclosure-key" not in phase2
    assert "challenge-key" in phase2
```

- [ ] **Step 2: Run it; expect missing-module failure.** Implement `registry.py` with the design §3 table verbatim (algorithm, owner, persistent, phase): principal/cursor/privacy HMAC seeds, challenge Ed25519, per-client lease seeds, tunnel TLS/mTLS references (provisioned by install, not this store), agent approval key (device-bound, never here), disclosure/audit-checkpoint/audit-chain/backup rows marked phase 3.
- [ ] **Step 3: Write store tests.** Provision creates only Phase-2 rows; second provision never overwrites; `0600` enforced (chmod 0644 fixture → load raises fixed error); fingerprint recompute matches `ed25519:sha256:<hex>` of raw public bytes for Ed25519 and `spki:sha256` for tunnel certs; unknown purpose raises.

```python
def test_no_phase3_private_keys_provisioned(tmp_path):
    from telegram_mcp.keys.store import provision_missing

    created = provision_missing(tmp_path, phases=(2,))
    assert not any("disclosure" in name or "audit" in name or "backup" in name for name in created)
```

- [ ] **Step 4: Implement `store.py`.** `secrets.token_bytes(32)` for HMAC seeds; Ed25519 via `cryptography`; atomic write + `os.chmod 0o600`; parent dir must be `0700` owned by current euid or raise. Never log material; errors fixed strings.
- [ ] **Step 5: Implement `pairing.py`.** `export_public(name) -> bytes`, `import_peer_pin(name, public_bytes) -> str` (stores public + fingerprint, returns fingerprint for on-screen compare), `verify_fingerprint(name, shown: str) -> bool` (constant-time). Daemon challenge pin goes to the agent via the keychain pairing record in Plan 2b; this module handles the daemon side only.
- [ ] **Step 6: Run targeted + full suite; commit `feat: add key registry and file store`.**

## Task 3: Consent challenge bytes and broker

**Files:** Create `src/telegram_mcp/consent/__init__.py`, `src/telegram_mcp/consent/challenge.py`, `src/telegram_mcp/consent/broker.py`, `tests/unit/test_consent.py`, `tests/fixtures/consent/jcs_vectors.json` (written by the test run, then frozen).

**Interfaces:** Consumes `RuntimeContext` (`runtime_id`), `load_key` (challenge Ed25519 seed), agent public key (pinned bytes; tests use a stub signer). Produces `canonical_challenge(...) -> bytes` (exact JCS), `display_digest(payload) -> str`, `issue(...) -> str` (`tgu_` handle), `consume(handle, agent_sig) -> ConsumedChallenge` (exact-once, verifies daemon-independent agent signature), `invalidate(...)`, `StubSigner` (test-only signer/verify pair; production signer lives in Plan 2b).

- [ ] **Step 1: Write the failing JCS test.**

```python
from telegram_mcp.consent.challenge import jcs_dumps


def test_jcs_is_sorted_compact_utf8():
    assert jcs_dumps({"b": 1, "a": "é"}) == b'{"a":"\\u00e9"'.replace(b"\\u00e9", "é".encode()) + b',"b":1}'
```

(Note: `ensure_ascii=False`, so `é` stays literal UTF-8: expected bytes `b'{"a":"é","b":1}'`.)

- [ ] **Step 2: Run it; expect missing-module failure.** Implement `jcs_dumps(obj) -> bytes` (`sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`; reject floats with `ValueError`).
- [ ] **Step 3: Write display-digest + vector tests.** `display_digest` equals `sha256(b"telegram-mcp-display-v1" + jcs).hexdigest()` on a frozen vector; tampered display bytes change the digest; vectors file contains ≥3 frozen challenge objects with exact JCS hex, display digests, challenge digests, and daemon Ed25519 signatures over each JCS made with the fixture challenge key (seed `b"\x01" * 32`) for Plan 2b byte-equality.

```python
import hashlib

from telegram_mcp.consent.challenge import display_digest, jcs_dumps


def test_display_digest_vectors():
    payload = {"client_display": "Codex", "action_display": "read", "project_display": ["P"], "peer_display": None, "risk_class": "excerpt"}
    raw = jcs_dumps(payload)
    assert display_digest(payload) == hashlib.sha256(b"telegram-mcp-display-v1" + raw).hexdigest()
```

- [ ] **Step 4: Implement `challenge.py`.** Signed set exactly: `canonical_request_hmac`, `display_digest`, principal, client, account, tool, `policy_epoch`, `project_scope_digest`, `security_epoch`, `runtime_id` (hex), nonce (16 random bytes → 22-char b64url), expiry. Daemon Ed25519 signs raw JCS bytes; signature base64url. Agent-signature verify takes pinned P-256 public bytes, ECDSA-SHA256 over the same JCS bytes, DER signature. `tgu_` handle: `tgu_` + 26 chars from 130 CSPRNG bits via `mint_opaque_ref(prefix)` defined here; Task 6 deletes its own copy and imports this one.
- [ ] **Step 5: Write broker tests.** Exact-once (second consume fails), 45s expiry (frozen clock override), 5/min + 30/hr rate limits, invalidation on epoch/grant/disconnect, tampered challenge bytes fail agent-sig verify, wrong-key signature fails, shell-triggered issuance still requires agent signature (no auto-approve path exists in the module — assert no `approve()` without signature exists via `not hasattr`).

```python
async def test_replay_fails():
    from telegram_mcp.consent.broker import ConsentBroker, ConsentError
    from telegram_mcp.consent.challenge import StubSigner

    stub = StubSigner(seed=b"\x07" * 32)
    broker = ConsentBroker(challenge_key=b"\x01" * 32, agent_verify=stub.verify, runtime_id=b"\x00" * 16)
    handle = await broker.issue(tool="telegram_status", request_hmac="ab" * 32, principal="prn_" + "a" * 26, client="tcl_" + "b" * 26, account="tga_" + "c" * 26, policy_epoch=1, project_scope_digest="0" * 64, security_epoch=1, display_digest="1" * 64)
    await broker.consume(handle, agent_sig=stub.sign(broker.challenge_bytes(handle)))
    with pytest.raises(ConsentError):
        await broker.consume(handle, agent_sig=stub.sign(broker.challenge_bytes(handle)))
```

(Exact `issue()` signature follows the signed set; `agent_sig` verification uses the stub signer in tests.)

- [ ] **Step 6: Implement `broker.py`.** `StubSigner(seed)` (test-only, lives in `challenge.py`): deterministic Ed25519 fixture keypair with `sign(msg) -> bytes` and bound `verify(sig, msg) -> bool` (public key implicit to the fixture pair; production passes a closure over the pinned agent key with the same two-argument shape). Fixture-seed table (normative for both plans; Plan 2b asserts the same values): challenge daemon key `b"\x01" * 32`, `StubSigner` default `b"\x07" * 32`, vertical-slice `b"\x09" * 32`. `ConsentBroker(challenge_key, agent_verify, runtime_id)` keeps an in-memory dict only (never SQLite); monotonic clock with an injectable `now()` override for tests; per-client rate buckets; `challenge_bytes(handle)` accessor for tests; `invalidate_where(predicate)` for epoch/grant/disconnect sweeps; fixed-code `ConsentError`s mapping to `CONSENT_DENIED`/`CONSENT_UNAVAILABLE`/`DEADLINE_EXCEEDED` at the dispatch layer (wiring comes in Task 9's vertical slice; broker raises, never builds MCP results).
- [ ] **Step 7: Freeze `jcs_vectors.json`** (run generator test once, inspect bytes by eye against the JCS rule, commit the file as frozen), then assert its shape in a test (`version == 1`, ≥3 cases, each with `input`, `jcs_hex`, `display_digest`, `challenge_digest`, daemon `sig`); run targeted + full suite; commit `feat: add consent challenge bytes and broker`.

## Task 4: DisclosureGate seam and audit seam

**Files:** Create `src/telegram_mcp/consent/gate.py`, `src/telegram_mcp/audit_seam.py`, `tests/unit/test_gate.py`.

**Interfaces:** Consumes `ConsumedChallenge`. Produces `DisclosureGate` (Protocol: `async def authorize_disclosure(self, *, challenge, snapshot) -> DisclosureDecision`), `SyntheticDisclosureGate` (always allow, zero accounting), `AuditEvent` (frozen dataclass: no bodies/queries/usernames/phones/raw IDs; fields mirror Appendix C), `emit_audit(event) -> None` (allowlist log + in-memory sink for tests).

- [ ] **Step 1: Write the failing seam tests.**

```python
from telegram_mcp.consent.gate import DisclosureDecision, SyntheticDisclosureGate


async def test_synthetic_gate_accounts_nothing():
    gate = SyntheticDisclosureGate()
    decision = await gate.authorize_disclosure(challenge=None, snapshot=None)
    assert isinstance(decision, DisclosureDecision) and decision.allowed is True
    assert gate.accounted_records == 0
```

- [ ] **Step 2: Run; expect missing-module failure.** Implement `gate.py` (Protocol + synthetic) and `audit_seam.py` (`AuditEvent` with `event_id`, `ts`, `tool_name`, refs, epochs, counts, `status`, `error_code`; `emit_audit` logs allowlisted fields only and appends to the test-visible sink exposed as `get_audit_sink() -> list`; chain/checkpoint/receipt/ledger types are explicitly absent — assert `not hasattr(audit_seam, "AuditChain")`).
- [ ] **Step 3: Run targeted + full suite; commit `feat: add disclosure gate and audit seams`.**

## Task 5: Central policy engine

**Files:** Create `src/telegram_mcp/authority/__init__.py`, `src/telegram_mcp/authority/policy.py`, `tests/unit/test_policy.py`.

**Interfaces:** Consumes storage rows (Task 7 defines DDL; this task defines the engine against an abstract view so both stay testable). Produces `PolicyView` (frozen dataclass: `owner_allows: set[str]`, `owner_denies: set[str]`, `memberships: dict[str, set[str]]`, `grants: dict[tuple[str, str], dict]`, `policy_epoch: int`, `project_epochs: dict[str, int]`, `security_epoch: int`), `Decision` (frozen dataclass: `allowed: bool`, `egress: str | None`, `reason: str`), `AuthorityChanged` (exception with `.code: str`), `evaluate(view, *, client, project_refs, peer) -> Decision`, `check_pre_serialize(view, snapshot) -> None` (raises `AuthorityChanged`).

- [ ] **Step 1: Write the failing intersection test.**

```python
from telegram_mcp.authority.policy import PolicyView, evaluate


def test_deny_beats_allow_at_every_layer():
    view = PolicyView(owner_allows={"peer1"}, owner_denies={"peer1"}, memberships={"p1": {"peer1"}}, grants={("c1", "p1"): {"can_read": True, "egress": "full_text"}}, policy_epoch=1, project_epochs={"p1": 1}, security_epoch=1)
    decision = evaluate(view, client="c1", project_refs=["p1"], peer="peer1")
    assert decision.allowed is False
```

- [ ] **Step 2: Run; expect missing-module failure.** Implement `policy.py`: ordered checks per §10.8 (resolve client → validate refs → enabled → can_read → can_cross_search for cross → owner policy → intersect membership → snapshot epochs/digests). Most-restrictive egress across contributing grants (`metadata_only < excerpt < full_text`, smallest excerpt limit). No layer expands another (test each expansion attempt).
- [ ] **Step 3: Expand tests.** Deny-precedence matrix (3 layers × allow/deny), shared-peer minimum egress, missing grant row = deny (row-absence, never default-create), unknown project → `REF_NOT_FOUND`, disabled project/client → deny, pre-serialize revalidation raising each of the five codes on the matching mutation.
- [ ] **Step 4: Run targeted + full suite; commit `feat: add central policy engine`.**

## Task 6: Refs, cursors, epochs, lock

**Files:** Create `src/telegram_mcp/authority/refs.py`, `src/telegram_mcp/authority/cursors.py`, `src/telegram_mcp/authority/epochs.py`, `tests/unit/test_refs_cursors.py`.

**Interfaces:** Consumes cursor HMAC key + privacy key (store), `RuntimeContext`. Produces `mint_opaque_ref` re-export (imported from `consent/challenge.py`; this task deletes any duplicate), `validate_ref_format(ref) -> str` (returns prefix or raises `ValueError`), `CursorStore` (Protocol: `put(ref, record)`, `get(ref)`, `delete(ref)`, `purge_expired(now) -> int`; Task 7 binds SQLite, tests use the in-memory fake), `mint_cursor(...) -> str`, `check_cursor(store, keys, *, ref, presenter, now, runtime_id)` raising the four cursor codes, `bump_policy_epoch(state)`, `bump_project_epoch(state, project)`, `set_locked(state, locked)` (against injected state dicts shaped like the Task 7 rows; Task 7 binds).

- [ ] **Step 1: Write the failing ref test.**

```python
import re

from telegram_mcp.authority.refs import mint_opaque_ref


def test_ref_shape_and_uniqueness():
    refs = {mint_opaque_ref("tpr_") for _ in range(100)}
    assert len(refs) == 100
    assert all(re.fullmatch(r"tpr_[a-z2-7]{26}", r) for r in refs)
```

- [ ] **Step 2: Run; expect missing-module failure.** Implement `mint_opaque_ref`: 130 CSPRNG bits → 26 lowercase unpadded Base32 chars, in `authority/refs.py` as a thin import of `consent/challenge.py`'s minter (no second copy exists).
- [ ] **Step 3: Write cursor/epoch tests.** Keyed-HMAC `query_digest` (mutating query text changes digest; plain-SHA256 oracle test: assert digest != sha256(text)); 15-min TTL expiry → `CURSOR_EXPIRED`; each of the 7 triggers → its code (epoch bumps, grant change without bump via scope-digest recompute, security-epoch advance → `INVALID_CURSOR`, wrong presenter → `INVALID_CURSOR`, restart (`runtime_id` change) → `INVALID_CURSOR`); GC drops only expired; lock/unlock epoch behavior.
- [ ] **Step 4: Implement `cursors.py`/`epochs.py`/`refs.py`.** `project_scope_digest` = HMAC-SHA256 (privacy key, domain `b"telegram-mcp-scope/v1\0"`) over JCS of sorted project id/epoch/grant vectors; list-projects vector variant per spec. `query_digest` = HMAC-SHA256 (cursor key, domain `b"telegram-mcp-cursor/v1\0"`) over canonical shape excluding `cursor`.
- [ ] **Step 5: Run targeted + full suite; commit `feat: add refs, cursors, epochs`.**

## Task 7: SQLite migrations with C2/C4

**Files:** Create `src/telegram_mcp/storage/__init__.py`, `src/telegram_mcp/storage/db.py`, `src/telegram_mcp/storage/migrations.py`, `src/telegram_mcp/storage/settings.py`, `tests/contract/test_migrations.py`, `tests/security/test_storage_integrity.py`.

**Interfaces:** Consumes epoch/cursor binding functions (Task 6), closed settings definitions. Produces `migrate(conn) -> int` (current version; transactional; rerunnable), `open_db(path) -> Connection` (0700 dir check, 0600 files, `foreign_keys=ON` verified, `quick_check`, migrations, startup GC), `SETTINGS_REGISTRY`, `bind_cursor_store(conn)`, `bind_epoch_state(conn)`.

- [ ] **Step 1: Write the failing C2 test (direct SQLite, no app code).**

```python
import sqlite3

import pytest


def _seed(conn):
    conn.execute(
        "INSERT INTO accounts(id, account_ref, telegram_user_id, created_at, updated_at)"
        " VALUES (1, 'tga_" + "a" * 26 + "', 1001, '2026-09-22T00:00:00Z', '2026-09-22T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO principals(id, principal_ref, principal_key, auth_mode, created_at)"
        " VALUES (1, 'prn_" + "b" * 26 + "', 'k1', 'local', '2026-09-22T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO mcp_clients(id, principal_id, client_ref, auth_kind, auth_binding, client_kind, enabled, created_at)"
        " VALUES (1, 1, 'tcl_" + "c" * 26 + "', 'bearer', 'b1', 'codex_local', 1, '2026-09-22T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO projects(id, account_id, project_ref, slug, display_name, enabled, project_epoch, created_at, updated_at)"
        " VALUES (1, 1, 'tpr_" + "d" * 26 + "', 's1', 'S1', 1, 1, '2026-09-22T00:00:00Z', '2026-09-22T00:00:00Z')"
    )


def test_excerpt_null_rejected(tmp_path):
    from telegram_mcp.storage.migrations import migrate

    conn = sqlite3.connect(tmp_path / "t.db")
    migrate(conn)
    _seed(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO client_projects(client_id, project_id, egress_level, excerpt_max_codepoints, created_at, updated_at)"
            " VALUES (1, 1, 'excerpt', NULL, '2026-09-22T00:00:00Z', '2026-09-22T00:00:00Z')"
        )


def test_excerpt_width_accepted_and_others_reject_limits(tmp_path):
    from telegram_mcp.storage.migrations import migrate

    conn = sqlite3.connect(tmp_path / "t.db")
    migrate(conn)
    _seed(conn)
    conn.execute(
        "INSERT INTO client_projects(client_id, project_id, egress_level, excerpt_max_codepoints, created_at, updated_at)"
        " VALUES (1, 1, 'excerpt', 200, '2026-09-22T00:00:00Z', '2026-09-22T00:00:00Z')"
    )
    conn.execute("DELETE FROM client_projects")
    conn.execute(
        "INSERT INTO client_projects(client_id, project_id, egress_level, excerpt_max_codepoints, created_at, updated_at)"
        " VALUES (1, 1, 'metadata_only', NULL, '2026-09-22T00:00:00Z', '2026-09-22T00:00:00Z')"
    )
    conn.execute("DELETE FROM client_projects")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO client_projects(client_id, project_id, egress_level, excerpt_max_codepoints, created_at, updated_at)"
            " VALUES (1, 1, 'full_text', 200, '2026-09-22T00:00:00Z', '2026-09-22T00:00:00Z')"
        )
```

- [ ] **Step 2: Run; expect missing-module failure.** Transcribe all 19 `CREATE TABLE`s from the spec (§12, exact lines: `schema_version` L1091, `accounts` L1100, `principals` L1113, `mcp_clients` L1128, `projects` L1148, `project_peers` L1169, `client_projects` L1186 with the C2-fixed CHECK, `peers` L1211, `policy_state` L1231, `peer_policy` L1250, `message_refs` L1269, `cursors` L1288, `security_state` L1315, `disclosure_receipts` L1329, `exposure_ledger` L1366, `verification_keys` L1389, `audit_checkpoints` L1404, `audit_events` L1423, `settings` L1461) plus §12.4 indexes and §12.3 triggers (same-account `project_peers`, grant consistency, receipt/cursor consistency). Any transcription doubt is settled by re-reading the spec lines, never by inventing constraints.
- [ ] **Step 3: Write C4 + integrity tests.** Second-project add rejected unless `shared`; enable rejects undeclared overlap; all retained (not just enabled) memberships inspected; cross-account `project_peers` insert fails; inconsistent cursor principal/client insert fails; `foreign_keys=OFF` connection refused; corrupt DB file fails closed on open; required indexes exist (`sqlite_master` query); expired cursors purged at open, live ones kept.
- [ ] **Step 4: Implement `db.py`/`migrations.py`/`settings.py`.** `settings.py` holds the code-defined closed registry (budget-threshold keys reserved for Phase 3 but inert, checkpoint cadence, release metadata; typed validators; unknown keys rejected; bodies/queries/URLs/phones never stored).
- [ ] **Step 5: Bind Task 6 protocols to SQLite** (`bind_cursor_store`, `bind_epoch_state`) with round-trip tests; run targeted + full suite; commit `feat: add SQLite migrations with C2/C4`.

## Task 8: Admin IPC, leases, rendezvous, tunnel identity, install, doctor

**Files:** Create `src/telegram_mcp/ipc/__init__.py`, `src/telegram_mcp/ipc/admin.py`, `src/telegram_mcp/ipc/leases.py`, `src/telegram_mcp/ipc/rendezvous.py`, `src/telegram_mcp/doctor.py`, `scripts/install_service_users.sh`, `scripts/install_paths.sh`, `scripts/consent-agent.plist`, `tests/unit/test_ipc.py`, `tests/security/test_install.py` (platform-gated).

**Interfaces:** Consumes authority engine, broker, store, `RuntimeContext`. Produces `serve_admin(socket_path, router)`, `verify_peer(uid, gid) -> bool`, `mint_lease(client, epoch) -> str` / `verify_lease(token, *, now) -> LeaseClaims`, `serve_rendezvous(...)`, tunnel SPKI pin verify/add/rotate, `doctor(checks) -> report`, install scripts (interactive admin auth).

- [ ] **Step 1: Write the failing lease test.**

```python
from telegram_mcp.ipc.leases import mint_lease, verify_lease


def test_lease_round_trip_and_shape():
    token = mint_lease(seed=b"\x03" * 32, client="tcl_" + "d" * 26, epoch=7, now=1_000_000)
    assert token.startswith("tgml1.") and len(token) <= 1024
    claims = verify_lease(token, seeds={"tcl_" + "d" * 26: b"\x03" * 32}, epoch=7, now=1_000_010)
    assert claims.client.endswith("d" * 26)
```

- [ ] **Step 2: Run; expect missing-module failure.** Implement `leases.py` exactly per §9.7.1: canonical JSON sorted keys no whitespace, fields `aud="telegram-mcp-loopback"`, `cid`, `exp`, `iat`, `nonce` (16B → 22-char b64url), `sec`, `v=1`; MAC over domain + ASCII payload, constant-time compare; reject >1024 chars pre-decode, unknown/dup keys (strict decode), bad b64, 16B nonce, `exp > iat+60`, skew >5s, `sec != current epoch`.
- [ ] **Step 3: Write IPC tests.** Admin command over socket with correct peer → served; wrong UID → refused; non-UTF8 payload → fixed error without payload logging; duplicate-key admin JSON → rejected pre-dispatch (strict decode; a `{"cmd": "lock", "cmd": "status"}` body never dispatches); `lock`/`unlock` require presence proof object (stub in tests); every §33 command routed, unknown command → fixed error; tunnel SPKI: unpinned/expired/replaced rejected, `rotate-binding` succeeds and history retained. Lease edges: `exp == iat+60` accepted, `exp == iat+61` rejected; skew exactly ±5s accepted, ±6s rejected.
- [ ] **Step 4: Implement `admin.py`/`rendezvous.py`.** asyncio Unix servers; macOS peer credentials via `LOCAL_PEERCRED` with a self-test asserting the reported UID equals a known client UID (platform-gated); rendezvous performs the pinned-key handshake from Task 3 at connect.
- [ ] **Step 5: Write install scripts + doctor.** `install_service_users.sh`: exact `dscl` create sequence for `telegram-mcpd`/`telegram-mcp-tunnel` (non-login shell `/usr/bin/false`, home `/var/empty`, allocated UID/GID printed for review), group `telegram-mcp-admin` with operator member; `install_paths.sh`: `/private/var/run` dir + socket parents, DB dir `0700`, plus tunnel server-cert and mTLS client-cert generation (exact `openssl` commands with SPKI printout for the pin record); consent LaunchAgent plist content defined in Plan 2b (`agent/ConsentAgent-Info.plist`), loaded/unloaded by the launcher here. `doctor.py`: permission/ownership/pin/epoch/rotation checks, `--production` gate set, `--off` probe (ports closed, sockets absent, no runtime/tunnel processes, no Telegram connection).
- [ ] **Step 6: Platform-gated tests** (`install` + Touch ID-adjacent presence paths + `sudo -n -u` failure for both accounts + cross-account secret unreadability) marked `platform_gated`; run headless suite + `ruff`/`mypy`; commit `feat: add admin IPC, leases, install, doctor`.

## Task 9: CLI wiring and Phase-2a evidence

**Files:** Modify `src/telegram_mcp/cli.py`; create `docs/verification/phase-2a.md`; modify `README.md`, `AGENT.md`, `CHANGELOG.md`.

**Interfaces:** Consumes all Tasks 1–8. Produces working `telegram-mcp start/stop/status` (+ modes + admin passthrough) and the evidence record. `demo` verb behavior unchanged.

- [ ] **Step 1: Write the CLI smoke test.** `start --local` in a sandbox HOME (fake service user = current euid via `--as-self` test-only flag? No — test-only flags in production CLI are a backdoor. Instead: drive `run_lifecycle` + `serve_admin` directly in-process with tmp sockets, then assert `status` output shape. CLI arg parsing tested with `--help` only.)

```python
def test_status_shape_while_off(capsys):
    from telegram_mcp.runtime.bootstrap import bootstrap_status

    assert set(bootstrap_status()) >= {"state", "mode", "version"}
```

- [ ] **Step 2: Wire `cli.py`.** Verbs `start/stop/status/doctor/admin/keys/pair/rotate`; admin verbs proxy to the socket with fixed error mapping; startup config errors exit nonzero with fixed messages (never Pydantic dumps); `demo` untouched.
- [ ] **Step 3: Vertical slice test (broker → gate → fake adapter).** With an allow-grant seeded in a tmp database, `StubSigner`, and the fake adapter returning one canned peer, drive a `telegram_list_chats` call through validation → authority → broker issue/consume → `SyntheticDisclosureGate` → fake retrieval → success result validating against the assembled output schema. Then revoke the grant and assert the same call fails closed without invoking the broker. This is the only test that touches all three layers together.

```python
async def test_vertical_slice_needs_consent_not_just_policy():
    from telegram_mcp.consent.broker import ConsentBroker
    from telegram_mcp.consent.challenge import StubSigner
    from telegram_mcp.consent.gate import SyntheticDisclosureGate

    stub = StubSigner(seed=b"\x09" * 32)
    broker = ConsentBroker(challenge_key=b"\x01" * 32, agent_verify=stub.verify, runtime_id=b"\x02" * 16)
    gate = SyntheticDisclosureGate()
    handle = await broker.issue(tool="telegram_list_chats", request_hmac="cd" * 32, principal="prn_" + "e" * 26, client="tcl_" + "f" * 26, account="tga_" + "g" * 26, policy_epoch=1, project_scope_digest="2" * 64, security_epoch=1, display_digest="3" * 64)
    consumed = await broker.consume(handle, agent_sig=stub.sign(broker.challenge_bytes(handle)))
    decision = await gate.authorize_disclosure(challenge=consumed, snapshot=None)
    assert decision.allowed is True
```
- [ ] **Step 4: Full reproducibility gate.**

```bash
uv sync --locked
uv run python scripts/extract_contracts.py --check
uv run pytest tests/unit tests/contract tests/integration tests/security -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src/telegram_mcp
uv build
```

- [ ] **Step 5: Write `docs/verification/phase-2a.md`** (commands, versions, results, hashes, C5/C7 carry-over, gate ledger E/H/M/N/P + privilege/key parts of R as partial-until-joins-2b, unresolved items incl. SMAppService exact API), update `README.md`/`AGENT.md`/`CHANGELOG.md` with the Raouf template; commit `test: qualify the Phase-2a authority foundation`.

## Phase-2a acceptance and explicit exclusions

Complete when the runtime starts/stops cleanly with OFF assertions green, challenges round-trip with exact-once guarantees, authority matrices pass, migrations prove C2/C4 plus integrity failures, leases verify at the edges, install/doctor scripts pass platform-gated runs, and evidence states its limits. No disclosure accounting, receipts, audit chain, real Telegram adapter, tunnel-client runtime, or production deployment is claimed — those belong to Phase 3/4/6 or Plan 2b.
