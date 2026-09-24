# Comms 5b-2 verification — WhatsVault imported intact

Design: `comms-consolidation-design.md` rev 2 §3. Plan: `2026-09-24-comms-5b2-whatsvault-import.md`
(executed first as a throwaway spike; the plan was written from the spike's measurements).

## Proof

| Claim | Evidence |
|---|---|
| Exact source commit, from GitHub | `git fetch … b6fd51ac83d91018cb2d7fe37f0bce669c7317aa` → `FETCH_HEAD` equal (checked in the command) |
| Exact tree, untouched | `HEAD:transports/whatsapp` = `abdbcdc775ff62c4eab6d0527e7081128c133ba7` = source tree; `test_the_subtree_is_the_measured_tree` (also requires a clean working tree under the prefix) |
| Full history, no squash | source commit is an ancestor of HEAD; history grew by WhatsVault's commits (`test_the_source_commit_and_its_history_are_present`) |
| Importable as `whatsvault` from the subtree | `test_whatsvault_imports_from_the_subtree` |
| Native substrate recorded and pinned | `sqlcipher3` 0.6.2, `cp312-cp312-macosx_11_0_arm64` wheel, SQLCipher `4.12.0 community`, Python 3.12.2 arm64 (`test_the_native_substrate_is_recorded`; `docs/provenance/whatsvault.md`) |
| Lock additions only | 12 packages added, `git diff uv.lock` 0 removed lines; `pip-audit` clean (`docs/verification/dependencies.md`) |
| Transport isolation now live | `test_whatsapp_never_imports_telegram` moved from skip to pass |

## Gate (branch head)

| Check | Result |
|---|---|
| `uv sync --locked`, contracts | exit 0; OK (23 files) |
| Telegram pytest | 1519 passed, 10 skipped (was 1514/11: +4 provenance tests, isolation guard skip→pass) |
| smoke | 60/60 |
| formal | 624 states, 18 assertions |
| ruff / format / mypy | clean (232 files; 101 source files) |
| build | wheel contains `whatsvault/` (80 entries) |
| WhatsVault pytest (its own config, shared venv) | **539 passed** — identical to its source baseline |

## Rulings and deviations

- `git subtree` not installed → the equivalent subtree merge (`merge -s ours` + `read-tree --prefix`); identical history and tree.
- Abbreviated SHA refused by the remote → full SHA everywhere.
- Design §3.3 assumed Homebrew SQLCipher → measured a hash-pinned wheel bundling SQLCipher; recorded as a deviation (stronger provenance).
- WhatsVault `apps/` and its console scripts are not installed from the root project in 5b-2 (top-level `apps`/`tests` would collide with Telegram's `tests`); its tests reach `apps` through their own `pythonpath`.
- The spike's Telegram count (1515) lacked the plan's 4 provenance tests; the plan's expected 1519 was corrected before execution and matched.

## Next

Design §3.4 seam consolidation (common-contract procedure, one seam per commit) or 5b-3 (comms spec v0.2).
