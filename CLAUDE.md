# Telegram MCP gateway — working agreement

Read-only Telegram MCP gateway for one owner, with project isolation, human
consent and accountable disclosure. The frozen product specification is
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
  separate `selftest-` subcommands or injected seams, never a branch inside a
  real path. Tests assert this against the agent source.
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
uv run pytest -q                                  # 1519 passed, 10 skipped
uv run python scripts/e2e_smoke.py                # 60 checks, end to end
uv run pytest tests/formal -q -s                  # 624 states, 18 assertions
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src/comms src/telegram_mcp
uv build
(cd transports/whatsapp && ../../.venv/bin/python -m pytest -p no:randomly -p no:cacheprovider)  # WhatsVault: 539 passed
```

Host-touching tests are opt-in and never run by default:

```bash
uv run pytest -q --run-platform-gated             # real pairing, Touch ID, host probes
uv run pytest tests/telegram/test_testdc.py --run-telegram-testdc -q -s   # Test DC (TG_TESTDC_* + Keychain)
bash scripts/package_agent.sh                     # rebuild the consent-agent bundle
```

The smoke is not the suite. The suite proves each unit; the smoke drives the
shipped artifacts — the demo server over a real TCP socket, the schema on
disk, real Unix sockets, the installed CLI, the real agent against the real
broker — and prints one ledger. Both must pass before any claim of done.

## Map

Telegram code lives in `src/comms/transports/telegram/` (paths below are relative
to it unless they start at the repo root). `src/comms/core/` is empty and guarded
until 5b-2; `src/telegram_mcp/` is only the legacy CLI forwarder.

| Area | Where |
|---|---|
| Ten frozen tool contracts | `contracts/*.json`, loaded by `contract.py` |
| MCP surface | `server.py`, `dispatch.py`, `validation.py`, `results.py` |
| Runtime lifecycle, lock, launcher | `runtime/` |
| Keys, pairing pins | `keys/` |
| Consent challenge wire, broker, gate | `consent/` |
| Authority, refs, cursors, epochs | `authority/` |
| Schema, migrations, settings | `storage/` |
| Admin socket, leases, RV-1, tunnel pins | `ipc/` |
| Disclosure coordinator, budgets, receipts, audit chain | `disclosure/` |
| Bounded formal model | `formal/`, `SECURITY-MANIFEST.json` |
| Telegram adapter (only Telethon importer), reads, daemon | `telegram/`, `runtime/daemon.py` |
| Health checks | `doctor.py` |
| Swift consent agent | `agent/consent-agent.swift` |
| Plans and design | `docs/superpowers/` |
| Evidence, gates, deviations | `docs/verification/` |

## Frozen wires — changing these breaks both halves

- **TG-JCS-v1**: sorted ASCII keys, `,`/`:` separators, literal UTF-8, escape
  only `"`/`\`/U+0000–U+001F. Floats, lone surrogates and non-ASCII keys are
  fatal. Byte-equality vectors: `tests/fixtures/consent/jcs_vectors.json`.
- **Challenge / ApprovalEnvelope / display digest** — spec §9.8 and the
  Phase-2 design §4. `display_digest` is
  `SHA256("telegram-mcp-display-v1" || JCS(payload))`.
- **RV-1 rendezvous**: HELLO → CHALLENGE → READY over a transcript digest,
  5-second deadline, agent authenticated by its *transport* key.
- **Prompt frames**: `PROMPT` / `APPROVAL` / `DENIAL`, documented in
  `tests/agent/stub_broker.py`.
- **`tgml1` bearer leases** — spec §9.7.1, exactly.
- **Key ids** are `<kind>:sha256:<64 hex>`, recomputed on load, never stored.

If a wire changes, the Phase-2J join gate
(`tests/integration/test_join_gate.py`) is the thing that proves both halves
still agree. It drives the real broker against the real packaged agent.

## Conventions worth knowing

- Errors carry fixed, non-enumerating strings; the code goes in
  `structuredContent`, never in the human-readable text.
- `ruff` runs with a broad default rule set. Uniform `ValueError` on
  validation is the house style, with a `# noqa: TRY004 -- …` and a reason.
- AF_UNIX paths cap near 104 bytes, so socket tests `chdir` into `tmp_path`
  and use a relative directory.
- The consent agent is built and packaged by a fixture; tests point at the
  bundle binary, never a loose build. The default bundle is ad-hoc signed,
  because pairing must refuse it.

## Host state (this Mac)

A real Secure Enclave approval key and a transport key are paired to the
certificate-signed bundle; no daemon pin is present, because no runtime has
been provisioned. Service accounts, `/private/var/run/telegram-mcp` and the
tunnel certificates have **not** been installed — both installers are
idempotent and print their plan with `--dry-run`.
