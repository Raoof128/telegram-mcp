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
