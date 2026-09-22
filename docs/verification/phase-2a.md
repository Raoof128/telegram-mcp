# Phase 2a verification — privileged runtime, keys, consent, authority, storage, IPC

**Status:** Phase-2a authority foundation complete and qualified headlessly.
**NOT production.** No Telegram login, real account data, session file, Secure
Enclave signature, disclosure receipt, exposure ledger, audit anchor, installed
service account, loaded LaunchAgent, tunnel ingress or installed-client
acceptance is claimed. The Phase-2J join gate against the real signed consent
agent has **not** run: Plan 2b stops at its Task 1, so no byte agreement
between the Python broker and the Swift agent is claimed beyond the frozen JCS
vectors both sides assert against.

## Reproducibility

```bash
uv sync --locked
uv run python scripts/extract_contracts.py --check
uv run pytest tests/unit tests/contract tests/integration tests/security -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src/telegram_mcp
uv build
```

Host-mutating and host-interrogating tests are opt-in:

```bash
uv run pytest -q --run-platform-gated          # needs macOS + admin rights
TELEGRAM_MCP_AGENT_BUNDLE=<signed binary> uv run pytest tests/integration/test_join_gate.py -q --run-platform-gated
```

Install plans are inspectable without touching the host:

```bash
bash scripts/install_service_users.sh install --dry-run
bash scripts/install_paths.sh install --dry-run
plutil -lint scripts/consent-agent.plist
```

## Environment (2026-09-22, Australia/Sydney)

- macOS 27.0 (arm64); `uv` 0.11.29; `uv run python` 3.12.2.
- `mcp==2.2.0`, `telethon==1.45.0` (unused), `cryptography==50.0.1`,
  `jsonschema==4.26.0`, `pydantic==2.13.5`, `starlette==1.6.0`,
  `uvicorn==0.53.0`.
- `ruff` 0.16.8, `mypy` 2.3.1, `pytest` 9.1.1.
- `swiftc` Apple Swift 6.4 (consent-agent shell tests build from source).
- `/usr/bin/openssl` is **LibreSSL 3.3.6**, which is why `install_paths.sh`
  uses `-nodes` and not the OpenSSL 3 spelling `-noenc`; verified live.

## Actual results

- `uv sync --locked`: 67 packages resolved, 64 checked.
- `extract --check`: contracts check OK (23 files).
- Suites: `tests/unit` **269 passed, 1 skipped**; `tests/contract` **77
  passed**; `tests/integration` **39 passed, 14 skipped**; `tests/security`
  **55 passed, 2 skipped**; `tests/agent` **3 passed**. Gate command
  (`unit contract integration security`): **440 passed, 17 skipped**.
- Skips are all deliberate and named: 14 join-gate scenarios (no signed agent
  bundle), 2 install host probes (`--run-platform-gated`), 1 Phase-1 skip.
- `ruff check`, `ruff format --check`: clean over `src tests scripts` (74 files).
- `mypy src/telegram_mcp`: clean (42 files). `mypy src tests` is also clean.
- `uv build`: `telegram_mcp-0.1.10-py3-none-any.whl` and the sdist built.

## Hashes

- Spec: `36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a`
- `uv.lock`: `01a2a106e3ad8e63cf0f9d2cba9feec2da6e4227be958777e151051f5d6204f4`
- Wheel: `449b6991b8da6133710d19822ba342573124f5d3ce85f3723fe211852f47a534`

## What was proven

**Runtime lifecycle (Tasks 1).** Ordered startup 1–17 with fixed
`StartupFailed` per step, kernel-lockfile single-runtime rule with a liveness
probe (a stale lock is reclaimed, a live owner fails the start), DRAINING new
calls answering bounded `INTERNAL_ERROR`, shutdown that never calls Telegram
`log_out`, stop-while-OFF as no-op success, pre-start stray sweep matching on
exact argv element plus exact UID.

**Keys (Task 2).** `0600` files inside a `0700` store, key ids recomputed on
load and never stored, purpose separation asserted across ids, Ed25519 backup
key per spec §9.6.1, per-client lease seeds minted on demand, pin slots for the
agent approval and transport publics with fingerprints recomputed from stored
bytes on every verify.

**Consent (Task 3).** TG-JCS-v1 byte-frozen with vectors both plans assert
against, daemon Ed25519 challenge signatures, exact-once consume with the full
ApprovalEnvelope verified in order (challenge digest, pinned `key_id`, P-256
approval signature, liveness, atomic consume), 45-second monotonic deadline,
5/min and 30/hr rate limits, invalidation sweeps. There is no `approve` method
in the broker: a shell can trigger a prompt and can never approve one.

