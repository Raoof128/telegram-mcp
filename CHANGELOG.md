# Changelog

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** Telegram MCP planning.
- **Summary:** Read the V0.1.10 spec and prepared a release roadmap plus a detailed synthetic-foundation plan, as selected by the user.
- **Files changed:** `AGENT.md`, this file, and the two dated planning documents under `docs/superpowers/plans/`.
- **Verification:** Reviewed the full spec across architecture/contract/disclosure/release workstreams; checked JSON syntax and demonstrated the SQLite NULL and nested-schema reference defects. Planning documents received link, code-fence and coverage checks.
- **Follow-ups:** Review the plans and select an execution method. No implementation, dependencies, Git initialization, host provisioning or Telegram access performed.

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** Telegram MCP Phase-1 Task 1 foundation.
- **Summary:** Initialized local Git, established locked package (`mcp==2.2.0`, `Telethon==1.45.0`, `hatchling==1.32.4`) with audit-corrected floors and Appendix B `.gitignore`.
- **Files changed:** `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `src/telegram_mcp/__init__.py`, `tests/unit/test_package.py`, `docs/verification/dependencies.md`, `AGENT.md`, this file.
- **Verification:** `uv sync --locked` (67 packages); mcp 2.2.0 / telethon 1.45.0 confirmed; `pytest tests/unit/test_package.py -q` 1 passed (RED→GREEN); `git check-ignore` blocks session/env probes.
- **Follow-ups:** Continue Phase-1 Tasks 2–7 natively; no Telegram access, no service users, no remote.

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** Telegram MCP Phase-1 foundation complete (Tasks 1–7).
- **Summary:** Synthetic-only MCP foundation: locked deps, validated ten-tool contracts, closed dispatch, public-SDK transport, 158-test acceptance with modern + legacy wire parity.
- **Files changed:** `src/telegram_mcp`, `scripts/extract_contracts.py`, test suites, `README.md`, `docs/verification/`, `AGENT.md`, this file.
- **Verification:** 158 passed; lint/type/format clean; wheel fresh-install smoke OK; gates A/B/I/K/L partial only.
- **Follow-ups:** Phase-2 privileged runtime plan next; no production claim.

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase-2 track integration (2a Tasks 1-5, 2b Task 1) and handoff repair.
- **Summary:** Merged the `phase-2a-authority` and `phase-2b-agent` tracks into `main` and repaired what the tracks left broken: `binascii.Error` is now imported rather than reached through `base64`, `ConsentBroker._pinned_key_id` is narrowed to `str` at construction, `tests/unit/test_policy.py` kwargs are `dict[str, Any]`, and `tests/agent/conftest.py` builds the Swift agent with the plan's exact `swiftc` command so the shell tests no longer depend on an untracked `build/` artifact.
- **Files changed:** `src/telegram_mcp/consent/broker.py`, `src/telegram_mcp/consent/challenge.py`, `tests/unit/test_policy.py`, `tests/agent/conftest.py`, `AGENT.md`, `CHANGELOG.md`.
- **Verification:** `uv run pytest -q` 271 passed, 1 skipped from a clean `build/` (Swift binary rebuilt by the fixture); `ruff check` and `ruff format --check` clean on `src tests`; `mypy src tests` clean (was 8 errors across 3 files at merge).
- **Follow-ups:** Phase-2a Tasks 6-9 inline on `main` (refs/cursors/epochs, SQLite migrations with C2/C4, admin IPC/leases/rendezvous/tunnel identity/install/doctor, CLI wiring and evidence). Phase-2b Tasks 2-5 still open. No Telegram access, no service users, no remote, no production claim.

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** Telegram MCP Phase-2a authority foundation, Tasks 6-9 (Tasks 1-5 arrived on the `phase-2a-authority` track).
- **Summary:** Completed the Phase-2a plan inline: ten-prefix ref validation, keyed cursor binding with the seven invalidation triggers mapped onto the four spec codes, epoch operations; all 19 spec tables with the C2 excerpt-width fix and the C4 retained-membership rules plus §12.3 integrity triggers, the guarded `open_db` path and the closed settings registry; `tgml1` leases per §9.7.1, one frame codec, the admin socket with the whole §33 surface routed and a presence gate, RV-1 rendezvous, tunnel pins with history, `doctor` with an honest `--production` gate, the two idempotent installers and the consent LaunchAgent definition; CLI verbs `start/stop/status/doctor/admin/keys/pair/rotate` with `demo` untouched, the three-layer vertical slice, and the Phase-2J join-gate harness.
- **Files changed:** `src/telegram_mcp/authority/{refs,cursors,epochs}.py`, `src/telegram_mcp/storage/`, `src/telegram_mcp/ipc/`, `src/telegram_mcp/doctor.py`, `src/telegram_mcp/cli.py`, `src/telegram_mcp/keys/store.py`, `scripts/install_service_users.sh`, `scripts/install_paths.sh`, `scripts/consent-agent.plist`, the new unit/contract/integration/security suites, `tests/conftest.py`, `pyproject.toml`, `README.md`, `docs/verification/phase-2a.md`, this file, `CHANGELOG.md`.
- **Verification:** Gate suite 440 passed, 17 skipped (all named); `extract --check` 23 files OK; `ruff check`/`format --check` clean over `src tests scripts`; `mypy src/telegram_mcp` clean (42 files); `uv build` produced wheel `449b6991b8da6133710d19822ba342573124f5d3ce85f3723fe211852f47a534`. Gates E/H/M/N/P and the privilege/key parts of R are recorded PARTIAL; `doctor --production` fails deliberately.
- **Follow-ups:** Phase-2b Tasks 2-5 (display gate and Touch ID, rendezvous client, pairing/LaunchAgent/signing, evidence), then the Phase-2J join gate, then the SMAppService API question. No Telegram access, no service users installed, no remote, no production claim.
