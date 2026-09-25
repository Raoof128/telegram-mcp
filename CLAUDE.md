# Telegram MCP gateway — working agreement

Telegram and WhatsApp comms gateway for one owner (read-only through comms
v0.2; typed writes under `owner_full_admin` since v0.3), with project isolation and
accountable disclosure. Since comms spec v0.2 (`docs/comms-spec-v0.2.md`) an
owner command is the authorization: there is no consent ceremony and no Touch
ID anywhere. The frozen product specification is
`telegram-mcp-v0.1.10-final-engineering-spec.md`
(SHA-256 `36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a`);
it controls wherever anything else is silent.

**Where the work stands:** Phases 1–3 are complete and qualified (synthetic
protocol, privileged runtime and consent, disclosure receipts, budgets, audit
chain and anchor). **Phase 4a is complete:** `telegram_list_projects` and
`telegram_resolve_project` succeed for real through the authenticated
loopback ingress (`runtime/ingress.py`), a daemon-delivered consent prompt
(`consent/prompter.py`), live SQLite authority and the Phase-3 coordinator,
with receipts that verify persisted and offline. `runtime/composition.py` is
the only wiring point. **Phase 4b is on `main`** (merged at `cd1434e`):
`telegram-mcp daemon`, Touch-ID-approved admin commands bound to their exact
request, raw reviewed Telegram login, and `list_chats`, `resolve_peer`,
`get_messages` and `get_unread` against a fake transport. The adapter owns
the MTProto boundary (`telegram/telethon_adapter.py`: one send per request,
per-operation allowlist and work budget; Telethon's login helpers are never
used). **Phase 4c is on `main`** (merged at `0a11bb9`): `get_context` and both
searches, so all nine sensitive tools are served on the fake transport. The
searches are per peer only, with a pure continuation engine
(`telegram/search.py`) and signed §23D coverage whose counters are measured.
`bounds.PageBudget` holds every read to both §13.2 caps (bytes and 32,000
codepoints). A page ends only on Telegram's own signal (`_page_end`).
Evidence: `docs/verification/phase-4.md`, `telegram-rpc-review.md`.
**Phase 5a is on `main`** (merged at `f3047da`): the operator surface runs on
one transaction runner (`ipc/handlers/_wrapper.py`; audited admin events share
the disclosure append guard and anchor), one policy evaluator with traces
(`authority/policy.py`; retrieval decides chat class through `admit_live`),
metadata-effective access and `policy explain/simulate/diff` (semantic
simulate-equals-commit over every simulatable command; bound `tps_` stages).
Evidence: `docs/verification/phase-5.md`. 5b (revoke, rotation, retention,
recovery, chain epochs) is designed, not built.
**Comms consolidation 5b-1** (branch `comms-5b1`): the Telegram implementation was relocated
mechanically to `comms.transports.telegram` (AST-equivalent to its pre-move tree;
design `docs/superpowers/specs/2026-09-24-comms-consolidation-design.md`). The
`telegram-mcp` and `comms` CLIs both work, and every frozen wire, logger name and
on-host identifier is unchanged. Evidence: `docs/verification/comms-5b1.md`.
**Comms 5b-2** (branch `comms-5b2`): WhatsVault is imported intact at `b6fd51a` under
`transports/whatsapp/` with full history (tree-hash proven; `docs/provenance/whatsvault.md`),
importable as `whatsvault` in the one Python 3.12 environment. Its suite runs under its own
pytest config (command above). Nothing in the subtree is edited until design §3.4 seams begin.
**Comms 5b-3** (branch `comms-5b3`): owner-direct authority. The consent
subsystem is deleted; receipts are v2 `owner_direct` (v1 kept byte-identical,
verified by `proof_version`); admin authority is peer credentials; retired
identifiers are tombstoned. Evidence: `docs/verification/comms-5b3.md`. The
history above describes each phase as it shipped; where it mentions consent,
prompts or Touch ID, v0.2 has since retired them.
**Comms 5b-4** (branch `comms-5b4`): the campaign core in `src/comms/core/`, on
fake transports only. It has an encrypted `comms.db` (SQLCipher, fail-closed), a
directory with shared delivery identities, campaigns frozen into immutable
generations of deduplicated jobs, one reducer, an engine whose only exception
boundary is `deliver`, cancel/retry/resolution/provider updates, a scheduling time
gate, and recovery that never resends. It is proved by a bounded model
(`formal/campaign_model.py`) and a differential walk. No CLI or real adapter yet
(5d/5e). Evidence: `docs/verification/comms-5b4.md`.
**Comms v0.3** (branch `comms-v0.3`; local tags `comms-v0.3-part-{a,b,c,d}`, none pushed):
spec `docs/comms-spec-v0.3.md` (D5 `owner_full_admin`: typed writes; host permission UX is the
only prompt layer). Part A sealed the legacy chain into the comms chain; B added durability,
audit, keys, retention, recovery and backup; C built four adapters (`telegram_bot`,
`telegram_user`, `whatsapp_cloud`, `whatsapp_webhooks`); D built the service layer
(`src/comms/services/`), the 109-tool catalog (`src/comms/mcp/`, digest pinned), `comms mcp`
(stdio proxy with `cml1` leases; HTTP `/mcp`, `cml1` locally and OAuth remotely), the generated
CLI, three isolated listeners, and the smoke over the real comms composition. Not yet: the
daemon serving `comms.db` (blocks every P §88 owner acceptance row), 13 tools `NOT_OFFERED`,
most operator commands. Evidence: `docs/verification/comms-v0.3.md`; rulings
`docs/verification/comms-v0.3-rulings.md`.
Nothing here has ever touched Telegram; the Test DC harness is owner-run.

