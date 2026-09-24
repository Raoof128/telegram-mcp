# Comms v0.3 verification

Spec: `docs/comms-spec-v0.3.md` (rev 2 + R-001; SHA-256 prefix `0a893d8d6a6b92a8`). Design:
`docs/superpowers/specs/2026-09-24-comms-v0.3-design.md` (rev 2 + R-001; `b91671682376f102`). Both
pins live in `docs/verification/comms-v0.3-rulings.md`, and `tests/security/test_v03_preflight.py`
fails on any edit that is not pinned there. Plan: `docs/superpowers/plans/2026-09-24-comms-v0.3.md`
rev 2, executed inline and test-first. A fail-fast full gate runs before every commit.

Nothing here has touched Telegram or Meta. Live acceptance is owner-run and counts as evidence only.

## Part A: the constitutional cutover

Commits `f3d744b` … this section (branch `comms-v0.3`, base `94df7bc`).

### What exists now

| Area | Where |
|---|---|
| One audit chain engine, profile-parameterised (`COMMS`, `LEGACY_TELEGRAM`) | `comms/core/audit/chain.py`; the legacy module is a thin binding |
| One anchor engine (`comms/audit-head-anchor/v1`) | `comms/core/audit/anchor.py` |
| `comms.db` schema v2: audit chain, integrity latch, lineage, exact-next-state cutover | `comms/core/storage/migrations.py` |
| `AuditWriter`: commit, then anchor exactly that head under one lock; typed audit events | `comms/core/audit/{writer,specs}.py` |
| The integrity latch: blocks new effects, never the recording of started ones | `comms/core/audit/integrity.py`, the claim CAS, `Engine` |
| The cutover state machine, legacy side and comms side, replay-safe | `comms/core/audit/cutover.py`; `transports/telegram/runtime/cutover_barrier.py` |
| The legacy seal and `tgml1` retirement, enforced by the legacy database | legacy `Migration(3)`: `audit.append_state`, `auth.tgml1_state` |
| `verify_all`: legacy chain (from a signed root) → seal → lineage → genesis → comms chain → anchor | `comms/core/audit/verify_all.py` |
| Historical verification standing alone | `transports/telegram/legacy_verify` |
| The retired surfaces, out of production composition | daemon serves admin only; `runtime/legacy_composition.py` is the historical harness |
| The seed `TOOL_CATALOG` and closed `comms_*` dispatch | `comms/mcp/{catalog,dispatch}.py` |

### The exit gate (design §A.9)

`tests/security/test_v03_part_a_exit.py` holds the owner's table verbatim. It checks that the table matches the design, then re-runs every owning test in a fresh process. None may fail, be skipped or be deselected.

| Group | Checks | Owning tests |
|---|---|---|
| Spec | v0.3 normative; precedence resolved; manifest green | `test_supersession.py`, `test_v03_preflight.py` |
| Legacy surface | the 16 names are not advertised and a direct call causes zero effects; `serve` (and `demo`) refused; `apps/mcp` absent | `test_catalog_skeleton.py`, `test_tombstones.py`, `test_v03_retired_surfaces.py` |
| History | v1/v2 receipts verify; the legacy chain verifies (vectors byte-identical); key coverage complete | `test_legacy_verify.py`, `test_legacy_chain_vectors.py` |
| Cutover | drained, sealed, anchored; `cut_` durable; genesis bound; chained and anchored; 12 crash boundaries converge; a mismatched lineage fails closed | `test_cutover_{legacy,comms,crashes}.py`, `test_verify_all.py` |
| New surface | `comms_*`, closed dispatch; deterministic digest; collision-free; honest annotations; no raw RPC; no secrets (canary) | `test_catalog_skeleton.py`, `test_ai_boundary.py`, `test_refs_v03.py` |

### Gate at the Part A head

