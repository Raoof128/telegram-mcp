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

`comms-v0.3-part-a` is an annotated local tag on the commit that adds this section. It is **not pushed**, and nothing is ever rebased across it. The tag object SHA is recorded in the follow-up commit.

No production claim.
