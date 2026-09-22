# Phase 3 verification — disclosure, budgets, proofs and audit integrity

**Status:** Plans 3a, 3b and 3c complete. Phase 3 is built and qualified
against the fake adapter, exactly as Phase 2 was.

**NOT production.** No Telegram login, real account data, session file,
committed disclosure receipt, exposure-ledger row, audit event, chain anchor,
installed service account, loaded LaunchAgent or tunnel ingress is claimed.
The coordinator exists and commits real receipts, ledger rows and audit
events — **against a fake adapter only**. No tool slice calls it yet; that is
Phase 4. The nine sensitive tools still return `POLICY_UNCONFIGURED`.

## Reproducibility

```bash
uv sync --locked
uv run python scripts/extract_contracts.py --check    # 23 files OK
uv run pytest -q                                      # 626 passed, 7 skipped
uv run pytest tests/formal -q -s                      # 624 states, 18 assertions
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

## What Plan 3b delivered

| Module | Proves |
|---|---|
| `disclosure/budget.py` | keyed subject digests so no ref reaches the ledger; rolling windows; the two dimensions; projected tiers; the six-component reservation binding; `actual ≤ reserved`; ledger rows written inside a caller-owned transaction |
| `tests/integration/test_budget_concurrency.py` | two interleaved coroutines cannot both take the last capacity |
| `tests/security/test_budget_bypass.py` | retrying does not accumulate past the ceiling; cycling projects is caught by the client-global ceiling; a second client has its own budget |

## Gate ledger

Every row is **PARTIAL**. Phase 3a moves parts of Gate O and nothing else.

| Gate | State | What passes | What is missing |
|---|---|---|---|
| **O** | PARTIAL (much improved) | a receipt signature verifies against the declared public key and fails on tampering; an independent verifier checks a live payload in a process where `sqlite3.connect` and `socket.socket` both raise; historical keys stay exportable after retirement; provenance is correct for single- and multi-origin records | a receipt, its ledger rows and exactly one audit event now commit in a single transaction, the anchor refreshes before any byte leaves, the content-leak sweep is green with a control proving it can fail, and a rotated client credential does not break an old receipt. What is still missing: no *real* sensitive success exists, because no tool slice calls the coordinator — Phase 4 |
| **P** | PARTIAL | measurement exists in exactly one module and the per-project/global split reconciles; egress can only reduce and the most restrictive profile wins; soft and hard thresholds behave exactly as §23C.1 specifies against the projected figure; concurrent reservations cannot both take the last capacity; retries and project cycling are both caught; `actual ≤ reserved` fails closed | no tool consults the ledger, so nothing is enforced end to end; the emergency lock is not yet wired to reservations; measurement identity across prompt, reservation, ledger and receipt cannot be asserted until a coordinator exists. Plan 3c |
| **Q** | PARTIAL | the bounded model explores all 624 reachable states and every one of the 18 assertions holds; a second test proves no assertion is unreachable; `formal/README.md` and `SECURITY-MANIFEST.json` record the checker, bounds and configuration | policy explain/simulate/diff and the overlap/drift inspector are not built; the adversarial benchmark runs against a fake adapter only |
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

3. **The budget ledger is bound to one connection and therefore one thread.**
   `sqlite3` connections are thread-bound, so the planned thread-based
   concurrency test failed inside `get_setting` before reaching any budget
   logic. The daemon is single-process asyncio, so the suite races coroutines
   instead — the real concurrency model — and `BudgetLedger` documents that a
   future thread pool needs a ledger per thread.

4. **A stray `# noqa: TRY004` was removed** from `measure.py`: ruff correctly
   reported it unused, because `MeasurementError` is not the `TypeError` case
   the directive exists for.

5. **The bidi egress test builds its control character with `chr(0x202E)`**
   rather than a source literal. Ruff's `PLE2502` trojan-source rule rejects a
   bidi control in source and is right to; the test needs the character to
   reach the transformer, not to sit in the file.

## What this build still cannot do

It cannot disclose anything. There is no coordinator, so no path exists from a
tool call to a signed receipt; the nine sensitive tools still return
`POLICY_UNCONFIGURED`. `doctor --production` continues to fail by design.


## What Plan 3c delivered

| Module | Proves |
|---|---|
| `audit/chain.py` | MAC-linked events with an explicit epoch-bound genesis; 40 appends under four-way contention stay contiguous and verify; a caller-owned transaction is required, so §23A.3's single commit is possible |
| `audit/anchor.py` | frozen JSON shape, domain-separated `anchor_mac`, write/fsync/rename/fsync, and fail-closed rejection of symlink, wrong owner, wrong mode, unknown version or bad MAC |
| `coordinator.py` | the twelve steps, the append guard across 11 and 12, and an outcome that is a payload with its receipt or a refusal — never a third shape |
| `verify.py` | a persisted receipt rebuilds byte-identically and verifies, including after a client credential rotation |
| `formal/model.py` | 18 assertions over 624 reachable states, none unreachable |

## Adversarial extraction benchmark

First run, sealed. A greedy scripted client against the fake adapter:

```text
corpus=synthetic-uniform-v1  window=30min  hours=24
released=1152  refused=48  records=23040  receipts=1152
```

480 records per 30-minute window, and the bound that actually bit was the
**per-project** ceiling of 500, not the client-global 1500 — a single-project
client never reaches the global limit. Every release produced a receipt: 1152
releases, 1152 receipts, nothing escaped unaccounted.

**This figure is benchmark-observed, not a property of the world.** It is what
one scripted client extracted from a fake adapter under a named synthetic
corpus and the default budget configuration, all three recorded beside the
number. It is not a measurement of real private content, and it is a lower
bound on what a cleverer client might achieve even in this configuration.

The honest corollary is visible in the run itself: the client was refused in
every window and then continued in the next one. Budgets limit burst rate,
not lifetime exposure.