| Check | Result |
|---|---|
| `uv sync --locked`; `extract_contracts.py --check` | OK |
| `uv run pytest -q` | **2385 passed**, 4 skipped |
| `scripts/e2e_smoke.py` | **57/57** (the new cutover phase: `run_cutover` to COMPLETE, `verify --all` green, `tgml1` retired, idempotent rerun) |
| `pytest tests/formal` | 544 states, 22 assertions; campaign model 96,528 states, 11 properties |
| ruff, ruff format, mypy (131 source files), `uv build` | clean |
| WhatsVault suite | **450 passed** (539 − 89 removed with `apps/mcp`, R-A16) |

### Accounting

- **Tests.** Since A1, 2220 IDs became 2375 (`comms-v0.3-collected-after-a.txt`). Ten IDs left the collection:
  - two were removed with the v0.2 no-send guard (D5);
  - seven were replaced by named tests that are collected;
  - one is a parametrize ID that embeds an object address, so it is normalised.

  Unexpectedly missing: **0** (`test_v03_test_accounting.py`). The 89 WhatsVault removals account for its whole difference.
- **Smoke.** Of the 52 legacy checks, 19 are retained and 33 are retired, each with its surface: `telegram_mcp_v0.1`, the project/grant authority, the policy engine, and `tgml1` issuance. The retired checks still run against the historical harness until D37. Six checks are new (`comms-v0.3-smoke-map.json`, `test_smoke_map.py`).
- **WhatsVault subtree.** The byte-identical pin became a stronger one: every divergence from the measured tree must be listed under its ruling (`test_whatsvault_provenance.py`).

### Rulings made in Part A

Registered in `comms-v0.3-rulings.md`: R-000 (tooling), R-001 (`cmg_`/`ctp_`), R-002 (profiles, `aev_`/`ack_`), R-003 (`LegacyPort`, the DB-enforced seal), R-004 (`advance_comms`; the DB-enforced `tgml1` retirement), R-005 (`verify_chain(root=)` early; the legacy final-marker rule), R-006 (the historical harness; `demo` retired; exit 8), R-007 (29 retired admin commands; `consent*` stay tombstoned; the `surface=` seam), R-008 (the tombstone scope; strict JSON to core; `auth headers` retired), R-A16 (the WhatsVault edit), R-A20 (the AI-boundary rewrite). Smaller execution rulings are in the plan ledger.

### Waiting on the owner

- **R-A20:** the plan empties `.claude/settings.json`'s always-ask rules for `comms campaign send` / `retry-failed` (D5). That loosens the permission guardrail on the executing agent's own actions, so it is left for the owner to apply or confirm. No test depends on either state.
- The out-of-repo runbook steps (Secure Enclave keys, the consent bundle, the GitHub rename, the archive and the folder move) remain separately approved.

Deferred minor: an intermittent uvicorn `CancelledError` traceback at smoke shutdown, which predates v0.3. The ledger still passes.

### Tag

`comms-v0.3-part-a` is an annotated local tag on the commit that adds this section. It is **not pushed**, and nothing is ever rebased across it. Tag object `38b28bb43ad702859ac23250f8760acf15412bc0` → commit `fcae4ffd4b66a154c43ee1962d85e380cb7df52a`.

No production claim.

## Part B: durability, audit, keys, retention, recovery, backup

Commits `855c408` … this section, after the `comms-v0.3-part-a` tag (`fcae4ff`).

### What exists now