**Authority (Tasks 5–6).** Central engine with intersection semantics and
pre-serialize double evaluation; ten-prefix ref validation over one minter;
keyed cursor binding where the query digest is an HMAC over the canonical
request with `cursor` removed (never a plain SHA-256 of query text) and the
project scope digest binds ref, epoch, grant bits, egress level and excerpt
width; all seven invalidation triggers mapped onto the four spec codes, with
identity checked before state so a foreign presenter learns nothing and cannot
drop another principal's row; `runtime_id` binding so a restart invalidates
every cursor; epochs advancing on lock *and* unlock, unlock requiring presence.

**Storage (Task 7).** All 19 tables in §12.2 order, §12.4 indexes, the
`security_state` singleton seeded at epoch 1 unlocked, and the five Phase-3
tables present as reserved schema that stays empty through a full Phase-2 flow.
Both roadmap corrections are proven by direct-write tests: **C2** — the spec's
own CHECK admits an `excerpt` grant with a NULL width, because
`NULL BETWEEN 64 AND 4000` is NULL and SQLite accepts a NULL CHECK; the
transcription adds `IS NOT NULL` and keeps the 64–4000 range. **C4** — shared
membership inspects every *retained* membership, and enabling a project is
refused while an overlapping membership is still undeclared. §12.3 integrity is
enforced by triggers, so inconsistent direct writes fail: cross-account
`project_peers`, a `client_projects` grant without owner policy coverage, a
`message_refs` row whose account contradicts its peer, and cursor/receipt
tuples whose principal does not own the client. `open_db` refuses a symlinked
path, a loose directory and a corrupt database, and pins the WAL sidecars to
`0600`.

**IPC (Task 8).** Lease wire per §9.7.1 including the edges (`exp == iat+60`
accepted, `iat+61` refused; ±5s skew accepted, ±6s refused; boolean
timestamps, padded base64url, a 31-byte MAC, unknown and duplicate keys, a
15-byte nonce and a foreign `cid` all refused), with the MAC key derived from
seed plus `runtime_id` so a restart invalidates every lease at zero wire cost.
One frame codec serves both sockets: 64 KiB ceiling checked before the payload
is read, strict UTF-8, strict JSON. The admin socket authenticates with
`getpeereid`, routes the whole §33 surface, answers `NOT_AVAILABLE_IN_PHASE`
where nothing is implemented and `UNKNOWN_COMMAND` outside §33, refuses a
duplicate-key body before dispatch (`{"cmd": "lock", "cmd": "status"}` never
reaches a handler) and refuses non-UTF-8 input without its bytes reaching a
log line. RV-1 rendezvous completes only with the paired **transport** key,
enforces a 5-second handshake deadline and refuses a stale `runtime_id`.
Tunnel pins verify/add/rotate with history retained.

**CLI and slice (Task 9).** `demo` is unchanged. `start`/`stop`/`status` are
bootstrap control that works while OFF; `admin` proxies to the socket with
fixed exit codes; `doctor`, `keys`, `pair` and `rotate` run locally. There are
no test-only flags. The vertical slice drives one `telegram_list_chats` call
through validation, authority, broker issue/consume, the synthetic disclosure
gate, a fake adapter and real result assembly, then proves that revoking the
grant fails closed at authority with the broker never reached.

## Deviations from the plans, and why

1. **`ipc/framing.py` and `ipc/tunnel.py`** are not in Plan 2a's Task-8 file
   list. The frame codec is shared by the admin and rendezvous sockets and a
   second copy would be the defect; the tunnel pin record needs a home and the
   spec has no history table for it.
2. **Tunnel pin history** lives in a daemon-owned `0600` JSON-lines record in
   the key store, public material only. The spec stores the *current* pin as
   the `openai_tunnel` client's `auth_binding` and defines no history table,
   while the design requires rotation with history retention.
3. **`_seed` in the C2 contract test** also creates the `policy_state` row.
   Spec §12.3 requires a `client_projects` row to join an owner-principal
   client to a project on an account that principal has policy for; without
   that row the trigger fires first and would mask the CHECK under test. The
   C2 assertions themselves are the plan's, unchanged.
4. **`lock` requires a presence proof**, which is stricter than spec §9.3
   (where only `unlock` demands presence) and is what Plan 2a Task 8 asks for.
   `epochs.set_locked` still enforces presence for unlock only, so an internal
   emergency-lock path exists without the admin plane.
