# Phase 3 verification — disclosure machinery (Plan 3a)

**Status:** Plan 3a complete. Plans 3b (exposure accounting) and 3c (audit
chain, anchor, coordinator) have **not** started.

**NOT production.** No Telegram login, real account data, session file,
committed disclosure receipt, exposure-ledger row, audit event, chain anchor,
installed service account, loaded LaunchAgent or tunnel ingress is claimed.
Plan 3a builds the deterministic machinery only: **nothing is wired into a
tool**, so no disclosure has ever been committed by this build.

## Reproducibility

```bash
uv sync --locked
uv run python scripts/extract_contracts.py --check    # 23 files OK
uv run pytest -q                                      # 536 passed, 7 skipped
uv run python scripts/e2e_smoke.py                    # 41 passed, 0 failed
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src/telegram_mcp                          # 49 source files
```

## What Plan 3a delivered

| Module | Proves |
|---|---|
| `disclosure/measure.py` | the single measurement authority; record elements read off the frozen contracts, not guessed |
| `disclosure/egress.py` | `metadata_only`/`excerpt`/`full_text`, most-restrictive intersection, message text never sanitised |
| `disclosure/provenance.py` | ordered emitted-record vector, order-sensitive digest that never hashes text |
| `disclosure/coverage.py` | the §23D object, the closed six `partial_reasons`, the invariant chain JSON Schema cannot express |
| `disclosure/receipts.py` | the twenty-one Appendix-K fields, Ed25519 over JCS bytes, tamper rejection |
| `disclosure/keys.py` | the public verification-key registry with activation and retirement |
| `tools/status.py` | the zero-seed key retired |

## Gate ledger

Every row is **PARTIAL**. Phase 3a moves parts of Gate O and nothing else.

| Gate | State | What passes | What is missing |
|---|---|---|---|
| **O** | PARTIAL | a receipt signature verifies against the declared public key and fails on tampering; an independent verifier checks a live payload in a process where `sqlite3.connect` and `socket.socket` both raise; historical keys stay exportable after retirement; provenance is correct for single- and multi-origin records | no receipt is ever *committed*: no `disclosure_receipts` row, no audit chain, no checkpoints, no anchor. "Exactly one receipt per sensitive success" cannot be claimed because no sensitive success exists. The content-leak sweep across all four stores is Plan 3c |
| **P** | PARTIAL | measurement exists in exactly one module, and the per-project/global split reconciles (`sum(project_bytes) + container_bytes == bytes_disclosed`); egress can only reduce; the most restrictive profile wins for shared objects | no budgets are enforced, no reservations exist, no ledger row is written. Plan 3b |
| **Q** | PARTIAL | nothing | the bounded formal model is Plan 3c Task 9; `formal/README.md` and `SECURITY-MANIFEST.json` do not exist |
| **A–N, R** | unchanged | see `phase-2a.md` and `phase-2b.md` | unchanged |

## Deviations recorded

1. **`telegram_status` now requires a disclosure key.** The frozen contract
   types `disclosure_proof_key_id` as `{"type": "string", "minLength": 1}` and
   `disclosure_proof_public_key` as `{"pattern": "^[A-Za-z0-9_-]{43}$"}`.
   Neither admits `null`, so there is no "no key provisioned" state to report
   and `make_status` takes the pair as a required argument. **No schema was
   widened.** The synthetic build mints one ephemeral per-process Ed25519 key,
   whose private half never leaves the process and signs nothing.

2. **`FILE_BACKED_KEYS` grew from four rows to seven.** `doctor`'s
   `keys.inventory` and `telegram-mcp keys list` both read that set, so a store
   provisioned for Phase 2 only now reports the three signing rows as missing.
   That is the honest answer, and `keys provision` was changed to create all
   seven so the two verbs cannot disagree.

3. **A stray `# noqa: TRY004` was removed** from `measure.py`: ruff correctly
   reported it unused, because `MeasurementError` is not the `TypeError` case
   the directive exists for.

4. **The bidi egress test builds its control character with `chr(0x202E)`**
   rather than a source literal. Ruff's `PLE2502` trojan-source rule rejects a
   bidi control in source and is right to; the test needs the character to
   reach the transformer, not to sit in the file.

## What this build still cannot do

It cannot disclose anything. There is no coordinator, so no path exists from a
tool call to a signed receipt; the nine sensitive tools still return
`POLICY_UNCONFIGURED`. `doctor --production` continues to fail by design.