| Area | Where |
|---|---|
| Chain epochs: seal and open in the caller's transaction; verify across contiguous sealed epochs and from a signed root (the five probe attacks are named regressions) | `comms/core/audit/chain.py` |
| The truncation root: the latest verified checkpoint at or before the cutoff | `comms/core/audit/retention_root.py` |
| The key inventory (design §B.4): every purpose with its rotation and destruction rule; staged rotation (stage → prove → activate) with orphans; `audit-chain-key` rotation opens an epoch (epoch *n* ↔ key version *n*); checkpoint and cursor consequences; backup-signer trust states; retired HMAC keys kept only while a dependency is proven | `comms/core/keys/{purposes,slots,rotate,signers,retired}.py` |
| The keyed campaign commitment on the chain (A12) | `comms/core/delivery/commitment.py`, freeze |
| The daemon-owned 0600 secret store (no Keychain, R-B11); the `comms.db` rekey with recovery at every boundary; staged provider-credential rotation with re-check and rollback | `comms/core/keys/{files,secrets}.py`, `storage/rekey.py`, `credentials.py` |
| Telegram session revoke (two transactions, one `auth.LogOut`), durable error mapping, recovery by login with `--new-account` | `transports/telegram/telegram/admin_rpc.py`, adapter, identity |
| Retention in foreign-key order: legacy exposure, legacy chain truncation behind a root, receipts, message refs, the comms chain prefix, campaign bodies, directory identities, retired key material, the maintenance event; fails closed on a bad root | `comms/core/maintenance/{retention,redaction}.py`, `transports/telegram/runtime/legacy_retention.py` |
| `audit repair` through an ancestor-proving verifier | `comms/core/audit/repair.py` |
| Backups: age-v1 X25519 (66 vendored C2SP vectors, interop with age 1.3.2), detached `comms-backup-signature/v1`, the `comms-backup/v1` payload and binding, transfer frames, export, staged import, commit (a new epoch) | `comms/core/backup/*` |
| `comms doctor`; eight runbooks | `comms/core/doctor.py`, `docs/runbooks/` |
| Two formal models, mutation-tested; a 200-walk differential test against the engine | `formal/{audit_model,keys_model}.py` |

### Gate at the Part B head

| Check | Result |
|---|---|
| `uv run pytest -q` | **2980 passed**, 4 skipped |
| `scripts/e2e_smoke.py` | **57/57** |
| `pytest tests/formal` | 44 passed: 544 states / 22 assertions; campaign model 96,528 states; audit model 215,040 states; keys model 512 states |
| ruff, ruff format, mypy, `uv build` | clean |
| WhatsVault suite | **450 passed** |

### Measured facts carried

- **N1**: SQLCipher accepts `PRAGMA rekey` inside `BEGIN IMMEDIATE`, so `rekey` refuses while a transaction is open (`test_rekey_refuses_inside_a_transaction`).
- **N2**: `write_anchor` (temp file, fsync, rename, fsync the directory) measured a median of 0.21 ms, p95 0.26 ms and max 0.37 ms over 200 runs, so per-event anchoring is kept and nothing is batched. **Bound:** `os.fsync` on macOS is not `F_FULLFSYNC`, the same as the legacy anchor; a power loss can lose the last anchor write, which the integrity latch and `audit repair` exist to recover.
- **N7**: the `security` CLI cannot take a secret on stdin, so there is no Keychain backend (R-B11).

### The exit gate

`tests/security/test_v03_part_b_exit.py` pins the plan's list verbatim — epochs; the root rule; anchor per event (N2 recorded); the inventory; the commitment; revoke and recovery; retention; backup; doctor; runbooks; the two models — and re-runs every owning test in a fresh process. None may fail, be skipped or be deselected, so the age interop tests must run: the gate is proven on a host with age(1).

### What the tests found

- **The crash table** (`test_part_b_crash_tables.py`): a re-commit of an import that had already applied staged a new chain key before checking the base digest, leaving an orphan. The commit now refuses before staging anything and destroys a staged key if its transaction refuses.
- **B9**: rotating away from a revoked backup signer tried to set it back to `TRUSTED_RETIRED`; the one-way trigger refused the rotation. Retirement now applies only to an `ACTIVE` signer.
- **B27 self-review**: `trust_key` could have overridden a local compromise mark; a signer this installation marked `VERIFICATION_ONLY` or `REVOKED` is now refused even when named.
- **B31**: the audit model's first run caught its own abstraction accepting an epoch that lost the event its seal signs; the engine already enforced it.

