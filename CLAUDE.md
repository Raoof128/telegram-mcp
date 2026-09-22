# Telegram MCP gateway — working agreement

Read-only Telegram MCP gateway for one owner, with project isolation, human
consent and accountable disclosure. The frozen product specification is
`telegram-mcp-v0.1.10-final-engineering-spec.md`
(SHA-256 `36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a`);
it controls wherever anything else is silent.

**Where the work stands:** Phase 1 (synthetic protocol foundation) and Phase 2
(privileged runtime, identity, consent, authority, storage, IPC, and the Swift
consent agent) are complete and qualified. Phase 3 is under way: Plan
3a (measurement, egress, provenance, coverage, receipts, verification keys) is
complete; Plans 3b (exposure accounting) and 3c (audit chain, anchor,
coordinator) have not started, so nothing is wired into a tool and no
disclosure has ever been committed. Nothing here has ever touched Telegram.

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
uv run pytest -q                                  # 536 passed, 7 skipped
uv run python scripts/e2e_smoke.py                # 41 checks, end to end
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src/telegram_mcp
uv build
```

Host-touching tests are opt-in and never run by default:

```bash
uv run pytest -q --run-platform-gated             # real pairing, Touch ID, host probes
bash scripts/package_agent.sh                     # rebuild the consent-agent bundle
```

The smoke is not the suite. The suite proves each unit; the smoke drives the
shipped artifacts — the demo server over a real TCP socket, the schema on
disk, real Unix sockets, the installed CLI, the real agent against the real
broker — and prints one ledger. Both must pass before any claim of done.

## Map

| Area | Where |
|---|---|
| Ten frozen tool contracts | `src/telegram_mcp/contracts/*.json`, loaded by `contract.py` |
| MCP surface | `server.py`, `dispatch.py`, `validation.py`, `results.py` |
| Runtime lifecycle, lock, launcher | `runtime/` |
| Keys, pairing pins | `keys/` |
| Consent challenge wire, broker, gate | `consent/` |
| Authority, refs, cursors, epochs | `authority/` |
| Schema, migrations, settings | `storage/` |
| Admin socket, leases, RV-1, tunnel pins | `ipc/` |
| Disclosure machinery (Phase 3) | `disclosure/` |
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
