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

## Part D: services, MCP, CLI, ingress, OAuth, smoke, client runbooks

Commits `4ef3cf5` … this section, after the `comms-v0.3-part-c` tag (`631b776`).

### Catalog inventory

`TOOL_CATALOG` (`src/comms/mcp/catalog.py`, families under `src/comms/mcp/tools/`) holds **109 tools**, pinned by order, per-tool digest and overall digest in `tests/mcp/catalog_pin.json`. The catalog digest is `4380d08525605e6f5842a03d4c7b58fc740e73d774a73fddbe5191aff6ce6514`.

| Family | Tools |
|---|---|
| group | 39 |
| campaign | 15 |
| message | 12 |
| whatsapp (templates, account, phone, webhook) | 8 |
| context | 7 |
| audience | 7 |
| location | 6 |
| capability | 5 |
| media | 4 |
| account | 3 |
| telegram (bot, user status) | 2 |
| admin (`comms_admin_identity_inspect`) | 1 |

- **Reads and writes:** 52 are read-only and 57 are writes that require a `req_` request id.
- **Annotations:** 19 are destructive and 79 are open-world.
- **Egress:** every tool declares its class in `EGRESS_MATRIX` (`src/comms/mcp/egress.py`).
- **Not offered: 13 tools.** `facades.NOT_OFFERED` answers `PROVIDER_UNSUPPORTED` without reaching a provider:
  - message.forward;
  - media upload and download;
  - group.members_get, permissions_get, topic_get and topic_list, invite_list, join_requests_list, admin_log and create;
  - account.profile;
  - whatsapp.phone_status.

  The adapters or services for these are not built.
- **CLI:** the `comms` CLI generates one command per tool for the campaign, location, audience, group, message and template families (`template` carries `comms_whatsapp_template_*`, design D.7).

### Capability inventory

`core/providers/capability.py` defines 68 capability ids and 8 states. Only an explicit `AVAILABLE` counts as available.

| Adapter | Capabilities advertised |
|---|---|
| `telegram_user` | 37 (every Telegram capability) |
| `telegram_bot` | 26 |
| `whatsapp_cloud` | 29, of which 8 are Groups capabilities reported only when discovery finds them |

Resolution follows P §68 (`services/capability.py`), and the provider's answer is final (A25).

### Versions

| Component | Version |
|---|---|
| MCP SDK | `mcp` 2.2.0 |
| MCP protocol | `2026-07-28` (stateless; the stdio proxy pins it) |
| Telethon | 1.45.0 |
| Meta Graph API | v21.0 |
| Bot API | 9.x method set |
| Web stack | httpx 0.28.1, starlette 1.6.0, uvicorn 0.53.0, pydantic 2.13.5 |
| Database | sqlcipher3 0.6.2 |

Schema v4 adds the following:

- groups;
- mutations and operation records;
- `recipients.display_name`;
- ctx handles and cursors;
- `security_epoch`;
- clients;
- `oauth_refresh_tokens`.

### Counts (gate at the Part D head)

| Check | Result |
|---|---|
| `uv run pytest -q` | **5023 passed**, 4 skipped (opt-in host and live tests) |
| `scripts/e2e_smoke.py` | **74/74** |
| `pytest tests/formal` | 57 passed: 544 states / 22 assertions; campaign model 96,528 states; audit, keys and operations models (4,728 states, 0 violations, 7 mutations caught) within the 57 |
| ruff, ruff format, mypy, `uv build` | clean |
| WhatsVault suite | **450 passed** |

### Canaries

`tests/security/test_v03_egress.py` plants eight canaries in a running world:

- a message body;
- a display name;
- a phone number;
- a Telegram user id;
- a chat id;
- a bot token (in the secret store);
- a `cml1` lease seed;
- an OAuth owner code.

It then drives all 109 tools through the real facades and dispatcher, and sweeps every output, the CLI's printed JSON, the root logger and every `comms.db*` file. The results:

- **Bodies:** appear only in `untrusted_text`, and only in the body-bearing reads.
- **Names:** appear only under `untrusted`, and only in listing reads.
- **Identities:** appear only in `comms_admin_identity_inspect`.
- **Secrets:** appear nowhere.
- **Database files:** no canary appears in plaintext in the encrypted files.

The Part C adapter canaries (`tests/security/test_adapter_canaries.py`) still pass.

**Not swept:** the stdio proxy's frames (they relay the HTTP answers unchanged), backup sidecars and webhook responses (fixed bodies).