### Rulings made in Part B

Registered: R-B11 (no Keychain). The ledger records the smaller ones task by task (B2 key callable, B3 `skipped=`, B4 literal additions, B5 orphan definition, B6 epoch ↔ key version, B8 unaudited freeze path, B10 transport-supplied window, B12–B13 signatures, B14 sanctioned `LogOutRequest`, B16 bump on every login, B17 `LegacyRetention`, B18 truncated genesis, B19 draft-content redaction, B20 `disabled_at`, B21 `blocked`, B22 vector subset, B24 hex sidecar, B25–B27 new `cin_`/`cbi_` prefixes, B28 epoch-opening import, B29–B30 scope, B31–B32 model definitions).

### Waiting on the owner

Unchanged from Part A: the R-A20 `.claude/settings.json` change, and the out-of-repo runbook steps.

### Tag

`comms-v0.3-part-b` is an annotated local tag, **not pushed**: tag object `89fc6b23689d5d49646f18e87416a1bc47528541` → commit `c135599656a9b5479afb26d6656ec9091f2ccf3a`.

No production claim.

## Part C: provider adapters

Commits `1ed921f` … this section, after the `comms-v0.3-part-b` tag (`c135599`).

### Adapter inventory

| Adapter | Contracts (A18) | Where | Provider surface |
|---|---|---|---|
| `telegram_bot` | delivery, capability, admin, context | `transports/telegram/bot/` | Bot API, pinned to `https://api.telegram.org`; one `POST` per call from a closed method set; token only in the path, redacted from httpx's log |
| `telegram_user` | delivery, capability, admin, context | `transports/telegram/user/`, adapter `telegram/telethon_adapter.py` | MTProto via Telethon 1.45.0 (the only Telethon importer); `READ_RPCS`/`WRITE_RPCS`/`ADMIN_RPCS` by capability, each its own recorder operation |
| `whatsapp_cloud` | delivery, capability, admin | `transports/whatsapp/cloud/` | Meta Graph API v21.0, pinned to `https://graph.facebook.com`; media by id through the Meta-only downloader |
| `whatsapp_webhooks` | inbound_context, provider_updates | `transports/whatsapp/webhooks/` | the raw ASGI ingress (`GET`/`POST /webhooks/meta`), the durable inbox and the resumable fan-out |

Shared: `core/providers/{capability,protocols,semantics,ratelimit}.py` (capability ids and states, `ADAPTER_CONTRACTS`, the `SEMANTICS` idempotency table, normalized rate limits); `transports/net.py` (the origin pin and the IDNA exact-suffix host rule); `transports/telegram/{peers,message_text,args,admin_profiles,chat_specs,capabilities,page_bounds}.py` and `transports/whatsapp/numbers.py` (one copy of each shared rule); `core/campaigns/{render,templates}.py`, `core/delivery/window.py`; the registry `comms/runtime/adapters.py`, wired only through `composition.build_comms_adapters`.

### API versions and fixtures

