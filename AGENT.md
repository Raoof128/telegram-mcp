# Telegram MCP project guidance

The supplied `telegram-mcp-v0.1.10-final-engineering-spec.md` is the product specification. Planning documents live under `docs/superpowers/plans/` and preserve the original spec. Read the release roadmap's correction register before implementing affected contracts.

Follow the user's engineering lifecycle: design/security analysis, implementation, security/bug review, then verified delivery. Read this file and `CHANGELOG.md` before edits. Do not claim production readiness before Gates A–R pass for the exact artifact. Never put Telegram credentials, session files, private content or keys in source control or logs.

## Planning record

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** Telegram MCP planning.
- **Summary:** Read the V0.1.10 spec and prepared a release roadmap plus a detailed synthetic-foundation plan, as selected by the user.
- **Files changed:** This file, `CHANGELOG.md`, and the two dated planning documents under `docs/superpowers/plans/`.
- **Verification:** Reviewed the full spec across architecture/contract/disclosure/release workstreams; checked JSON syntax and demonstrated the SQLite NULL and nested-schema reference defects. Planning documents received link, code-fence and coverage checks.
- **Follow-ups:** Review the plans and select an execution method. No implementation, dependencies, Git initialization, host provisioning or Telegram access performed.

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** Telegram MCP Phase-1 Task 1 foundation.
- **Summary:** Initialized local Git, established locked package (`mcp==2.2.0`, `Telethon==1.45.0`, `hatchling==1.32.4`) with audit-corrected floors (`pydantic>=2.12,<3`, explicit `mcp-types`, `starlette` direct) and Appendix B `.gitignore`.
- **Files changed:** `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `src/telegram_mcp/__init__.py`, `tests/unit/test_package.py`, `docs/verification/dependencies.md`, this file, `CHANGELOG.md`.
- **Verification:** `uv sync --locked` (67 packages); `importlib.metadata` confirms mcp 2.2.0 / telethon 1.45.0; `uv run pytest tests/unit/test_package.py -q` 1 passed (RED ModuleNotFoundError watched before GREEN); `git check-ignore` blocks session/env probes.
- **Follow-ups:** Continue Phase-1 Tasks 2–7 natively; no Telegram access, no service users, no remote.

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** Telegram MCP Phase-1 foundation complete (Tasks 1–7).
- **Summary:** Built the synthetic-only MCP foundation natively: locked package, exact ten-tool contracts with C3 hoisting, bounded validation, safe_demo config, closed dispatch with synthetic status, public-SDK transport with strict-JSON preflight and header redaction, full wire/privacy/legacy acceptance.
- **Files changed:** Full `src/telegram_mcp` tree, `scripts/extract_contracts.py`, all test suites, `README.md`, `docs/verification/phase-1.md`, `docs/verification/dependencies.md`, this file, `CHANGELOG.md`.
- **Verification:** Full suite 158 passed; `extract --check` 23 files OK; ruff/mypy/format clean; wheel (39 files) fresh-install smoke OK; modern + legacy (2025-11-25) wire parity; gates A/B/I/K/L recorded partial only.
- **Follow-ups:** Phase-2 privileged runtime plan next. No Telegram access, no service users, no remote, no production claim.