## Non-negotiables

- **No production claim** until Gates A–R pass for the exact artifact. Current
  gate status lives in `docs/verification/phase-2a.md` and `phase-2b.md`; every
  gate there is PARTIAL, and each says what is missing.
- **Never commit** Telegram credentials, session files, keys or private
  material, and never log them. `.gitignore` carries the Appendix B baseline.
- **Fail closed.** A check that cannot be performed reports that it was
  skipped; it never reports success. `doctor --production` failing in this
  phase is correct behaviour, not a bug.
- **No test-only flags in production paths.** Headless testing goes through
  separate `selftest-` subcommands or injected seams (such as the
  coordinator's `crash_at`), never a branch inside a real path.
- **One copy of each shared rule.** The strict JSON decoder, the JCS encoder,
  the frame codec and the opaque-ref minter each exist once and are imported;
  a second copy is the defect.
- **Read `AGENT.md` and `CHANGELOG.md` before editing**, and append a dated
  `**Raouf:**` entry to both afterwards with Scope, Summary, Files changed,
  Verification and Follow-ups. This is the project's audit trail.

## Verify

```bash
uv sync --locked
uv run python scripts/extract_contracts.py --check
uv run pytest -q                                  # 5023 passed, 4 skipped
uv run python scripts/e2e_smoke.py                # 74 checks, end to end
uv run pytest tests/formal -q -s                  # 57 passed: 544 states/22 assertions; campaign 96,528/11; operations 4,728
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src/comms src/telegram_mcp
uv build
(cd transports/whatsapp && ../../.venv/bin/python -m pytest -p no:randomly -p no:cacheprovider)  # WhatsVault: 450 passed
```

Host-touching tests are opt-in and never run by default:

```bash
uv run pytest -q --run-platform-gated             # service accounts, host probes
uv run pytest tests/telegram/test_testdc.py --run-telegram-testdc -q -s   # Test DC (TG_TESTDC_* + Keychain)
```

The smoke is not the suite. The suite proves each unit; the smoke drives the
shipped artifacts — the demo server over a real TCP socket, the schema on
disk, real Unix sockets, the installed CLI, the real ingress into the real
coordinator — and prints one ledger. Both must pass before any claim of done.

## Map

Telegram code lives in `src/comms/transports/telegram/` (paths below are relative
to it unless they start at the repo root). `src/comms/core/` is the transport-neutral
campaign core, which never imports a transport; `src/telegram_mcp/` is only the legacy
CLI forwarder.

| Area | Where |
|---|---|
| Ten frozen tool contracts | `contracts/*.json`, loaded by `contract.py` |
| MCP surface | `server.py`, `dispatch.py`, `validation.py`, `results.py` |
| Runtime lifecycle, lock, launcher | `runtime/` |
| Keys (consent keys retired, not erased) | `keys/` |
| Authority, refs, cursors, epochs | `authority/` |
| Schema, migrations, settings | `storage/` |
| Admin socket (peer-credential authority), leases, tunnel pins | `ipc/` |
| Disclosure coordinator, budgets, receipts, audit chain | `disclosure/` |
| Bounded formal model | `formal/`, `SECURITY-MANIFEST.json` |
| Telegram adapter (only Telethon importer), reads, daemon | `telegram/`, `runtime/daemon.py` |
| Health checks | `doctor.py` |
| Plans and design | `docs/superpowers/` |
| Campaign core: `comms.db`, directory, freeze, reducer, engine, recovery (repo root) | `src/comms/core/`, `formal/campaign_model.py` |
| Services: every read and write (repo root) | `src/comms/services/` |
| MCP catalog, dispatch, HTTP, stdio proxy, OAuth (repo root) | `src/comms/mcp/` |
| Comms composition, facades, listeners (repo root) | `src/comms/runtime/` |
| `comms` CLI (repo root) | `src/comms/cli.py`, `src/comms/cli_commands/` |
| Evidence, gates, deviations | `docs/verification/` |

## Frozen wires — changing these breaks both halves

- **TG-JCS-v1**: sorted ASCII keys, `,`/`:` separators, literal UTF-8, escape
  only `"`/`\`/U+0000–U+001F. Floats, lone surrogates and non-ASCII keys are
  fatal. One copy: `src/comms/core/canonical.py`. Byte-equality vectors:
  `tests/fixtures/canonical/jcs_vectors.json`.
- **Receipt proofs** `tg-mcp-disclosure/v1` (historical, consent fields) and
  `tg-mcp-disclosure/v2` (owner-direct, `soft_threshold_exceeded`), selected by
  the stored `proof_version` — `docs/comms-spec-v0.2.md`.
- **Call binding**: `SHA256("comms-call-binding/v1\0" || JCS({args, nonce, tool}))`.
- **Campaign-core domains** (`src/comms/core/domains.py`, the only home):
  `comms-delivery-idem/v1`, `comms-campaign-snapshot/v1`, `comms-campaign-target/v1`,
  `comms-campaign-recipients/v1`. Every `comms-*` literal is pinned by
  `tests/security/test_comms_wire_frozen.py`.
- **`tgml1` bearer leases** — spec §9.7.1, exactly.
- **Key ids** are `<kind>:sha256:<64 hex>`, recomputed on load, never stored.

**Tombstoned** (comms spec v0.2): the consent challenge, `ApprovalEnvelope`,
the display digest, RV-1, the `PROMPT` / `APPROVAL` / `DENIAL` frames, the
admin-approval domains, `tgu_`, `PRESENCE_REQUIRED`, and the consent keys.
They never come back under a new meaning; `tests/security/test_tombstones.py`
and `test_comms_protocol_frozen.py` pin that.

## Conventions worth knowing

- Errors carry fixed, non-enumerating strings; the code goes in
  `structuredContent`, never in the human-readable text.
- `ruff` runs with a broad default rule set. Uniform `ValueError` on
  validation is the house style, with a `# noqa: TRY004 -- …` and a reason.
- AF_UNIX paths cap near 104 bytes, so socket tests `chdir` into `tmp_path`
  and use a relative directory.

## Host state (this Mac)

A real Secure Enclave approval key and a transport key are still paired to the
old certificate-signed consent bundle. Comms v0.2 retired both; deleting them
and uninstalling the bundle is an owner-approved runbook step
(`docs/comms-spec-v0.2.md`), never automated. No daemon pin is present,
because no runtime has been provisioned. Service accounts, `/private/var/run/telegram-mcp` and the
tunnel certificates have **not** been installed — both installers are
idempotent and print their plan with `--dry-run`.