- Bot API envelope shape (unchanged since 2.0), methods as of Bot API 9.x; Graph API **v21.0**; Telethon **1.45.0** (its layer's `messages.*ForumTopic` requests); httpx 0.28.1.
- `tests/fixtures/providers/PROVENANCE.json` pins 58 fixtures by SHA-256 with provider, API version, source and capture date: 38 Bot API envelopes (`telegram_bot/`) and 20 Graph API bodies (`meta/`). All are hand-built from the documented shapes and say so; none is a live capture. The Meta contract oracle is WhatsVault's `FakeGraph` (R-C27), deterministic, with HMAC-signed webhooks.

### Gate at the Part C head

| Check | Result |
|---|---|
| `uv run pytest -q` | **3540 passed**, 4 skipped |
| `scripts/e2e_smoke.py` | **57/57** |
| `pytest tests/formal` | 44 passed: 544 states / 22 assertions; campaign model 96,528 states; audit model 215,040 states; keys model 512 states |
| ruff, ruff format, mypy, `uv build` | clean |
| WhatsVault suite | **450 passed** |

### What the tests found

- **Hidden replay (C14).** Telethon's `MTProtoSender._reconnect` re-queues every in-flight request after an automatic reconnect: a dropped `sendMessage` would be sent twice. The client now runs with `auto_reconnect=False` and `connection_retries=0`; a control test shows the default replays.
- **Tokens in logs (C6).** httpx logs every request URL at INFO and its exceptions hold the URL; the Bot API token lives in the path. A redacting filter and a fixed, unchained `BotTransportError` close both (mutation-checked).
- **Chat ids (C16, C21).** Channel ids were marked by string concatenation (`-100` + N), right only for ten-digit ids; now `-(10**12 + N)`, proved equal to Telethon's `get_peer_id` (the 5b-4 test fixture delegates to it).
- **Campaign content (C23).** Campaign content is `{canonical, fa, en, links, media}` but the Telegram transports accepted only `{text}`: every real campaign would have frozen as skipped. `core/campaigns/render.py` is the one rendering rule, proved through a real freeze.
- **A leaking repr (C31).** `DeliveryResult` printed its provider ref, which for Telegram names the chat; it is now `repr=False`.
- **Missing conformance (C32).** `whatsapp_webhooks` advertised two contracts with no cases; the first live-mode run reported `NO_CASES`. Three cases now cover them.
- **Guards (C15, C33).** The adapter boundary (only the adapter imports Telethon; only composition imports the adapter) moved the send and rights reads behind neutral types; the tombstone closure moved the page bounds out of `disclosure/bounds.py`; the wire-domain guard renamed a `comms-` literal.

### The exit gate

`tests/security/test_v03_part_c_exit.py` pins the plan's list verbatim — conformance over every contract, with zero unexplained skips; classification tables; semantics; egress; `random_id`; retries pinned; the window and templates; the webhook; canaries; fixture provenance — and re-runs every owning test in a fresh process. It also runs the whole conformance registry in-process: every advertised contract of all four adapters passes with zero skips.

### Rulings made in Part C

Registered: R-C27 (the Meta oracle in WhatsVault's `fake_meta.py`). The ledger records every smaller one task by task (C3 support and saga table; C6–C13 the Bot API client, classification, capability, admin tables and poller; C14 RPC sets; C15 key persistence and the collision rule; C16–C21 the MTProto transports; C22–C26 Meta; C28–C29 the webhook ingress and inbox; C30 rate limits; C33 the registry; the inbound-body retention phase).

### Follow-ups (carried into Part D)

- **The live update stream (C21).** The client stays `receive_updates=False`; turning the stream on makes Telethon issue `updates.GetDifference`, which needs its own review. The consumer, the translation and the single-owner claim are built.
- **The daemon call (C33).** The legacy daemon does not yet open `comms.db` or the secret store; Part D's comms runtime calls `build_comms_adapters`.
- **The WhatsVault archive (C29).** The worker takes an idempotent `Archive`; binding WhatsVault's importer (private to its app today) belongs with that runtime.
- **Live acceptance (C32).** The runbooks and the opt-in, evidence-only run exist; with no accounts described every live case reports `NOT_CONFIGURED`. The Groups API endpoint shapes (C26) are unverified until that run.
- **Per-recipient template language (C23)** and `chat.set_photo` (needs the media path) are Part D.

### Waiting on the owner

Unchanged: the R-A20 `.claude/settings.json` change, and the out-of-repo runbook steps.

### Tag

`comms-v0.3-part-c` is an annotated local tag, **not pushed**: tag object `b7501031a7608a62334d8d1132af23bde811289c` → commit `631b776fead53fdfde02096e37fbf807a8860dfe`.

No production claim.