### `audit verify --all`

The smoke's comms phase runs the cutover when it builds its world. It then drives every surface: stdio, local HTTP, remote OAuth, the CLI over the admin socket, a campaign, an admin operation, a WhatsApp write and a replay. After that it runs `verify_all` over the legacy chain, the lineage and the comms chain. The check "audit verify --all is clean after every surface wrote" passes, with all three parts `ok`.

The operator command `comms audit verify --all` parses, but it is not yet wired to a handler. See the follow-ups.

### Release gate (P §88)

Every row is owner-run acceptance (Task D39). None has run, and the gate proves none of them. The runbooks are `docs/runbooks/live-acceptance-{telegram,whatsapp}.md` and `docs/runbooks/clients-{claude-code,codex,chatgpt}.md`.

| Row | Status |
|---|---|
| Telegram read/context | PENDING OWNER |
| Telegram bot writes | PENDING OWNER |
| Telegram user writes | PENDING OWNER |
| Campaign delivery | PENDING OWNER |
| WhatsApp Cloud sending | PENDING OWNER |
| WhatsApp webhooks | PENDING OWNER |
| Capability discovery | PENDING OWNER |
| Admin actions | PENDING OWNER |
| Context provenance | PENDING OWNER |
| MCP ChatGPT | PENDING OWNER |
| MCP Codex | PENDING OWNER |
| MCP Claude Code | PENDING OWNER |
| Audit | PENDING OWNER |
| Crash recovery | PENDING OWNER |
| Privacy | PENDING OWNER |
| WhatsApp Groups (optional; may be `UNAVAILABLE`) | PENDING OWNER |

**Blocker for every row.** The daemon does not yet open `comms.db` and serve the comms composition. The smoke assembles the composition in process. Until the daemon does this, no client can connect to a running daemon.

### Claim boundary (D5, verbatim from `docs/comms-spec-v0.3.md`)

> `owner_full_admin` intentionally grants the connected MCP host authority to invoke typed write tools. Comms does not require an independent human-presence ceremony.
>
> **Claim boundary.** Comms does **not** guarantee that a model will perform only the writes that match the owner's semantic intent. Content retrieved from Telegram or WhatsApp is untrusted, and a model may be misled by it. The defences against model or tool misuse are:
>
> - typed tools, with no raw-RPC tool (P §37);
> - explicit opaque targets, and ambiguity refusal (`AMBIGUOUS_TARGET`, P §41);
> - capability checks, with the provider's response as final authority (A25);
> - host permission UX and honest annotations (P §3);
> - request-id idempotency and durable operation records (A27, A28);
> - audit evidence on an anchored chain (A7, A8).
>
> Two further limits are not claimed:
>
> - **Cross-client duplicates.** Two independently issued, semantically identical instructions from different clients are not detected as duplicates. Only one operation identity, `(authenticated_client, request_id)`, is protected.
> - **Tool selection.** A model's choice of tool is acceptance evidence, never a proven property.

### What the tests found

- **Dispatcher (D26).** The dispatcher called services directly, so a `CommsError` escaped as an exception instead of becoming a structured error. It now calls through `ServiceRegistry.call`.
- **Request ids (D12, D13).**
  - A malformed request id reached SQLite as an `IntegrityError`; it is now `INVALID_ARGUMENT` before storage.
  - Adapters did not validate saga steps before recording; every step is now validated first.
- **Transactions (D15).** The 5b-4 core wrote in its own never-nested transaction, so a service write could not share the executor's audited transaction. Each core write was split into a `*_in_tx` body.
- **OAuth clients (D37).** The remote OAuth client must be a registered clients row: a bare `cli_` ref is refused as disabled. The smoke now registers it, which is the production contract.
- **The SDK (D32–D34).** Found while testing it:
  - redirect URIs must be `AnyUrl`;
  - a `Route` given a function treats it as a request handler;
  - token expiry is checked against real time;
  - the SDK needs a lifespan, which the router forwards.
- **Flaky assertion (D13).** A resolve assertion could match random base32 refs by chance.

### The exit gate

`tests/security/test_v03_part_d_exit.py` reads design D.1–D.11 from the design itself and maps each item to its owning tests. It re-runs every owning test in a fresh process, and none may fail, be skipped or be deselected. The plan's D38 names "the Part D list" but spells out no list of its own.

It also checks:

- every client runbook records the D.11 fields;
- this section names what D38 requires.

The P §80 intent prompts are pinned as data in `tests/evaluation/intent_prompts.json`, for owner-run acceptance. P §81 is deterministic (`tests/evaluation/test_ambiguity_deterministic.py`):

- two people named Ali, two MQ groups and several matching messages each give `AMBIGUOUS_TARGET` with no mutation row;
- every write target is a ref pattern, so a name never reaches a write service.

The operations model (`formal/operations_model.py`) explores 4,728 states with no violations, and all seven planted mutations are caught. The differential walk replays 300 seeds against the real executor.

### Rulings made in Part D

Registered: R-D38. The ledger (`.superpowers/sdd/2026-09-24-comms-v0.3/progress.md`) records every ruling in full, task by task. In short:

- **D1–D5:** schema v4 and operation records.
  - `groups` maps 1:1 to destinations.
  - `CommsError` holds P §54's codes plus four of our own.
  - The request-id check is exact.
  - Mutation persistence is a core API.
  - On replay, a CREATE is resolve-only and a SET_STATE gets one same-key retry.
  - Crash seams sit at the call boundary.
- **D6–D11:**
  - P §68 actor resolution, applied literally.
  - Name resolution, where more than one match is always ambiguous.
  - P §71 bounds as constants.
  - The security epoch.
  - Live-or-local context by capability.
  - Pinned provenance per transport.
- **D12–D17:** the service layer.
  - Every step is validated before it is recorded.
  - Object refs are minted for created invites and topics.
  - Message writes are added to the admin adapters.
  - The core is split into `*_in_tx` bodies.
  - Directory renames.
  - WhatsApp account-level template and media writes.
- **D18–D25:** the catalog.
  - One ordered tuple from family modules.
  - A shared write-result schema and actor enum.
  - Group list/get reads.
  - Explicit P §74 profiles.
  - Open-world campaign sends.
  - Local directory tools.
  - Account-level results.
  - The pin.
- **D26–D29:** the transport layer.
  - Registry dispatch.
  - A clients table with the seed version in a key slot.
  - The HTTP guards in one copy.
  - The pure lease format and the privilege-free proxy.
- **D30–D31:**
  - Facades bind tool arguments to services, with the 13 tools not offered.
  - One declarative operator table, of which only client add/rotate/disable and oauth approve are wired.
- **D32–D34:**
  - The SDK's auth handlers over a Comms provider with one public client and PKCE S256, behind an owner code.
  - Remote `/mcp` validates the token on every request.
  - Three exact-path listeners.
- **D35–D37:**
  - The operations model and the walk.
  - Typed egress.
  - The smoke's move to the comms composition.

**R-D38.**

- **Name resolution.** The catalog has no name-resolution tool, because P §22–35 names none. The design D.4's "resolution tools return `AMBIGUOUS_TARGET`" is therefore met by the service resolver (`services/resolve.py`) together with ref-only write targets. The model resolves names through list reads.
- **The exit test.** It keys on design D.1–D.11.
- **The CLI.** The missing D.7 `template` group was added.
- **The smoke.** Its comms phase gained `verify --all`.

### Follow-ups

- **The daemon (blocks every P §88 row).** It must open `comms.db` and the secret store, call `build_comms_adapters` and `build_comms_runtime`, and serve the three listeners.
- **Operator commands.** These parse, but refuse with a fixed message until a handler exists:
  - transport, credential, keys, audit, retention, backup and cutover;
  - client and oauth are wired.
- **The 13 tools not offered.** They need their adapter operations and services.
- **Egress not swept:** the stdio proxy frames, backup sidecars and webhook responses.
- **`cml1` replay.** A lease replayed within its 60 s window is not tracked, so the nonce is not remembered.
- **Carried from Part C and not addressed here:**
  - the live update-stream switch (`receive_updates`);
  - the WhatsVault archive binding;
  - per-recipient template language.
- **Group photos.** `chat.set_photo` is served as `comms_group_info_set_photo`, but it takes a `med_` ref that only the (not offered) media upload would mint.

### Waiting on the owner

- **R-A20:** the `.claude/settings.json` always-ask rules.
- **P §88 acceptance:** D39, after the owner-approved merge.
- **Out-of-repo runbook steps.**

### Tag

`comms-v0.3-part-d` is an annotated local tag, **not pushed**: the tag object and commit are recorded by the commit that follows the tag.

No production claim.