5. **`request_stop` now sends a framed control request** instead of the raw
   `b"STOP"` sentinel Task 1 wrote, because the admin socket speaks
   length-prefixed strict JSON; a bare sentinel is read as a length header and
   refused. `control` requests are deliberately outside the §33 `cmd` surface.
6. **`consume(handle, envelope)`** is the real broker signature; Plan 2a's
   Task-9 sketch wrote `consume(handle, agent_sig=...)`. The frozen wire is the
   whole ApprovalEnvelope, so the envelope form wins.
7. **`canonical_request_hmac` in the slice** is computed with the cursor-key
   query digest — the same keyed HMAC over the canonical request shape. The
   dedicated per-tool binding lands with the tool slices in Phase 4.
8. **`project_scope_digest` is bare lowercase 64-hex**, because that is what
   the frozen challenge wire carries. The Phase-3 receipt prints the same value
   with its `hmac-sha256:` label; the label belongs to that serializer.
9. **A fake Telegram adapter is defined inside the slice test**, not shipped in
   `src`, so nothing in the package can be mistaken for a real adapter.

## Carry-over corrections

- **C5** (direct-HTTPS OAuth tests): untouched here. Phase 2a adds no
  direct-HTTPS path; `release.profile` accepts `safe_demo`, `local_dev` and
  `production` only, and the startup refusal for real-data direct HTTPS remains
  Phase 1/7 material.
- **C7** (tunnel mTLS as the only production ingress): read as "only remote
  production route". The loopback coding-client ingress is retained: port 8766
  carries bearer leases, 8767 is the mTLS tunnel ingress in ChatGPT modes only,
  and `ports_for_mode` freezes that mapping.

## Gate ledger

| Gate | Status | Evidence | Unresolved dependency |
|---|---|---|---|
| E (consent/user presence) | **PARTIAL** | Broker exact-once, tamper, replay, rate-limit and invalidation tests; frozen JCS vectors | No real Touch ID signature; Phase-2J join gate not run (Plan 2b Tasks 2–4) |
| H (authority/policy) | **PARTIAL** | Deny-precedence matrix, intersection, double evaluation, epoch and grant invalidation, cursor code matrix | Live Telegram identities and tool slices are Phase 4 |
| M (storage integrity) | **PARTIAL** | 19 tables, C2/C4 direct-write proofs, §12.3 trigger proofs, index set, integrity/GC/permission tests | Phase-3 tables carry reserved schema only; no receipts or ledger rows exist to verify |
| N (privilege separation) | **PARTIAL** | Socket modes 0770/0660, `getpeereid` authentication, key store `0700`/`0600`, install plans, doctor checks | Service accounts not created on this host; `sudo -n -u` and direct-secret probes are platform-gated and unrun |
| P (operator control plane) | **PARTIAL** | Whole §33 surface routed with honest `NOT_AVAILABLE_IN_PHASE`, presence gate, lease edges, CLI wiring | Most §33 commands are unimplemented by design in this phase |
| R (privilege/key parts) | **PARTIAL** | Key inventory with distinct recomputed ids, pin slots, rotation with history, `doctor --production` gate set | `doctor --production` **fails** here, correctly: Telegram auth state, endpoint credential rejection and tunnel TLS trust cannot be verified before Phases 4 and 6 |

No gate is claimed PASS. Every PARTIAL states what is missing.

## Unresolved items

1. **Phase-2J join gate** — the whole gate is unrun. `tests/integration/test_join_gate.py`
   names all 13 scenarios and takes the bundle from `TELEGRAM_MCP_AGENT_BUNDLE`;
   the driver's prompt frames need Plan 2b Task 3 and the signed bundle needs
   Plan 2b Task 4.
2. **SMAppService exact API** — still unverified against current Apple
   documentation. `LaunchctlJobControl` drives `launchctl kickstart/stop` with
   interactive escalation; whether the registration should move to
   `SMAppService` (and its exact call shape) is an open implementation
   question, not a design change.
3. **Service-account install** — never executed. Both installers are
   idempotent with `--dry-run` plans the tests assert against, but no account,
   group, directory or certificate has been created on this host.
4. **Consent LaunchAgent** — `scripts/consent-agent.plist` lints and its
   content is asserted (`RunAtLoad` false, logs to `/dev/null`), but it has
   never been loaded, and its content remains owned by Plan 2b.
5. **Audit chain and external anchor** — out of scope here by design; the seam
   emits allowlisted events with no chain, checkpoints or anchor.
6. **`policy_state` multi-row support** — `bump_policy_epoch` refuses an
   ambiguous state rather than guessing, which is correct for V0.1.10's single
   owner and will need an explicit key at the call sites if that ever changes.
