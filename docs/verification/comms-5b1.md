# Comms 5b-1 verification: mechanical relocation

Design: [`comms-consolidation-design.md`](../superpowers/specs/2026-09-24-comms-consolidation-design.md) rev 2, §2.
Plan: [`2026-09-24-comms-5b1-rename-restructure.md`](../superpowers/plans/2026-09-24-comms-5b1-rename-restructure.md) rev 2. The whole plan was dry-run in a throwaway worktree first, which found 8 defects before execution.

**No semantic change was made.** The proof is AST equivalence, not review.

## Commits (branch `comms-5b1`)

| Commit | What |
|---|---|
| migration tools | `scripts/migration/{move_map,rewrite,ast_equivalence}.py`, 9 tool tests, and the baseline protocol fixture (29 constants), captured before the move |
| `27af242` | the contract tests, RED by design. **This is BASE for the equivalence proof.** |
| `4d8c671` | the move: `git mv src/telegram_mcp src/comms/transports/telegram`, the rewriter (177 files, then 0 on re-run), import sort and format, and four path edits |

## The proof

```text
$ PYTHONPATH=scripts uv run python -m migration.ast_equivalence 27af242
96 modules and 23 data files checked; ok=True
```

- **Every Python module:** the AST at BASE, with imports rewritten through the move map and the two resource anchors (`server.py`, `contract.py`: `resources.files("telegram_mcp")` → `"comms.transports.telegram"`), is identical to the moved module's AST. The comparison is **order-sensitive**, so it also proves that `ruff`'s import sorting and formatting only re-wrapped lines.
- **Every data file** (`contracts/*.json` and the others) has the same git blob hash.
- The move added only the three `comms` package markers.
- **Protocol constants:** the 29 at BASE are the same multiset after the move (`test_comms_protocol_frozen.py`).
- **`uv.lock` is unchanged**, and `uv sync --locked` passes.

## Frozen identifiers kept (the complete `KEEP_LITERALS`)

| Literal | Role |
|---|---|
| `telegram_mcp` (`cli.py`, `observability/logging.py`) | root logger name |
| `telegram_mcp.admin` (`ipc/admin.py`, `ipc/handlers/_wrapper.py`) | logger name |
| `telegram_mcp.audit` (`audit_seam.py`) | logger name |
| `telegram_mcp.sensitive`, `.daemon`, `.rendezvous` | logger names |
| `telegram_mcp_principal` (`runtime/ingress.py`) | ASGI scope key |
| `telegram_mcp_operation` (`telegram/telethon_adapter.py`) | context-variable name |
| test-side: `telegram_mcp.audit`, `telegram_mcp`, `telegram_mcp.test.filter.probe`, `telegram_mcp_rpc_phase` | logger names and a context key referenced by tests |

`test_every_remaining_telegram_mcp_literal_is_a_frozen_identifier` asserts that the set of remaining literals equals this table exactly.

## Compatibility surface (design §2.3), each item measured

| Guarantee | Test |
|---|---|
| The `telegram-mcp` console script works | `test_console_scripts_resolve_to_comms` |
| The `comms` console script works (same `main`) | same test |
| `python -m telegram_mcp.cli` works | `test_python_dash_m_legacy_cli_still_works` |
| Contracts load from the moved package (10 tools) | `test_contracts_load_from_the_moved_package` |
| The legacy package is only a two-file forwarder | `test_the_legacy_package_is_only_a_forwarder` |
| Protocol and host identity are unchanged | protocol fixture plus `KEEP_LITERALS`; no on-host path, service or bundle string is in any rewrite rule |

## Layering (design rev 2 §2.2), permanent from now on

The permanent layering guards are in `tests/security/test_comms_layering.py`:
- `comms.core` has no definitions;
- no static or dynamic import, and no string, reaches a transport;
- the dynamic-import guard is proven non-vacuous by a planted violation;
- the Telegram transport never imports `whatsvault`, and that guard asserts the directory exists, so it cannot pass vacuously;
- the WhatsApp→Telegram guard is skipped with the reason "arrives in 5b-2".

## Gate at the move commit

| Check | Result |
|---|---|
| `uv sync --locked` | exit 0 |
| contracts check | OK (23 files) |
| pytest | 1514 passed, 11 skipped (1493 + 9 tool tests + 12 contract tests; the new skip is the 5b-2 guard) |
| smoke | 60/60 |
| formal | 624 states, 18 assertions |
| ruff, format | clean (231 files) |
| mypy `src/comms src/telegram_mcp` | clean (101 source files) |
| build | `telegram_mcp-0.1.10` sdist and wheel. The distribution name is unchanged by design; it changes with the out-of-repo rename (design §5). |

## Rulings during execution

- The planted-violation test used `__import__(__name__)`, which works only because `tests/security` is not a package. It now uses `sys.modules[__name__]`.
- The Telegram-isolation guard passed vacuously before the move (empty walk). It now asserts that the directory exists.
- The leftover-literal scan omitted `test_comms_entry_points.py`, which names `telegram_mcp.cli` on purpose. It is excluded like the rewriter's `POST_MOVE_FILES`.
  - **This is the most instructive ruling.** The dry run masked it, because its early crashed rewriter passes had already rewritten that test, so the dry run never exercised the legacy path. The real run did.
