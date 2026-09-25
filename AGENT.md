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

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** Telegram MCP Phase-2b Swift consent agent, Tasks 2-5.
- **Summary:** Completed the consent agent inline: the display gate (verify the daemon signature, recompute the display digest and compare it in constant time against the digest inside the signed challenge, and only then prompt), the Touch ID approval flow on a fresh LAContext with the key obtained through a KeyProvider seam, the RV-1 rendezvous client with prompt frames frozen here, and pairing with a Secure Enclave approval key, a software transport key and a keychain-pinned daemon challenge key. Six rendezvous scenarios run against the real Python broker rather than a stub. Pairing requires a stable certificate-backed identity and refuses ad-hoc builds; the plan's Developer-ID rule was relaxed deliberately after confirming against Apple's current documentation that Developer ID and notarization govern distribution and that a free Apple Development certificate creates Enclave keys with no entitlement or provisioning profile.
- **Files changed:** `agent/consent-agent.swift`, `agent/ConsentAgent-Info.plist`, `scripts/package_agent.sh`, `scripts/consent-agent.plist`, `src/telegram_mcp/ipc/rendezvous.py`, `tests/agent/` (conftest, stub_broker, shell tests), `tests/conftest.py`, `tests/__init__.py`, `docs/verification/phase-2b.md`, this file, `CHANGELOG.md`.
- **Verification:** `tests/agent` 26 passed, 2 skipped (the interactive pairing and Touch ID legs); full suite 466 passed, 19 skipped; ruff/format/mypy clean; 9 JCS vectors byte-equal; `plutil -lint` OK; ad-hoc bundle refuses pairing (`flags=0x10002(adhoc,runtime)`) and the certificate-signed bundle reports `pairable`.
- **Follow-ups:** Both interactive legs have now run on this host: a Secure Enclave approval key (`p256:sha256:fbd39bb3...`) and a software transport key are paired, and a real Touch ID approval produced a signature the broker's verifier accepts. Next: re-point Plan 2a's Phase-2J join-gate harness at this driver and drive the remaining seven scenarios. No daemon pin (no runtime exists yet), no loaded LaunchAgent, no Telegram access, no production claim.

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase-1 + Phase-2 end-to-end smoke, and two startup-ordering fixes it found.
- **Summary:** Added `scripts/e2e_smoke.py`, which drives the shipped artifacts in one run — the demo server as a subprocess over a real TCP socket, the real 19-table schema on disk, real Unix sockets for the admin and rendezvous paths, the installed CLI, both installer dry-run plans, and the real Swift agent against the real consent broker — and prints one pass/fail ledger. Writing it exposed two ordering defects in `STARTUP_STEPS`: the single-runtime lock was acquired at step 8, after secrets were read and the database was opened, migrated and garbage-collected; and the tunnel client started before READY was advertised, contradicting both the design and the module's own comment. Both are fixed and pinned. `python -m telegram_mcp.cli` now works alongside the console script.
- **Files changed:** `scripts/e2e_smoke.py`, `src/telegram_mcp/runtime/lifecycle.py`, `src/telegram_mcp/cli.py`, `tests/unit/test_runtime.py`, `README.md`, `docs/verification/phase-2a.md`, this file, `CHANGELOG.md`.
- **Verification:** Smoke 41 passed, 0 failed, 0 skipped (~9 s, stable across three runs); full suite 467 passed, 20 skipped; ruff/format/mypy clean.
- **Follow-ups:** Re-point Plan 2a's Phase-2J join-gate harness at the Plan-2b driver and drive its remaining seven scenarios; decide how to scope the agent's transport key in the keychain. No Telegram access, no service users installed, no production claim.

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** Close the two open items — keychain scoping for the agent's transport key, and the Phase-2J join gate.
- **Summary:** The agent's keychain records now carry a `SecAccess` naming the binary as the only trusted application, and reads disable interaction so a foreign code identity fails closed rather than raising an authorization dialog; writes replace rather than update so no record inherits a permissive ACL. Verified live: the signed bundle reads its own records and survives rebuilds, the ad-hoc bundle sees none, and `/usr/bin/security` gets a prompt instead of bytes. The daemon now also serves one agent session at a time. The join gate was rebuilt on the real driver and passes all thirteen scenarios against the real broker and the real packaged agent, plus one interactive Touch ID approval through the production `run` path that imports a daemon pin and removes it afterwards. Writing it exposed two further defects, both fixed: `doctor`'s OFF probe matched any process whose arguments merely mentioned a job label, so a `dscl` or `sudo -u` probe tripped it, and a stale test asserting the old keychain exposure survived its own replacement and was passing only by skipping.
- **Files changed:** `agent/consent-agent.swift`, `src/telegram_mcp/ipc/rendezvous.py`, `src/telegram_mcp/doctor.py`, `tests/conftest.py`, `tests/agent/conftest.py`, `tests/agent/stub_broker.py`, `tests/agent/test_consent_agent.py`, `tests/integration/test_join_gate.py`, `tests/unit/test_ipc.py`, `tests/unit/test_doctor.py`, `tests/security/test_install.py`, `scripts/e2e_smoke.py`, `docs/verification/phase-2a.md`, `docs/verification/phase-2b.md`, this file, `CHANGELOG.md`.
- **Verification:** 484 passed, 7 skipped by default; 487 passed, 4 skipped with `--run-platform-gated` (the four remaining skips are the rotation ceremony behind its flag, two install-dependent probes, and the vector generator); smoke 41/41; ruff, format and `mypy src/telegram_mcp` clean.
- **Follow-ups:** Phase 3 (disclosure, budgets, proofs, audit chain). Service accounts and paths are still not installed on this host, and no runtime has ever issued a challenge to the paired agent outside the join gate. No Telegram access, no production claim.

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** Project memory and cross-project knowledge capture.
- **Summary:** Added `CLAUDE.md` as the repository's working agreement — non-negotiables, the verification commands, the module map, the frozen wire contracts and the current host state — so a fresh session starts from the same rules rather than rediscovering them. Recorded the session's durable knowledge in Zurvan: four accepted decisions (pairing on a stable code identity rather than Developer ID, SecAccess scoping for the keychain records, the corrected startup ordering, and driving the real broker instead of a stub), four findings (the free-certificate Secure Enclave path, the keychain ACL behaviour matrix, SQLite admitting a row whose CHECK evaluates to NULL, and macOS LibreSSL rejecting `-noenc`), and one open question about what `doctor --production` will still refuse once the installers have run. Zurvan's search index was rebuilt.
- **Files changed:** `CLAUDE.md`, this file, `CHANGELOG.md`; Zurvan wiki entries outside this repository.
- **Verification:** Full suite 484 passed, 7 skipped; `zurvan index search` rebuilt 185,480 chunks; decisions and question accepted by the Zurvan write tools.
- **Follow-ups:** No Git remote is configured, so nothing has been pushed. Phase 3 next.

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** Third gauntlet pass over the Phase-3 design, and the fixes it forced.
- **Summary:** Ran the `simurgh-arise` doctrine over `docs/superpowers/specs/2026-09-22-telegram-mcp-phase-3-design.md`, checking every normative citation against the frozen specification and every architectural claim against the shipped code rather than against the document's own reasoning. Thirteen claims verified clean — the spec hash, all ten error codes and their retryability, the 15-second deadline, all seven §33 commands, the anchor fallback wording, the §12.2 degraded flow clause by clause, the five table DDLs, the two-dimension ledger, the three key-registry rows and the retention values — and the controlling statement survived a direct attempt to falsify it, because Phase 1 had already transcribed `disclosure_proof_key_id`, `disclosure_proof_public_key`, `meta.disclosure` and the whole `meta.coverage` object into the frozen contracts. Eighteen defects did not survive. Six were blocking: the design promised to keep `DisclosureGate` as the seam, but it returns a boolean and cannot release a payload, contradicting the document's own controlling statement (it also has no production caller, so the fix is free); Appendix L is normative and the design had substituted its own eight invariants for the required fourteen and named neither `formal/README.md` nor `SECURITY-MANIFEST.json`; the §23D coverage proof had no design text and no plan owner although its object is already contract-frozen and its digest signed into every search receipt; per-project byte attribution was missing, so an implementer would have charged envelope overhead to every project bucket; the reservation tuple had silently dropped `security_epoch` and `consent_challenge_digest`; and the pre-consent hard-budget refusal §23C.3 mandates was absent, so the design would have prompted a human for a call it was about to refuse. Twelve more followed, including the audit-chain genesis value and epoch-bump rule being undefined, the absence of a single measurement authority against a Gate P MUST, the settings work being understated as three rows when it is eleven, and no content-leak sweep against Gate O's first privacy MUST. Two defects were in shipped code, not the document. The checkpoint-cadence registry rows carried maxima of 100,000 events and seven days — wide enough for an operator to configure a violation of §26.5's "at least every 500 events or 60 minutes" — under a comment asserting the specification states no number, which §26.5 and the reference configuration both contradict; fixed test-first, maxima now 500 and 3600. And `tools/status.py` advertises a key derived from 32 zero bytes while Appendix K.2 tells verifiers to resolve `proof_key_id` against exactly that key: harmless while nothing signs anything, a forgery oracle the moment Phase 3 lands a signer, so it now carries a tripwire test that fails when the real key arrives and a warning at the point of the hazard. One finding was refined rather than adopted: the arithmetic showing two 45-second consent windows exceed the 75-second recommended client timeout is real, but §23C.3 says the request MUST be re-prompted, so the bound stays and the timing is documented as safe-but-wasteful instead. The design was rewritten as revision 2 with all eighteen folded in, plus the claim boundary that no participant in those conversations ever consented, and the honest bound that rolling windows limit burst rate and not lifetime exposure.
- **Files changed:** `docs/superpowers/specs/2026-09-22-telegram-mcp-phase-3-design.md` (446 → 928 lines), `src/telegram_mcp/storage/settings.py`, `src/telegram_mcp/tools/status.py`, `src/telegram_mcp/consent/gate.py`, `tests/unit/test_storage.py`, `tests/security/test_demo_isolation.py`, this file, `CHANGELOG.md`.
- **Verification:** 486 passed, 7 skipped (two new tests, both written before their fix or as a deliberate tripwire); smoke 41 passed, 0 failed; `extract_contracts --check` 23 files OK; ruff, format and `mypy src/telegram_mcp` clean over 42 source files; the fourteen Appendix-L assertions diffed programmatically against the frozen text with no omission and no addition; spec self-review clean (0 placeholders, 18 of 18 defect rows, 11 of 11 settings rows, every cross-reference resolving).
- **Follow-ups:** Write the three implementation plans (3a measurement/egress/provenance/coverage/receipts/keys, 3b accounting/reservations, 3c chain/anchor/coordinator/formal model) against revision 2. Honest scorecard before the pass was 5.2 of 10 across fidelity, consistency, implementability, falsifiability and honesty; it should be re-scored after the plans exist, not now. No Telegram access, no service users installed, no production claim.

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** External implementation-readiness review of the Phase-3 design; revision 3.
- **Summary:** A fourth review read revision 2 against the frozen contract hunting implementation ambiguity rather than architectural error, and raised ten points. Its document lives in a sandbox this machine cannot reach (`sandbox:/mnt/data/...`, absent from every local path), so nothing was read or merged; each point was verified against the frozen specification and revision 2 directly and applied here. Eight were real and adopted: budget decisions must compare the projected figure — committed rows plus live reservations plus this call's worst case — because the reservation is that worst case and a ceiling compared against current totals would bound where a call starts rather than where it ends; the append barrier needed an actual process-wide `audit_append_guard` acquired before Step 11 and held across Step 12, since a barrier entered by committing leaves a window where a second caller commits and the chain goes two ahead; the fallback anchor had no format or durability procedure, now a frozen JSON shape with a domain-separated `anchor_mac` that cannot be confused with an event MAC, a write/fsync/rename/fsync sequence and fail-closed symlink, owner and mode checks; the formal model asserted over anchor and payload state it never declared, so five variables were added including `audit_integrity_state` over the five integrity states, because an assertion that cannot be expressed produces a checker that passes for the wrong reason; "ordered set" was replaced by an ordered vector in emitted-record order with four frozen fields, since a set has no order and two implementers would canonicalise differently; `meta.coverage` sits outside `data` and therefore outside `bytes_disclosed`, verified against the frozen search contract whose `data` members are `project`, `results` and `search_scope`, so revision 2's "envelope and coverage overhead are charged globally" was wrong in both directions; a successful anchor repair is now itself a chained and anchored `admin.repair_anchor` event whose six-step ordering clears the latch only after the repair event is anchored; and the extraction benchmark is reframed as benchmark-observed under a named synthetic configuration and an explicit lower bound, because calling a fake-adapter number "how much private content a compliant assistant can see" was the adjective inflation this project refuses elsewhere. One point was refined: revision 2 already carried all four FAIL CLOSED rows, so the claim that fatal states were newly made explicit was stale — what was genuinely missing is that `repair-anchor` must actively refuse in those states rather than merely being unable to help, since moving a pointer over lost history is laundering evidence, and that is now stated with its reason. One claim was withdrawn rather than implemented: revision 2 promised `disclosure show` would report `payload_unreconstructable` after a ref-regenerating restore, which Phase 3 cannot deliver without durable restore-lineage metadata it does not persist, so the distinction is handed to Phase 5 and the false comfort removed. Two further defects surfaced while applying the rest and were fixed here: `event_id` enters the event MAC, so its format is frozen as `evt_` plus a 26-character Crockford base32 ULID per Appendix C, or the chain is not reproducible; and `audit_events.tool_name` is `NOT NULL` while §6.5 requires administrative events to append through the same barrier, so a closed dotted vocabulary was fixed that no MCP tool name can collide with.
- **Files changed:** `docs/superpowers/specs/2026-09-22-telegram-mcp-phase-3-design.md` (928 → 1127 lines), this file, `CHANGELOG.md`.
- **Verification:** 486 passed, 7 skipped; smoke 41 passed, 0 failed; ruff and `mypy src/telegram_mcp` clean over 42 source files. Document self-review clean: 0 placeholders, all three superseded revision-2 phrasings gone except where the withdrawal quotes them, and every new anchor present. No source file changed this round.
- **Follow-ups:** The design is ready to freeze; the next review belongs at the 3a/3b/3c plan level, where the subject changes from architectural ambiguity to exact APIs, SQL transactions, failure injection and test ordering. No Telegram access, no service users installed, no production claim.

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** The three Phase-3 implementation plans, and the gauntlet over them.
- **Summary:** Wrote `3a` (measurement authority, egress, provenance, §23D coverage, Appendix-K receipts, offline verifier, verification-key registry, key provisioning, synthetic-key retirement), `3b` (subject digests, rolling windows, the two dimensions, tiers, reservations, ledger commit, concurrency and bypass suites) and `3c` (settings rows, MAC-linked chain, external anchor, checkpoints, coordinator, crash model, content-leak sweep, recovery ceremony, formal model, extraction benchmark) — 5,157 lines across three files. Then gauntleted them by **executing** their own code rather than reading it: the plans' modules and tests were extracted verbatim into a throwaway spike and run. That found eleven defects the design review could not have caught, because each lives in the seam between two tasks. Two were blocking. `append_event` opened and committed its own `BEGIN IMMEDIATE`, which SQLite proves incompatible with the coordinator wrapping it — the error is `cannot start a transaction within a transaction` — so running the plan as written would either raise at step 11 or push an implementer into deleting the coordinator's transaction, silently turning §23A.3's one atomic commit into three and destroying the crash model the whole design rests on; appends now require a caller-owned transaction and refuse without one. And plan 3a proposed reporting `null` for the two disclosure-key fields of `telegram_status`, which the frozen contract types as a non-null string and a 43-character base64url pattern, so the null design would have broken the contract and widening it would have contradicted the controlling statement; the key is now required, with the demo minting an ephemeral per-process Ed25519 key whose private half signs nothing. Three test defects were proven by running them: a measurement assertion comparing per-project bytes against the global figure was arithmetically false because the global figure carries container overhead (381 vs 409 on the plan's own fixture); a provenance assertion compared insertion-ordered dict keys against an alphabetical list; and an `event_id` assertion was vacuous, passing for any string containing a digit. Six more were structural: the coordinator's twelve steps were left as a "steps 1-10 omitted for brevity" comment in the single most important module of Phase 3, now written in full with the append guard wrapping steps 11 and 12; four suites depended on five fixtures that appeared nowhere; `disclose_sync` was called in tests against an `async def disclose` interface, resolved as async tests since `asyncio_mode = "auto"`; `disclosure/verify.py` was referenced by two tasks and created by none; `AdminRouter.knows()` was invented where the real closed list is the module-level `ADMIN_COMMANDS` tuple, whose 51 entries do contain all seven commands Phase 3 needs; and `SettingSpec`'s new `pattern` field was described rather than shown. Writing the plans also caught a defect in the design committed an hour earlier: §5.2's record-element list named `peers[]`, `projects[]` and `unread[]` for tools whose frozen contracts actually emit `matches[]`, `matches[]` and `chats[]`, and `telegram_cross_project_search` carries a `projects` array that is scope metadata rather than records — so a measurement keyed on it would have counted the wrong thing on exactly the tool where cross-project accounting matters most.
- **Files changed:** `docs/superpowers/plans/2026-09-22-telegram-mcp-phase-3a-disclosure-machinery.md`, `...-3b-exposure-accounting.md`, `...-3c-audit-and-coordinator.md` (new), `docs/superpowers/specs/2026-09-22-telegram-mcp-phase-3-design.md`, this file, `CHANGELOG.md`.
- **Verification:** The spike ran the plans' own code: measurement 5 of 6 passing before the fix, egress 8 of 8, provenance 13 of 14, coverage 10 of 10, receipts 9 of 9, budget 7 of 7; the nested-transaction failure reproduced directly against the project's own connection factory; all seven §33 commands confirmed present in `ADMIN_COMMANDS`; the status contract's non-nullability read off the frozen schema. Spike removed afterwards — the tree carries plans only. Suite 486 passed, 7 skipped; ruff, format and mypy clean. Final placeholder scan across all three plans: zero.
- **Follow-ups:** Execute 3a, then 3b, then 3c. The dependency direction is one-way, so 3a can start immediately. No Telegram access, no service users installed, no production claim.

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** Execute Phase-3 Plan 3a inline — the deterministic disclosure machinery.
- **Summary:** All nine tasks done test-first: the three Phase-3 key rows are provisioned and carry distinct recomputed ids; `disclosure/measure.py` is the single measurement authority Gate P demands, with per-tool record elements read off the frozen contracts and a per-project/global byte split that reconciles exactly; `egress.py` transforms and intersects profiles without ever sanitising a message body; `provenance.py` commits to an ordered emitted-record vector whose digest is order-sensitive and text-blind; `coverage.py` carries the §23D object, its closed six `partial_reasons` and the invariant chain JSON Schema cannot express, validated against the frozen `meta.json`; `receipts.py` builds the twenty-one Appendix-K fields, signs over JCS bytes and rejects tampering; `keys.py` holds the public verification-key registry with retirement that never deletes; and the zero-seed disclosure key is retired. Execution found four defects the plan had not: `keys provision` created four rows while `keys list` reported seven, so the two CLI verbs disagreed and `list` failed on a freshly provisioned store; two existing tests were pinning the gap rather than the requirement (`test_provision_phases_3_creates_nothing` asserted the defect literally) and were replaced; the smoke provisioned Phase-2 rows only and failed three checks once `FILE_BACKED_KEYS` grew; and ruff's `PLE2502` trojan-source rule correctly rejected the bidi control the egress test needs, which now builds it with `chr(0x202E)` so the character reaches the transformer without sitting in the source. The tripwire planted two commits earlier fired exactly on cue when `make_status` gained its required key argument, and was replaced by tests that describe the requirement instead of the hazard. One process failure worth recording: an early commit went in against a red suite because `&&` chained off `tail` rather than pytest; caught on the next command and fixed in the following commit, but the guard belongs in the habit, not the pipeline.
- **Files changed:** `src/telegram_mcp/disclosure/{__init__,measure,egress,provenance,coverage,receipts,keys}.py` (new), `src/telegram_mcp/tools/status.py`, `src/telegram_mcp/dispatch.py`, `src/telegram_mcp/keys/store.py`, `src/telegram_mcp/cli.py`, `tests/unit/test_disclosure_{measure,egress,provenance,coverage,receipts,keys}.py` (new), `tests/security/test_offline_verifier.py` (new), `tests/security/test_demo_isolation.py`, `tests/unit/test_keys.py`, `tests/unit/test_doctor.py`, `tests/integration/test_cli.py`, `tests/conftest.py`, `scripts/e2e_smoke.py`, `docs/verification/phase-3.md` (new), `CLAUDE.md`, this file, `CHANGELOG.md`.
- **Verification:** 536 passed, 7 skipped (up from 486); smoke 41 passed, 0 failed; `extract_contracts --check` 23 files OK; ruff, format and `mypy src/telegram_mcp` clean over 49 source files. The offline verifier is proven by forbidding `sqlite3.connect` and `socket.socket` inside the test, so "needs no database" is enforced rather than asserted. Coverage objects validate against the frozen `meta.json`. Gates O, P and Q are recorded PARTIAL in `docs/verification/phase-3.md`, each naming exactly what is missing.
- **Follow-ups:** Plan 3b (exposure accounting), then Plan 3c (chain, anchor, coordinator). Nothing from 3a is wired into a tool: the nine sensitive tools still return `POLICY_UNCONFIGURED` and no disclosure has ever been committed. No Telegram access, no service users installed, no production claim.

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** Execute Phase-3 Plan 3b inline — exposure accounting.
- **Summary:** All seven tasks done test-first. `disclosure/budget.py` now carries keyed subject digests so no opaque ref lands in `exposure_ledger` while `exposure status` can still recompute one deterministically; rolling windows; the two budget dimensions, where three projects touch four physical buckets and a record with several origins is charged whole in each contributing bucket; thresholds read from the closed settings registry with "at or above" escalating rather than passing; the reservation lifecycle bound to the six components §23C.3 freezes, including the `security_epoch` and `consent_challenge_digest` revision 1 had dropped; and `commit` converting a reservation into ledger rows inside a caller-owned transaction, refusing when actual exceeds reserved. Execution corrected two defects the plan had carried. The concurrency suite used threads, but `sqlite3` connections are thread-bound and this daemon is single-process asyncio, so the thread version died inside `get_setting` with "SQLite objects created in a thread can only be used in that same thread" before it reached any budget logic; it now races coroutines, which is the real concurrency model, and `BudgetLedger` documents that a future thread pool needs a ledger and a connection per thread rather than a shared one. And the project-cycling fixture reserved 700 records per project against a 500-record per-project ceiling, so it tripped on the first call instead of demonstrating what it claimed: three calls of 400 now pass under their own ceilings while the fourth reaches 1600 against the 1500 client-global ceiling, which is the bypass the test exists to catch. The shared `seed_authority_rows` and `insert_committed_receipt` helpers moved to `tests/authority_fixtures.py`, because `conftest` is not importable from a subdirectory, and they are proven against the §12.3 tuple-consistency trigger that rejects a receipt whose client belongs to another principal or whose principal/account pair has no `policy_state` row.
- **Files changed:** `src/telegram_mcp/disclosure/budget.py`, `tests/unit/test_disclosure_budget.py` (new), `tests/integration/test_budget_concurrency.py` (new), `tests/security/test_budget_bypass.py` (new), `tests/authority_fixtures.py` (new), `tests/conftest.py`, `docs/verification/phase-3.md`, `docs/superpowers/plans/2026-09-22-telegram-mcp-phase-3b-exposure-accounting.md`, this file, `CHANGELOG.md`.
- **Verification:** 561 passed, 7 skipped (up from 536); smoke 41 passed, 0 failed; ruff, format and `mypy src/telegram_mcp` clean over 50 source files. Gate P now records soft and hard tiers behaving exactly as §23C.1 specifies against the projected figure, concurrent reservations unable to both take the last capacity, retries and project cycling both caught, and `actual ≤ reserved` failing closed — with what remains stated plainly: no tool consults the ledger, so nothing is enforced end to end, and measurement identity across prompt, reservation, ledger and receipt cannot be asserted until a coordinator exists.
- **Follow-ups:** Plan 3c (audit chain, external anchor, degraded state and recovery, the coordinator, the operator commands, the formal model, the crash and content-leak gauntlets). No Telegram access, no service users installed, no production claim.

### 2026-09-22 (Australia/Sydney)
**Raouf:**
- **Scope:** Execute Phase-3 Plan 3c inline — audit chain, external anchor, coordinator, formal model.
- **Summary:** All ten tasks done test-first. The eleven Phase-3 settings rows landed, and with them the content guarantee moved from a key-name heuristic to the type: an `int` row cannot hold prose whatever it is called, and every `str` row must now be closed by `choices` or a `pattern`, which closed the last free-form key (`audit.external_anchor_ref`, now a bare filename with no separators and no traversal). `audit/chain.py` carries MAC-linked events with an epoch-bound genesis and refuses to append outside a caller-owned transaction — the guard that makes §23A.3's single commit possible, and the one that caught the same defect again later in the repair path. `audit/anchor.py` has the frozen JSON shape, a domain-separated `anchor_mac` that cannot be replayed from an event MAC, write/fsync/rename/fsync durability and a rejection matrix verified live (0644 refused, wrong key refused, no temp file surviving). `coordinator.py` runs the twelve steps with the append guard held across 11 and 12 and returns a payload with its receipt or a refusal, never a third shape. The crash suite proves the model: ten crash points before step 11 leave nothing durable, a crash between 11 and 12 leaves the disclosure accounted and the payload withheld with the latch set, and the next call is refused while appending nothing. Writing that found a real defect — the step-12 crash seam sat outside the anchor handler, so an injected failure escaped instead of latching degraded. The content-leak sweep scans every table plus the logs and carries a control test proving it can fail. `verify.py` rebuilds a persisted receipt byte-identically and verifies it, including after a client credential rotation, which is the hole revision 2 of the design had promised to paper over and revision 3 withdrew. The bounded formal model explores all 624 reachable states against Appendix L's fourteen assertions plus this architecture's four, with a second test asserting no assertion is unreachable; it earned its keep three times, showing that revalidation alone does not stop a locked disclosure, that the window between step 8 and step 12 is closed by the reservation's `security_epoch` binding rather than by revalidation — exactly the component an earlier draft had dropped — and that the append guard must exclude environment interleaving or a lock lands between the commit and the anchor. No third-party checker was available, so the model is a self-contained exhaustive breadth-first search, recorded as such in `SECURITY-MANIFEST.json`.
- **Files changed:** `src/telegram_mcp/disclosure/{coordinator,verify}.py` and `disclosure/audit/{__init__,chain,anchor}.py` (new), `src/telegram_mcp/storage/settings.py`, `formal/{model.py,README.md}` and `SECURITY-MANIFEST.json` (new), `tests/unit/test_audit_{chain,anchor}.py`, `tests/integration/test_{disclosure_coordinator,audit_recovery}.py`, `tests/security/test_{no_uncommitted_escape,no_content_in_stores}.py`, `tests/formal/test_state_machine.py`, `tests/adversarial/test_maximal_extraction.py`, `tests/coordinator_fixtures.py` (all new), `tests/unit/test_storage.py`, `docs/verification/phase-3.md`, `CLAUDE.md`, `README.md`, this file, `CHANGELOG.md`.
- **Verification:** 626 passed, 7 skipped (up from 561); smoke 41 passed, 0 failed; formal model 624 reachable states with all 18 assertions holding and none unreachable; ruff, format and `mypy src/telegram_mcp` clean over 55 source files. The adversarial extraction benchmark ran once and its first result is sealed in the evidence: 1152 releases, 48 refusals, 23,040 records across 24 simulated hours, 1152 receipts — every release accounted. The bound that actually bit was the per-project ceiling of 500 rather than the client-global 1500, because a single-project client never reaches the global limit, and the client was refused in every window and simply continued in the next, which is the honest demonstration that budgets limit burst rate and not lifetime exposure.
- **Follow-ups:** Phase 4 — the real Telegram adapter and the nine tool slices behind the disclosure seam. Gates O, P and Q remain PARTIAL: no tool slice calls the coordinator, so the nine sensitive tools still return `POLICY_UNCONFIGURED`, and every result above was produced against a fake adapter. No Telegram access, no service users installed, no production claim.

### 2026-09-23 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase-4 design (allowlisted Telegram adapter and vertical tool slices). Design only; no code.
- **Summary:** Four sections presented and reviewed one at a time, each amended before the next; the result is written to `docs/superpowers/specs/2026-09-23-telegram-mcp-phase-4-design.md`. Decomposition: 4a real coordinator seams and the authenticated runtime ingress with `list_projects`/`resolve_project` as the first real sensitive success, no network; 4b Telethon adapter, daemon-side login and discovery, Test DC, `list_chats`/`resolve_peer`/`get_messages`/`get_unread` with a counterpart-observed `read_outbox_max_id` witness; 4c `get_context` (forum topics), both searches with a bounded 64-peer continuation window and truthful coverage, and a dedicated-account qualification run. Reading the code during design found four things the spec now carries: the runtime has no authenticated MCP ingress (only the synthetic demo server), the coordinator's authority and consent collaborators exist only as test fakes, no admin handler writes projects, grants or peers, and the coordinator reserves with a request-derived nonce where §9.8/§23C.3 bind the challenge's nonce. Reading the pinned Telethon 1.45.0 source fixed the exact login RPC set and showed entity resolution can silently send `contacts.ResolveUsernameRequest`, so the design requires cache-only `InputPeer` construction plus a runtime outbound-RPC recorder alongside the source AST guard. Phase-2 cursor limits (`_PER_PEER_MAX=64`, 8,192-byte state) set the continuation window size.
- **Files changed:** `docs/superpowers/specs/2026-09-23-telegram-mcp-phase-4-design.md` (new), this file, `AGENT.md`/`CHANGELOG.md`.
- **Verification:** Baseline re-run for the design: 626 passed, 7 skipped. Every Telethon request class named in the allowlist confirmed present in the installed 1.45.0 package; frozen error codes checked against §27.1.
- **Follow-ups:** External line-by-line gauntlet of the written spec, then the 4a plan via writing-plans. Open to verify at plan time: Telegram's strictness on `limit > -add_offset`, and the current Test DC address and number/code conventions. No Telegram access, no service users installed, no production claim.

### 2026-09-23 (Australia/Sydney)
**Raouf:**
- **Scope:** Gauntlet of the Phase-4 design, revision 1 → revision 2. Design only; no code.
- **Summary:** Every claim in revision 1 was checked against the shipped code, the pinned Telethon 1.45.0 source and Telegram's own method and constructor pages; sixteen defects were found and fixed in place, each listed with its evidence in §0A. The largest: the daemon has no prompt-delivery path at all — `PROMPT`/`APPROVAL`/`DENIAL` frames live only in `tests/agent/stub_broker.py` and only `doctor.py` self-tests ever call `broker.consume` — so 4a gains `consent/prompter.py`. Also: `ConsentBroker.issue` is async while the coordinator calls it without `await`; `ConsumedChallenge` carries neither the nonce nor the exposure digest the design relied on; consent timeout is `CONSENT_DENIED` per §27.1 (revision 1 said `CONSENT_UNAVAILABLE`, the shipped broker says `DEADLINE_EXCEEDED` — both wrong); the admin router gates only `lock`/`unlock`, so §33's "mutations remain presence-gated" must be implemented, with a test tying the gated set to the mutating set; the cursor validator rejects booleans and any non-integer key except `offset_peer_ref`, so exhaustion is now encoded by absence from `per_peer`; Hypothesis is not a dependency; Telegram's `forum_topic` flag is absent in the General topic and a direct topic post has no `reply_to_top_id`, so the topic rule became a five-row table with a General-topic path; Telethon's `_on_login` sends `GetDifference` unconditionally, so the runtime recorder's allowlist is scoped by operation; bad bearers and rate limits are HTTP 401/429 before parsing because §27.1 has no rate-limit code; and continuation past `max_cross_project_peers` was withdrawn because CT-139 forbids a complete result under peer-cap exhaustion. The dev Keychain placement is recorded as a deviation from §9.1, not as compliance.
- **Files changed:** `docs/superpowers/specs/2026-09-23-telegram-mcp-phase-4-design.md`, `AGENT.md`, `CHANGELOG.md`.
- **Verification:** Checked by execution: Telethon constructor arguments and all named request classes present in 1.45.0; `_on_login` source read; `messages.search` bounds strict and `inexact` present; `messages.getReplies` parameters; Telegram's pagination page states no strict `limit > -add_offset` rule, so that stays an empirical Test DC check. Suite unchanged at 626 passed, 7 skipped (no code touched).
- **Follow-ups:** Owner review of revision 2, then the 4a plan via writing-plans. No Telegram access, no service users installed, no production claim.

### 2026-09-23 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase-4a implementation plan. Plan only; no product code.
- **Summary:** Wrote `docs/superpowers/plans/2026-09-23-telegram-mcp-phase-4a-seams-and-ingress.md` from design revision 2: twelve test-first tasks taking `telegram_list_projects` and `telegram_resolve_project` to a real, receipted, anchored success through an authenticated loopback ingress, a real consent prompt and the Phase-3 coordinator. Reading the code for the plan found more the design had not named, now owned by tasks: nothing loads SQLite rows into an `AuthorityView`; the coordinator emits `next_cursor: null` unconditionally and leaves adapter side keys (`_coverage`) inside `data`, where they would be measured and fail the output schema; its `meta.source` is hard-coded to `telegram`; `disclose` takes no principal and retrieval receives raw rather than frozen arguments; the policy engine's `discover` operation is what the catalogue tools need; `provision_lease_seed` mints on a miss, so the ingress gets a read-only `read_lease_seed`; and `policy.evaluate` treats an empty allowlist as allow-all (recorded as a 4b blocker, unreachable in 4a). One empirical question was settled by probe instead of assumption: a `ContextVar` set in the ASGI wrapper is visible in the SDK's `on_call_tool` per request under stateless Streamable HTTP. Scope boundaries are stated up front: identity rows are seeded (the account row comes from 4b login), admin presence proofs stay injected until 4b, and there is no daemon entry point until 4b.
- **Files changed:** `docs/superpowers/plans/2026-09-23-telegram-mcp-phase-4a-seams-and-ingress.md` (new), `AGENT.md`, `CHANGELOG.md`.
- **Verification:** Every signature the plan consumes was read from the shipped source; the ContextVar route was executed against the installed `mcp==2.2.0`. Suite unchanged at 626 passed, 7 skipped.
- **Follow-ups:** Owner review of the plan and choice of execution method. No Telegram access, no service users installed, no production claim.

### 2026-09-23 (Australia/Sydney)
**Raouf:**
- **Scope:** Gauntlet of the Phase-4a plan, revision 1 → revision 2. Plan only; no product code on `main`.
- **Summary:** Revision 1 was executed verbatim, task by task, in a throwaway worktree: 699 passed with minimal fixes, and no task's tests failed for a logic reason. The run and a static pass found fifteen defects, all fixed in place and listed in the plan's "What revision 2 changed". The one that mattered most came from executing, not reading. Three concurrent calls from one client produced four prompts, every one approved, and still one refusal: budget buckets are per client and §9.8/§23C.3 require the approved exposure digest to match exactly, so a sibling call from the same client moves the snapshot by construction. The fix serialises sensitive disclosures per client, lowering the §28 per-client ceiling to 1 as §28 permits. Also: `meta.partial` would have contradicted the signed receipt; the commit gate failed on formatting in every task; the `CoordinatorConsent` harness raced its own session; a relative source path let seven architecture guards pass vacuously; a `git checkout -- src` instruction would have wiped uncommitted tasks; and design §2.8's cancellation and oversize cases had no tests.
- **Files changed:** `docs/superpowers/plans/2026-09-23-telegram-mcp-phase-4a-seams-and-ingress.md`, `AGENT.md`, `CHANGELOG.md`.
- **Verification:** Revision-2 code applied in the worktree: 704 passed, 7 skipped; ruff clean after formatting; mypy clean over 71 files; formal model 624/624. The new same-client concurrency test was seen to fail with the per-client lock removed (one call refused) and to pass with it restored. `main` suite unchanged at 626 passed, 7 skipped.
- **Follow-ups:** Owner review of revision 2 and choice of execution method. The throwaway worktree `.claude/worktrees/agent-a00bb75a052c3c0e1` holds uncommitted revision-2 code and can be removed. No Telegram access, no production claim.

### 2026-09-23 (Australia/Sydney)
**Raouf:**
- **Scope:** Execute the Phase-4a plan (revision 2) inline on branch `phase-4a` — real seams, authenticated ingress, first real sensitive success.
- **Summary:** All twelve tasks done test-first, each watched red before green, each committed on a green gate. `telegram_list_projects` and `telegram_resolve_project` now succeed through the whole chain: bearer checked before the body is parsed (401, byte-identical across causes; 429 with `Retry-After` for rate limits), identity-only `PrincipalContext`, live SQLite authority read fresh at steps 2 and 8, a consent prompt delivered by the new daemon-side prompter and answered by the packaged agent over RV-1, the coordinator's receipt, ledger rows, one audit event and anchor. The broker now carries the signed nonce and exposure digest, a consent timeout is `CONSENT_DENIED` per §27.1, the coordinator awaits issue, reserves with the approved nonce after the digest re-check, splits adapter side keys out of `data`, and labels catalogue results as gateway metadata. Every mutating admin command is presence-gated. Sensitive disclosures run one at a time per client, because exact-digest consent over per-client buckets otherwise re-prompts and refuses concurrent same-client calls. Tests written after their code (the ingress) were proven by mutation; the concurrency and revoke-during-prompt tests were shown to fail with their fix removed; three planted guard violations each failed their guard.
- **Files changed:** `src/telegram_mcp/{http_guards,sensitive_dispatch}.py`, `runtime/{identity,ingress,composition}.py`, `consent/{prompter,display}.py`, `disclosure/{exposure,seams}.py`, `storage/authority_view.py`, `telegram/{__init__,service,metadata}.py`, `ipc/handlers/{__init__,projects,leases}.py` (all new); `consent/broker.py`, `disclosure/coordinator.py`, `ipc/admin.py`, `keys/store.py`, `server.py`; eleven new test files plus `tests/coordinator_fixtures.py`, `tests/agent/stub_broker.py`, `tests/unit/{test_consent,test_gate,test_ipc}.py`; `scripts/e2e_smoke.py`; `docs/verification/phase-4.md` (new); `CLAUDE.md`, this file, `CHANGELOG.md`/`AGENT.md`.
- **Verification:** `uv sync --locked`, contract check, `pytest` 704 passed / 8 skipped, smoke 45 passed / 0 failed, formal model 624 states / 18 assertions, ruff check and format clean, mypy clean over 71 files, `uv build` sdist + wheel — every command exit 0, run fresh.
- **Follow-ups:** 4b: fix `policy.evaluate` treating an empty allowlist as allow-all (before any peer-scoped success), the live admin presence path, the daemon entry point, the account row from login. Owner-run: `pytest tests/integration/test_phase4a_touch_id.py --run-platform-gated`. No Telegram access, no service users installed, no production claim.

### 2026-09-23 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase-4a final whole-branch review and its fix pass, on branch `phase-4a`.
- **Summary:** A separate reviewer (started before the owner's no-subagent rule, finished at the owner's choice) found no Critical issue and no path that releases data or a receipt without verified consent; all six Review Focus scenarios held under execution. Two Important findings were fixed in this session, each with a test seen failing first. First, a client that disconnected mid-prompt left its consent challenge live, so a late approval committed a receipt, ledger row and audit event for an abandoned call, against spec §9.8's "MUST be invalidated"; the HTTP preflight now runs the app as a task and cancels it on `http.disconnect`, which reuses the existing cancellation path. Second, an agent that died between prompts went unnoticed and held the rendezvous slot; the prompter now owns one reader loop per session that detects EOF at once, routes answers by handle, and never cancels a read mid-frame (which also removes a minor desync). Remaining minors are deferred and the reviewer's "declined to judge" items ruled on in the ledger; display-name bidi safety is recorded as a hard 4b prerequisite.
- **Files changed:** `src/telegram_mcp/http_guards.py`, `src/telegram_mcp/consent/prompter.py`, `tests/integration/test_phase4a_end_to_end.py`, `tests/unit/test_prompter.py`, `docs/verification/phase-4.md`, `CLAUDE.md`, this file, `CHANGELOG.md`/`AGENT.md`.
- **Verification:** pytest 706 passed / 8 skipped; smoke 45/45; join gate against the packaged agent 16 passed / 1 skipped; reviewer's probes now show pending 0 and counts (0,0,0) after a disconnect, and immediate detach after idle agent death; ruff, format and mypy clean.
- **Follow-ups:** merge decision for `phase-4a` (owner); owner-run Touch ID test; 4b prerequisites listed in `docs/verification/phase-4.md` §7.

### 2026-09-23 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase-4b implementation plan and design revision 3. Plan and design only; no product code.
- **Summary:** Wrote `docs/superpowers/plans/2026-09-23-telegram-mcp-phase-4b-adapter-and-tools.md`: eighteen test-first tasks.
  - **Tasks 1–6:** owner mode (an empty allowlist denies), prompt-safe names, live Touch ID admin approvals on the unchanged consent wire with sentinel refs, identity bootstrap and `client rotate`, deadlines/work budgets/fair admission, and the Keychain `api_hash` reader.
  - **Tasks 7–11:** the ref store, the single Telethon module, dialog discovery, three-step login, and scope/allowlist handlers.
  - **Tasks 12–16:** worst-case bounds with a page cap, the project-scoped authority snapshot, a coordinator re-check of authority before retrieval with typed retrieval refusals, history views, and the four tools.
  - **Tasks 17–18:** an alias-resolving RPC guard with composition and the daemon, and the Test DC harness with a runtime recorder and read-state witness.

  Design §3.8 records the owner's two decisions: reuse the wire with sentinel refs, and one 4b plan. It adds three refinements the planning forced:
  - `list_chats`, `get_unread` and `resolve_peer` read only the project's members via `GetPeerDialogs`.
  - Pages fit a 48 KiB data cap, because the 64 KiB response refusal happens after commit.
  - Telegram tools refuse before consent when no session exists.

  Before handover, the plan's code was dry-run in a scratch copy of `main`, which found eight defects, now fixed in the text:
  - Telethon 1.45 constructor changes (2);
  - a keyed digest at import;
  - an open implicit transaction;
  - an order-dependent demo-isolation test;
  - the daemon importing a concrete backend;
  - an unimported name;
  - a missing runtime handler.

  It also found that the existing RPC guard reads only import statements, so it would never have seen an RPC built as `functions.messages.X`; Task 17 fixes it.
- **Files changed:** `docs/superpowers/plans/2026-09-23-telegram-mcp-phase-4b-adapter-and-tools.md` (new), `docs/superpowers/specs/2026-09-23-telegram-mcp-phase-4-design.md` (revision 3), `AGENT.md`, `CHANGELOG.md`.
- **Verification:** Scratch dry run: the new tests for Tasks 4–17 passed; the full suite showed 770 passed, and its failures were only those expected from unapplied Tasks 1–2 and the unrebuilt Swift agent. Telethon 1.45.0 signatures were read from the installed package. `main` suite unchanged.
- **Follow-ups:** An owner-requested simurgh gauntlet of this plan next, then execution inline (no subagents). The owner must create the Keychain item, and supply the `api_id` and Test DC IP for the opt-in Test DC run. No Telegram access, no production claim.

### 2026-09-23 (Australia/Sydney)
**Raouf:**
- **Scope:** Simurgh gauntlet of the Phase-4b plan, revision 1 → revision 2. Plan only; no product code on `main`.
- **Summary:** Applied the whole plan to a scratch copy of `main`, including the Swift renderer, the recorder and the smoke rows, and attacked it by execution. Eight defects were fixed in the plan text:
  - The plan's code failed its own commit gate: 7 ruff and 6 mypy errors.
  - Literal bidi and invisible characters were embedded in the plan's code (a Trojan-Source hazard).
  - A deleted message in a full history page silently ended pagination.
  - An unreachable Telegram at start crashed the daemon and took the admin socket with it.
  - An unauthorised session still reached a Touch ID prompt.
  - `auth logout-local` left the auth key in the client's memory. Proven against real Telethon: the next connect would sign back in.
  - The smoke pinned the admin-router order that the plan changes.
  - The Swift instruction invited deleting the `isInvisible` line.

  Five residuals are recorded as owner rulings in the plan's gauntlet record.
- **Files changed:** `docs/superpowers/plans/2026-09-23-telegram-mcp-phase-4b-adapter-and-tools.md`, `AGENT.md`, `CHANGELOG.md`.
- **Verification (scratch copy, fully applied):**
  - `pytest tests`: 825 passed, 8 skipped.
  - join gate: 16 passed, 1 skipped.
  - smoke: 49/49.
  - ruff check and format: clean.
  - mypy: clean over 85 files.
  - Each defect was shown failing by a probe before its fix.
  - `main` suite unchanged (706 passed, 8 skipped).
- **Follow-ups:** Owner review of revision 2 and the five residual rulings. Then inline execution. The Test DC run stays owner-only (needs the Keychain item, `api_id` and the Test DC IP).

### 2026-09-23 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase-4b plan revision 4, fixing an external gauntlet's eight findings. Plan and design only; no product code on `main`.
- **Summary:** Each finding was verified before any change: all eight held against the pinned Telethon source, the plan and the spec. The verification found more. `send_code_request` sends the prohibited `auth.ResendCodeRequest` when a code hash is cached, and Telethon's `_call` sleeps and sends hidden RPCs.
  - **Wire (G-1, G-2):** the adapter now owns the wire. `_GatewayClient._call` sends one request per call, with no retry or sleep, under a per-operation allowlist and work budget. Login is raw reviewed requests, and the executor is private.
  - **Admin approvals (G-3):** they now bind keyed secret digests, and tokens are bound to their exact request.
  - **Pagination (G-4):** keyset with constant state; the 1,024-chat silent stop is gone.
  - **Retryability (G-5):** `results.RETRYABILITY` is §27.1 verbatim and the only registry.
  - **Witness (G-6):** widened to DM and group markers, channel views and unread counts.
  - **Daemon (G-7):** a production-shape admin-socket mode.
  - **Estimator (G-8):** a seeded adversarial invariant proves the reservation dominates Phase 3's exact measurement; a control run shows it bites.
- **Files changed:** `docs/superpowers/plans/2026-09-23-telegram-mcp-phase-4b-adapter-and-tools.md`, `docs/superpowers/specs/2026-09-23-telegram-mcp-phase-4-design.md`, `AGENT.md`, `CHANGELOG.md`.
- **Verification (whole plan applied to a scratch copy of `main`):**
  - pytest: 847 passed, 10 skipped
  - smoke: 49/49
  - join gate: 16 passed, 1 skipped
  - ruff, format and mypy: clean
  - session-file ignores: checked in the real repository
  - `main` suite unchanged.
- **Follow-ups:** Execute revision 4 inline, per the owner. The Test DC run and the installed-host boundary check remain owner-run.

### 2026-09-23 (Australia/Sydney)
**Raouf:**
- **Scope:** Execute the Phase-4b plan (revision 4) inline on branch `phase-4b`, Tasks 1–18.
- **Summary:** All eighteen tasks were done test first, each watched red before green and each committed on a green full gate.
  - **Authority:** an empty owner allowlist now denies. Prompt-unsafe names are refused at creation and stripped by the agent's renderer.
  - **Admin approvals:** gated admin commands are approved with Touch ID on the consent wire, with secrets bound by keyed digest and tokens bound to their exact request.
  - **Identity:** principal, account and clients are bootstrapped for real.
  - **Telegram boundary:** deadlines, work budgets and fair admission; the Keychain `api_hash`; the ref store. A Telethon adapter owns the wire: one send per request, per-operation allowlist and budget, raw reviewed login, no Telethon helpers.
  - **Operator commands:** dialog discovery; three-step login; scope and allowlist.
  - **Exposure:** worst-case bounds with a 48 KiB page cap.
  - **Authority and coordinator:** the project-scoped authority snapshot; an authority re-check before retrieval with typed refusals and the §27.1 retryability registry.
  - **Reads:** history views, and the four reads with keyset pagination.
  - **Runtime:** the alias-resolving RPC guard, composition, `telegram-mcp daemon` with a production-shape admin-socket mode, the Test DC harness, the runtime recorder, the widened read-state witness, smoke rows and evidence.

  Control runs: a planted `ReadHistoryRequest` made both RPC guards fail, and a weakened estimator made all four exposure-invariant cases fail.
- **Files changed:**
  - **src — authority, consent, IPC:** `authority/policy.py`, `storage/{authority_view,identity,refstore}.py`, `ipc/admin.py`, `ipc/handlers/{projects,clients,auth,scope}.py`, `consent/admin_approval.py`.
  - **src — Telegram and disclosure:** `telegram/{deadline,errors,telethon_adapter,discovery,reads}.py`, `keys/keychain.py`, `disclosure/{bounds,seams,coordinator}.py`, `results.py`, `sensitive_dispatch.py`.
  - **src — runtime:** `runtime/{composition,daemon}.py`, `cli.py`.
  - **Agent:** `agent/consent-agent.swift`.
  - **Tests:** the new tests under `tests/`, plus `tests/telegram/`.
  - **Other:** `scripts/e2e_smoke.py`, `pyproject.toml`, `docs/verification/{phase-4,telegram-rpc-review}.md`, `CLAUDE.md`, this file, `CHANGELOG.md`/`AGENT.md`.
- **Verification:**
  - pytest: 860 passed, 10 skipped (the Test DC and host-gated tests skip honestly).
  - smoke: 49/49.
  - join gate: 16 passed, 1 skipped.
  - formal model: 624/624.
  - ruff and format: clean.
  - mypy: clean over 85 files.
  - `uv build`: OK.
- **Follow-ups:**
  - The final whole-branch review (self-review; no subagents, per the owner).
  - Owner-run: the Test DC run, the Touch ID test, and the installed-host boundary check.
  - Re-package the signed agent bundle with the renderer fix.
  - The merge decision (owner).
  - No production claim.

### 2026-09-23 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase-4b final whole-branch review and its fix pass, on branch `phase-4b`.
- **Summary:** This was a self-review, because the owner's rule is no subagents: the code-reviewer checklist over the whole branch diff, plus executed probes. The shipped daemon CLI fails closed when unpaired, before it creates state or reads the Keychain.

  One Important finding was fixed test first. `_call_reviewed` carried an `isinstance(self._client, _GatewayClient)` branch that existed only for injected test clients, which breaks the "no test-only flags in production paths" rule. Charging is now uniform: the session always charges its own request and marks it pre-charged, and the client charges anything else.

  Five minors are deferred to the owner and listed in the ledger.
- **Files changed:** `src/telegram_mcp/telegram/telethon_adapter.py`, `tests/unit/test_gateway_client.py`, `AGENT.md`, `CHANGELOG.md`.
- **Verification:** the new source guard went red, then green. Full gate: pytest 862 passed, 10 skipped; smoke 49/49; formal 624/624; ruff, format and mypy clean; build OK.
- **Follow-ups:**
  - The owner decides the merge and push; a self-review is weaker than a fresh reviewer.
  - Owner-run: the Test DC run, the Touch ID test, and the installed-host boundary check.
  - Re-package the signed agent bundle.

### 2026-09-23 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase-4c implementation plan and design revision 5 (§4.7), on branch `phase-4c`.
- **Summary:** The plan (`docs/superpowers/plans/2026-09-23-telegram-mcp-phase-4c-context-and-search.md`) covers `get_context`, `search_messages`, `cross_project_search`, the continuation engine, §23D coverage and the Test DC qualification harness, in 8 tasks. It is a transcription of code that already ran. The code was written and tested in a scratch copy of `main` at `cd1434e` (1290 passed, 10 skipped; smoke 53/53). It was then replayed task by task onto a fresh export, with each task's tests, ruff, format, mypy, the full suite and the smoke run at every stage; all 8 stages were green. The plan text itself was then replayed mechanically onto a third export: 34 blocks applied with zero fuzz, and the 33 files are byte-identical to the tested tree. Design §4.7 records the 11 refinements that execution forced (nearest-neighbour context windows, byte-accounted search pages, response attribution checks and others).
- **Files changed:** `docs/superpowers/plans/2026-09-23-telegram-mcp-phase-4c-context-and-search.md` (new), `docs/superpowers/specs/2026-09-23-telegram-mcp-phase-4-design.md`, `AGENT.md`, `CHANGELOG.md`.
- **Verification:** the staged proof table is in the plan. Full gate on this commit (docs only): pytest 862 passed, 10 skipped; smoke 49/49; formal 624/624; ruff, format and mypy clean; contracts check and build OK.
- **Follow-ups:**
  - The line-by-line gauntlet of the plan (owner request).
  - Inline execution.
  - Owner-run: the Test DC run, including the 4c forum and search cases; the dedicated-account qualification; the Touch ID test; the installed-host check; re-packaging the signed agent.

### 2026-09-23 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase-4c plan, revision 6: a line-by-line gauntlet, then an external review, on branch `phase-4c`.
- **Summary:** The gauntlet read the plan against the frozen spec, the pinned Telethon source and the tested tree, and ran probes against it. The owner then pasted an external review of the first transcription, and each claim was checked before anything changed. Every confirmed defect was fixed test first in the scratch tree: each new test failed before its fix, and four guard tests also had control runs. The confirmed defects:
  - **Paging ended too early.** A short page was taken as the end. Telethon 1.45.0 `client/messages.py:213-225` documents that it is not. This affected both search and 4b history.
  - **Offsets and coverage were unreliable.** A foreign entry could steer the next offset. Uncertainty was forgotten between pages. `peers_scanned` counted peers merely started. The coverage counters were guessed rather than measured.
  - **Two work bounds were enforced nowhere, including 4b.** One is the 10-search-page bound; the other is the 32,000-codepoint cap on combined text.
  - **Owner-excluded peers were counted as eligible.**
  - **Continuations were slow.** A continuation made one dialog request per resumed peer.
  - **The Test DC run would have failed at its first read,** because of a stale chat-kind equality.
  - **Smaller defects:** a lowercase `z` in RFC 3339 times and a fractional `until`; the epoch `since` edge; forum classification; an empty cursor entry; query-canary coverage of logs and files.

  The plan was re-proved: every task was replayed onto a fresh `main` export with its own tests, lint, types, the full suite and the smoke, and all 48 checks were green. The plan text was then replayed mechanically: 38 blocks applied with zero fuzz, and the 36 files are byte-identical to the tested tree. Design §4.7 records every rule change.
- **Files changed:** `docs/superpowers/plans/2026-09-23-telegram-mcp-phase-4c-context-and-search.md`, `docs/superpowers/specs/2026-09-23-telegram-mcp-phase-4-design.md`, `AGENT.md`, `CHANGELOG.md`.
- **Verification:** staged proof: 1333 passed, 10 skipped (plus the 4 git-only tests in the repo); smoke 53/53 at Task 8. Full gate on this commit (docs only): pytest 862 passed, 10 skipped; smoke 49/49; formal 624/624; ruff, format and mypy clean; contracts check and build OK.
- **Follow-ups:**
  - Inline execution.
  - Deferred minors: the exposure-invariant `_rehome` truncates a shared list in place; the universe digest binds the candidate universe.
  - Owner-run: the Test DC run (now including real paging), the dedicated-account qualification, the Touch ID test, the installed-host check, and re-packaging the signed agent.

### 2026-09-23 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 4c executed inline from the revision-6 plan, on branch `phase-4c` (8 tasks).
- **Summary:** `telegram_get_context`, `telegram_search_messages` and `telegram_cross_project_search` are served through ingress, consent and the coordinator on the fake transport; all nine sensitive tools are now served. Each task's red and green outputs, and its full-gate counts, matched the plan exactly, with no rulings needed. Control runs: the estimator weakening (3 invariant cases failed), and a planted query log line and query `INSERT` (the canary test failed in the log and in `meta.db-wal`). Evidence is in `docs/verification/phase-4.md` §4c; the two new RPC reviews are in `telegram-rpc-review.md`.
- **Files changed:**
  - **src:** `authority/{cursors,policy}.py`, `telegram/{telethon_adapter,search,reads}.py`, `disclosure/{bounds,search_authority,seams,coordinator}.py`, `storage/refstore.py`, `validation.py`, `consent/display.py`, `runtime/composition.py`.
  - **Tests:** new and updated tests under `tests/`, including `tests/telegram/`.
  - **Other:** `scripts/e2e_smoke.py`, `docs/verification/{phase-4,telegram-rpc-review}.md`, `CLAUDE.md`, `AGENT.md`, `CHANGELOG.md`.
- **Verification:** pytest 1337 passed, 10 skipped; smoke 53/53; formal 624/624; ruff, format and mypy clean (87 files); contracts check and build OK.
- **Follow-ups:**
  - The final whole-branch review (self-review; no subagents, per the owner).
  - Owner-run: the Test DC run (forum, edges, real paging and exhaustion), the dedicated-account qualification, the Touch ID test, and the installed-host check.
  - Re-package the signed agent.
  - A forum read-marker witness.
  - The merge decision (owner).
  - No production claim.

### 2026-09-23 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 4c landed on `main`.
- **Summary:** Branch `phase-4c` was pushed, merged into `main` with `--no-ff` as `0a11bb9` (11 commits since `cd1434e`), and `main` was pushed. The merged tree is identical to the gated branch head `1741ff9`. There are 9 sensitive tools and all are served on the fake transport. Real Telegram behaviour is owner-pending. `CLAUDE.md` and `docs/verification/phase-4.md` now say 4b and 4c are on `main`. The decisions and claims are recorded in Zurvan under the tag `telegram-mcp`.
- **Files changed:** `CLAUDE.md`, `docs/verification/phase-4.md`, `AGENT.md`, `CHANGELOG.md`.
- **Verification:** the full gate on the merged tree (below): pytest 1337 passed, 10 skipped; smoke 53/53; formal 624/624; ruff, format and mypy clean (87 files); contracts check and build OK.
- **Follow-ups:**
  - Owner-run: the Test DC run (forum, edges, real paging and exhaustion), the dedicated-account qualification, the Touch ID test, and the installed-host check.
  - Re-package the signed agent.
  - A forum read-marker witness.
  - Deferred minors: the conservative ten-page check; the exposure test's in-place `_rehome`; the candidate-universe digest.
  - Next: Phase 5 per the roadmap.
  - No production claim until Gates A–R pass for the exact artifact.

### 2026-09-24 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 5 design (operator controls, retention and recovery). Design only; no code.
- **Summary:** Brainstormed Phase 5 with the owner in four reviewed sections and wrote `docs/superpowers/specs/2026-09-24-telegram-mcp-phase-5-design.md` revision 1.
  - **Decomposition:** 5a inspect surface and one policy engine; 5b lifecycle, key rotation, retention, recovery; 5c backup, import, runbooks.
  - **Decisions:** in-tree age-v1 (X25519 subset) on `cryptography`; purges both scheduled and manual; `auth.LogOutRequest` as the single admin-only RPC; seven rotatable key purposes (principal refused); SAVEPOINT dry-run for simulate/diff/import; the recovery identity is used in daemon memory and never stored.
  - **Review:** 24 findings folded in and checked against the spec and the code.
  - **Self-review defect:** the draft sent up to 4 MiB in one admin frame against the 64 KiB codec cap (`ipc/framing.py:31`); replaced by chunked transfer.
  - **Shipped defect found:** `telegram/telethon_adapter.py:131-138` maps `UserDeactivated*` to `SESSION_REVOKED`; it should be `ACCOUNT_UNAVAILABLE`. Fixed in 5b.
- **Files changed:** `docs/superpowers/specs/2026-09-24-telegram-mcp-phase-5-design.md`, `AGENT.md`, `CHANGELOG.md`.
- **Verification:** Every design claim that cites code was checked by `grep`/`sed` at `9eec78e`: handler transactions, `PRESENCE_GATED` (31 names), the class filter location, the `accounts` / `mcp_clients` schema, the `ADMIN_EVENTS` vocabulary, the key store layout, the frame cap, and spec line 1310. Placeholder scan clean. No tests run (no code changed).
- **Follow-ups:**
  - The owner's line-by-line gauntlet of the design.
  - Then writing-plans for 5a.
  - No production claim.

### 2026-09-24 (Australia/Sydney)
**Raouf:**
- **Scope:** Gauntlet of the Phase 5 design (simurgh-arise), producing revision 2. Design only.
- **Summary:** Checked revision 1 (`1e21caf`) against the shipped code by reading the named lines and running one probe. Found 16 defects (2 blockers, 5 high, 5 medium, 4 low), each fixed in place and listed in §0B.
  - **G1 (blocker):** the purge order broke `audit_events.disclosure_ref → disclosure_receipts` (`migrations.py:369`). The audit prefix now goes before receipts, and receipt retention becomes a floor.
  - **G2 (blocker):** `verify_chain` cannot accept a second epoch or a truncated prefix, proven by `docs/verification/probes/phase5_chain_epochs_probe.py` (both are rejected with "chain sequence is not continuous"). New §3.8 adds epoch sealing and a root-aware verifier, to be built before rotation, restore or purge.
  - **High:** G3, `privacy-key` rotation would reset exposure-budget buckets, a hard-limit bypass; G4, network handlers cannot live inside one transaction wrapper; G5, audited admin events need the append guard and an anchor refresh, and lock/unlock append nothing today; G6, admin Touch ID prompts show only the command name.
  - **Medium and low:** `consent approve` deferred as a spec "MAY"; an active-account pointer; `key_slots` next to the existing `verification_keys`; the hourly cursor GC, which has no caller today; `client rotate` re-enabling a disabled client; plus four precision fixes.
- **Files changed:** `docs/superpowers/specs/2026-09-24-telegram-mcp-phase-5-design.md`, `docs/verification/probes/phase5_chain_epochs_probe.py`, `AGENT.md`, `CHANGELOG.md`.
- **Verification:**
  - The probe was run from the repo: epoch 1 verifies, and cases (a) and (b) are both rejected.
  - Ruff check and format are clean on the probe.
  - Every §0B row cites a file:line or the probe.
  - No product code changed.
- **Follow-ups:**
  - The owner's review of revision 2, then writing-plans for 5a.
  - Shipped gaps recorded for 5a/5b: G10 (no hourly cursor GC), G11 (`client rotate` re-enables), and the §3.2 `UserDeactivated` mapping.
  - No production claim.

### 2026-09-24 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 5a implementation plan written, then gauntleted line by line. Plan only; no product code.
- **Summary:** `docs/superpowers/plans/2026-09-24-telegram-mcp-phase-5a-operator-surface.md` has 11 TDD tasks:
  1. Composable audit and storage primitives.
  2. Transaction runners: tx, audited tx, and simulation.
  3. Existing handlers moved onto the runners, with two AST guards; fixes the duplicate `scope mode` and `client rotate` re-enabling a disabled client.
  4. One evaluator with traces, and the owner class rule moved into `authority/`.
  5. `EffectiveAccess`.
  6. `tps_` staging, and `policy explain/simulate/diff`.
  7. Audited lock/unlock, and audit verify/checkpoint/repair-anchor.
  8. Inspect commands and the lineage seam.
  9. The remaining project/scope/client commands.
  10. Touch ID summaries (G6).
  11. Composition, completeness by name, the `serve` verb, smoke, evidence.

  The gauntlet ran checks against `6680090` and found 15 defects, all fixed in place. The two high ones:
  - **P1:** lock contention surfaced as `OperationalError` → `INTERNAL_ERROR`. Measured at about 5.2 s; now mapped to a fixed `BUSY` refusal.
  - **P2:** after a failed anchor refresh, the audited-command recovery path (every audited command refused until repair-anchor) was untested and undocumented.

  Both predicted guard results were confirmed by running the guards.
- **Files changed:** the plan, `AGENT.md`, `CHANGELOG.md`.
- **Verification:**
  - Both proposed AST guards ran against today's tree.
  - The SAVEPOINT sequence and busy-lock behaviour were probed on the real `open_db`, along with the anchor-failure exception.
  - All 51 python blocks parse.
  - No tests changed.
- **Follow-ups:**
  - The owner's review of the plan, then native execution on branch `phase-5a` (no subagents, per the owner).
  - 5b and 5c plans after 5a merges.
  - No production claim.

### 2026-09-24 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 5a plan revision 3, from the owner's review of revision 2.
- **Summary:** 12 findings plus the presence-by-count note were each checked against the code. Ten were adopted:
  - live `PeerFacts` go through the one evaluator (new Task 4A);
  - a semantic simulate-equals-commit covers all 16 simulatable commands, including remove-peer and the ref-minting commands;
  - bound, capped `tps_` stages;
  - registry-based checkpoint verification, with `none` distinct from `verified`;
  - `disclosure show`/`verify` keep the Phase-3 withheld truth;
  - conditional `owner_class` rows that are diffed;
  - optional `exposure status` filters;
  - a failed rotation has no side effect;
  - strict list commands and overlap arguments;
  - presence pinned by name.

  Two claims were rejected with receipts:
  - `take()` does not consume valid handles (`discovery.py:46-51`); the review's test was adopted anyway.
  - `consent approve` stays deferred per the approved design rev 2 G7, and the reason is recorded in the completeness test.
- **Files changed:** the plan, `AGENT.md`, `CHANGELOG.md`.
- **Verification:** 64 code blocks parse; the new class guard flags exactly the four `reads.py` sites Task 4A removes; no placeholders.
- **Follow-ups:** execute inline on `phase-5a` in the order 1, 2, 3, 4, 4A, 5, 9, 6, 7, 8, 10, 11.

### 2026-09-24 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 5a executed inline from plan revision 3, on branch `phase-5a` (12 tasks: 1–11 plus 4A).
- **Summary:** The operator surface and the one policy engine are wired through the daemon:
  - three transaction-runner kinds, with a fixed BUSY refusal on lock contention;
  - an audited lock/unlock that refreshes the anchor and has a tested degraded-recovery path;
  - one evaluator with traces, and retrieval deciding chat class through it with live facts (4A);
  - metadata-effective access with conditional `owner_class` rows;
  - `policy explain/simulate/diff`, with semantic simulate-equals-commit over all 16 simulatable commands and bound, capped `tps_` stages;
  - checkpoint verification by recorded key (`none` distinct from `verified`);
  - withheld-disclosure truth in `disclosure show/verify`, and optional `exposure status` filters;
  - rename, members, remove-peer, overlap, instruction, cross-search grants, `scope remove`, `client disable`;
  - Touch ID summaries of at most 160 codepoints;
  - completeness pinned by name, and a pinned `serve` verb.

  Five shipped defects were fixed (see `docs/verification/phase-5.md` §5a.3), and four shipped gaps were recorded for 5b (§5a.4).
- **Rulings:**
  - `ADMIN_EVENTS` was already exported (plan P13 was wrong).
  - One commit (7dd7c79) landed on a red lint gate. It was fixed in the next commit, and every later commit went through a fail-fast gate script.
  - Test adjustments: C408, RUF059, and PLE2502 (a literal U+202E, now an escape).
  - The Task 3 command set was extended for Task 9.
  - Task 6's test count is 25, not 26.
  - The production `assert` became an explicit fail-closed raise.
- **Files changed:** `src/telegram_mcp/{authority/{policy,effective,staging}.py, storage/{db,refstore,authority_view,effective_access}.py, disclosure/{coordinator,seams,search_authority,keys,lineage}.py, disclosure/audit/{chain,anchor}.py, telegram/reads.py, ipc/admin.py, ipc/handlers/{_wrapper,projects,scope,clients,policy,audit,inspect}.py, consent/{admin_summaries,admin_approval}.py, runtime/composition.py, cli.py}`, the new tests, `scripts/e2e_smoke.py`, `docs/verification/phase-5.md`, `AGENT.md`, `CHANGELOG.md`.
- **Verification:** the full gate on `phase-5a`:
  - `uv sync --locked` OK; contracts check OK (23 files);
  - pytest 1493 passed, 10 skipped; smoke 60/60; formal 624 states, 18 assertions;
  - ruff, format and mypy clean (96 source files); build OK.
- **Follow-ups:**
  - Self-review of the whole branch (no subagents, per the owner).
  - The merge decision (owner).
  - The 5b plan.
  - No production claim.

### 2026-09-24 (Australia/Sydney)
**Raouf:**
- **Scope:** Comms consolidation design (the Phase 5b-0 contract and the 5b→5e sequence). Design only.
- **Summary:** The owner decided that this repo becomes `comms`, merging Telegram and WhatsApp.
  - **Owner decisions:** WhatsVault moves in (git subtree, full history, pinned `b6fd51a`); no Touch ID anywhere, effective only at 5b-3; Telegram sends by bot or user session per destination; WhatsApp sends through the Cloud API.
  - **Sequence:** 5b-1 rename/restructure (mechanical) → 5b-2 WhatsVault import (intact) → 5b-3 comms spec v0.2 (the only semantic change) → 5b-4 campaign core on fakes → 5c lifecycle/audit hardening → 5d real sends → 5e operator and AI surfaces.
  - **Owner amendments:** all seven folded in (thin `telegram_mcp` forwarder; static and dynamic core→transport guard; no-squash subtree with tree-hash proof; SQLCipher native provenance; governance precedence; presence frozen through 5b-2; out-of-repo operations outside every gate).
  - **Added:** AST equivalence modulo import paths as 5b-1's proof of "no semantic diff".
- **Files changed:** `docs/superpowers/specs/2026-09-24-comms-consolidation-design.md`, `AGENT.md`, `CHANGELOG.md`.
- **Verification:**
  - Baselines measured: Telegram 1493 passed / 10 skipped plus smoke 60 at `255b8e8`; WhatsVault 539 passed at `b6fd51a` (the result dots were counted, because its config hides the summary line).
  - WhatsVault CI covers Python 3.12 on macOS.
  - Every protocol constant named in §2.1 was grep-confirmed.
  - Only the console script and `python -m telegram_mcp.cli` reference the module path.
- **Follow-ups:**
  - The 5b-1 plan.
  - The out-of-repo steps (GitHub rename, archive, folder and memory move) come up for separate approval.
  - No production claim.

### 2026-09-24 (Australia/Sydney)
**Raouf:**
- **Scope:** Comms 5b-1, rename and restructure (mechanical), on branch `comms-5b1`.
- **Summary:** The Telegram implementation was relocated from `telegram_mcp` to `comms.transports.telegram` by pure prefix move.
  - `comms.core` exists, is empty and is guarded by permanent layering tests (static and dynamic imports, transport strings, transport isolation, and a planted-violation proof).
  - `telegram_mcp` is a two-file CLI forwarder; the `telegram-mcp` and `comms` console scripts both work.
  - **Proof:** AST equivalence against BASE `27af242` (96 modules, 23 data files, order-sensitive); the 29 protocol constants are unchanged; `uv.lock` is unchanged.
  - **Plan method:** the plan was dry-run in a throwaway worktree before execution (8 defects fixed first). Execution found one more, which the dry run had masked (see the rulings).
- **Rulings:** `sys.modules[__name__]` for the planted test; a fail-closed existence check on the isolation guard; the entry-point test excluded from the leftover-literal scan.
- **Files changed:**
  - moved: `src/telegram_mcp/**` → `src/comms/transports/telegram/**`;
  - new: `src/comms/{__init__,core/__init__,transports/__init__}.py`, `src/telegram_mcp/{__init__,cli}.py`, `scripts/migration/*`, the 4 new test files, `tests/fixtures/migration/protocol_constants.json`, `docs/verification/comms-5b1.md`;
  - edited: `pyproject.toml`, `SECURITY-MANIFEST.json`, `.gitignore`, `CLAUDE.md`, `tests/agent/stub_broker.py` (docstring), `AGENT.md`, `CHANGELOG.md`;
  - plus import-only rewrites across `src`, `tests` and `scripts`.
- **Verification:**
  - `uv sync --locked` OK; contracts OK (23 files);
  - pytest 1514 passed, 11 skipped; smoke 60/60; formal 624/18;
  - ruff and format clean (231 files); mypy clean (101 files); build OK.
- **Follow-ups:**
  - The merge decision for `comms-5b1`.
  - Then the 5b-2 plan (the WhatsVault subtree import at `b6fd51a`).
  - The out-of-repo steps (GitHub rename, folder and memory move) remain separately approved.
  - No production claim.

### 2026-09-24 (Australia/Sydney)
**Raouf:**
- **Scope:** Comms 5b-2 — WhatsVault imported intact (branch `comms-5b2`).
- **Summary:** WhatsVault `b6fd51a` imported with full history under `transports/whatsapp/` via the subtree merge (`git subtree` not installed); prefix tree proven equal to the source tree and pinned by tests; importable as `whatsvault`; four runtime deps pinned to WhatsVault's measured versions — lock additions only (12 packages, 0 changes), `pip-audit` clean; SQLCipher arrives as a hash-pinned cp312 arm64 wheel bundling 4.12.0 (design's Homebrew assumption superseded, recorded). Spike-first again: the plan was written from a throwaway-worktree run.
- **Files changed:** `transports/whatsapp/**` (imported, unedited), `tests/integration/test_whatsvault_provenance.py`, `pyproject.toml`, `uv.lock`, `docs/provenance/whatsvault.md`, `docs/verification/{dependencies,comms-5b2}.md`, `docs/superpowers/plans/2026-09-24-comms-5b2-whatsvault-import.md`, `CLAUDE.md`, `AGENT.md`, `CHANGELOG.md`.
- **Verification:** Telegram 1519 passed/10 skipped, smoke 60/60, formal 624/18, ruff/format/mypy clean, build OK (wheel includes whatsvault); WhatsVault 539 passed under its own strict config in the shared Python 3.12 venv.
- **Follow-ups:** merge decision; next is design §3.4 seams or 5b-3 (spec v0.2); out-of-repo steps (GitHub rename, whatsvault archive, folder/memory move) remain separately approved. No production claim.

### 2026-09-24 (Australia/Sydney)
**Raouf:**
- **Scope:** Comms 5b-3 — owner-direct authority, comms spec v0.2 (branch `comms-5b3`).
- **Summary:** The consent subsystem is deleted, not emulated. The disclosure pipeline makes one ledger consult immediately before reserve (the soft-threshold decision point), binds the reservation to the call (`comms-call-binding/v1`), and writes explicitly versioned v2 receipts (`owner_direct`, `soft_threshold_exceeded`, no consent fields); every v1 receipt stays byte-identical and verifies, dispatched on `proof_version`. JCS moved as the single copy to `canonical.py`. Admin authority is peer credentials alone. The Swift agent, broker, prompter, display, gate, RV-1, pairing, admin approver/summaries, `pair` verb, consent socket and launcher agent job are gone; the three consent keys are retired, not erased. The formal model has six replacement invariants (544 states, 22 assertions), each mutation-tested; its first run found a real gap, now guarded. Retired identifiers are tombstoned. `docs/comms-spec-v0.2.md` is the normative delta; the AI boundary is stated exactly.
- **Files changed:** `src/comms/transports/telegram/{canonical,cli,doctor,opaque}.py`, `disclosure/{budget,coordinator,receipts,seams,verify}.py` (`exposure.py` deleted), `storage/migrations.py`, `ipc/{admin,__init__}.py`, `ipc/handlers/*`, `keys/{registry,store,__init__}.py`, `runtime/{bootstrap,composition,daemon,lifecycle}.py`, `authority/refs.py`, `contracts/{meta,manifest}.json`; deleted `consent/`, `ipc/rendezvous.py`, `keys/pairing.py`, `agent/`, `scripts/{package_agent.sh,consent-agent.plist}`; `scripts/{extract_contracts.py,e2e_smoke.py,install_paths.sh}`; `formal/{model.py,README.md}`, `SECURITY-MANIFEST.json`; tests throughout (new: `test_canonical`, `test_receipt_migration`, `test_receipts_v2`, `test_owner_direct_disclosure`, `test_receipt_v2_meta`, `test_invariant_mutations`, `test_admin_peer_authority`, `test_retired_keys`, `test_ai_boundary`, `test_tombstones`, `test_test_accounting`); `.claude/settings.json`; `docs/comms-spec-v0.2.md`; `docs/verification/comms-5b3{.md,-collected-before.txt,-collected-after.txt,-classification.json}`; `CLAUDE.md`, `AGENT.md`, `CHANGELOG.md`.
- **Verification:** Full gate at branch head: contracts OK (23 files); Telegram 1427 passed/4 skipped; smoke 52/52; formal 544 states, 22 assertions; ruff/format/mypy clean; build OK; WhatsVault 539 passed. Test accounting: 196 IDs left the collection, 183 removed with consent and 13 replaced by named owner-direct tests, 0 unexplained (pinned by a test).
- **Follow-ups:** merge and push need the owner's approval. Runbook (owner-approved, outside every gate): delete the paired Secure Enclave approval key and pins, uninstall the old consent bundle and LaunchAgent. Next: 5b-4 campaign core. Deferred minor: intermittent uvicorn lifespan traceback at smoke shutdown. No production claim.

### 2026-09-24 (Australia/Sydney)
**Raouf:**
- **Scope:** Comms 5b-4: the campaign core, fake transports only (branch `comms-5b4`). Design rev 4, plan rev 3, executed inline and test-first.
- **Summary:** `comms.core` now holds a transport-neutral campaign core.
  - **Primitives.** `canonical` and `opaque` moved in as single copies, alongside `refs`, `timeutil` and `domains`.
  - **Storage.** A SQLCipher `comms.db` that fails closed: a wrong, short or missing key, or a plaintext file, is refused. Migrations are atomic. Schema v1 carries identity/origin binding triggers and immutability.
  - **Directory.** Delivery identities are shared between endpoints: a user and their private chat are one identity, and Telegram peer kinds are marked.
  - **Resolution.** Every target resolves with its origin paths.
  - **Events.** A typed, finite-domain event log.
  - **Transport contract.** No I/O in `prepare` or `still_valid`; `ResultKind` semantics are pinned.
  - **Reducer.** One reducer writes job state. The exhaustive table names the two descents, the claim and its attempt are atomic, and early provider updates are reconciled on bind.
  - **Freeze.** Generations, idempotency keys and three distinct digests.
  - **Engine.** Only an exception raised by `deliver` becomes an outcome.
  - **Operations.** Cancel, retry, resolution, and provider updates with `pending_match`.
  - **Scheduling and recovery.** `run_due` enforces the time gate. `recover` repairs state and never resends.
  - **Formal.** A bounded model (96,528 states, 11 properties, each mutation-tested) and a 300-sequence differential walk against the library.
- **Files changed:**
  - `src/comms/core/{canonical,opaque,refs,timeutil,domains}.py`, `storage/`, `campaigns/{directory,resolve,drafts,events}.py`, `delivery/{transport,reducer,freeze,engine,operations,scheduling,recovery}.py`;
  - `formal/campaign_model.py`, `pyproject.toml`;
  - 31 importers repointed; `tests/core/*`;
  - `tests/security/{test_comms_wire_frozen,test_comms_layering,test_ai_boundary}.py`, `tests/unit/{test_core_moves,test_canonical}.py`, `tests/formal/test_campaign_model{,_mutations}.py`;
  - the design (rev 4) and the plan (rev 3), `docs/verification/comms-5b4.md`, `CLAUDE.md`, `AGENT.md`, `CHANGELOG.md`.
- **Verification:** Full gate at branch head:
  - Telegram 2215 passed, 4 skipped; smoke 52/52;
  - formal: 544/22, plus the campaign model at 96,528 states and 11 properties;
  - ruff, format and mypy (110 files) clean; build OK;
  - WhatsVault 539 passed, and its subtree is byte-identical to `main`;
  - the freeze of 5,000 endpoints takes 341 ms (budget 3 s);
  - the differential walk runs 3,803 steps with a planted-defect teeth test;
  - every source §31 line is mapped to an existing test, and a test pins that.
- **Follow-ups:**
  - The owner must rule on a spec contradiction: §5.4's snapshot preimage includes identities, while §9/R22 require opaque-ref-only digests in events. The implementation keeps the snapshot digest out of events.
  - The model bound is 2 jobs; 3 exceeds the 60 s ceiling.
  - Merging and pushing need the owner's approval.
  - Next phases: 5c (chain-backed audit), 5d (real adapters and webhooks), 5e (CLI/admin and the key supply).
  - No production claim.

### 2026-09-24 (Australia/Sydney)
**Raouf:**
- **Scope:** Comms v0.3 Part A — the constitutional cutover (branch `comms-v0.3`), plan rev 2 executed inline and test-first (tasks A1–A23; A18 merged into A11).
- **Summary:** One profile-parameterised audit chain engine and one anchor engine now live in `comms.core`; the legacy Telegram chain is a thin binding that reproduces its byte vectors. `comms.db` v2 adds the comms audit chain, the integrity latch, lineage and an exact-next-state cutover table. `AuditWriter` commits and then anchors exactly that head under one lock. The resumable cutover drains, verifies, seals (DB-enforced), anchors, writes lineage and the `system.audit_cutover` genesis, and retires `tgml1` (DB-enforced, epoch bumped once); 12 crash boundaries converge. `verify_all` walks legacy (from a signed root) → seal → lineage → genesis → comms chain → anchor and fails closed. The Telegram MCP surface, disclosure-on-read, project/grant/scope/policy authority, exposure budget and `tgml1` issuance leave production (the daemon serves admin only; 29 admin commands answer `RETIRED_IN_V0_3`; `demo`/`serve` exit 8); `legacy_composition` keeps them as a historical harness for retained tests. `legacy_verify` verifies v1/v2 receipts, the legacy chain and key coverage without the retired engine. WhatsVault's `apps/mcp` is removed (R-A16). A seed `TOOL_CATALOG` with closed `comms_*` dispatch and a v0.3 AI-boundary suite (owner_full_admin) are in. Rulings R-000–R-008, R-A16, R-A20 are registered.
- **Files changed:** `src/comms/core/{audit/*,keys/{ids,slots}.py,storage/migrations.py,strict_json.py,validators.py,refs.py,domains.py}`, `src/comms/mcp/*`, `src/comms/services/registry.py`; Telegram `cli.py`, `contract.py`, `authority/__init__.py`, `disclosure/{audit/*,keys.py}`, `ipc/{admin,leases}.py`, `ipc/handlers/{exposure,inspect,audit,leases}.py`, `legacy_verify/`, `runtime/{composition,legacy_composition,daemon,cutover_barrier,identity}.py`, `storage/{migrations,settings,owner_state,authority_view}.py`; `transports/whatsapp/**` (R-A16 only); tests under `tests/{core/audit,mcp,security,unit,integration}`; `scripts/e2e_smoke.py`, `scripts/capture_chain_vectors.py`; `docs/comms-v0.3-supersession.json`, `docs/verification/comms-v0.3*`; `AGENT.md`, `CHANGELOG.md`.
- **Verification:** full gate at the Part A head — contracts OK; Telegram 2385 passed, 4 skipped; smoke 57/57; formal 544 states/22 assertions and the campaign model at 96,528 states; ruff/format/mypy clean; build OK; WhatsVault 450 passed. Part A exit gate (`test_v03_part_a_exit.py`) re-runs every §A.9 owning test; test accounting unexpectedly missing = 0; smoke map complete. Evidence: `docs/verification/comms-v0.3.md`.
- **Follow-ups:** the owner confirms or applies the R-A20 `.claude/settings.json` change; local tag `comms-v0.3-part-a` (not pushed); Parts B–D next; merge and push need the owner's approval. No production claim.

### 2026-09-25 (Australia/Sydney)
**Raouf:**
- **Scope:** Comms v0.3 Part B — durability, audit, keys, retention, recovery, backup (branch `comms-v0.3`), plan rev 2 tasks B1–B33, inline and test-first.
- **Summary:** Chain epochs and root-aware verification (the five probe attacks as regressions); the truncation root; the full key inventory with staged rotation, epoch-opening chain-key rotation, checkpoint/cursor consequences, backup-signer trust states and retired-HMAC dependency rules; the keyed campaign commitment; a daemon-owned 0600 secret store (no Keychain); the `comms.db` rekey with recovery at every boundary; staged credential rotation with re-check and rollback; Telegram session revoke, error mapping and login recovery; retention in foreign-key order (legacy truncation enables receipt purge; comms truncation; body and identity redaction; retired key material; a maintenance event), failing closed on a bad root; `audit repair` through an ancestor-proving verifier; age-v1 X25519 backups with signatures, binding, transfer frames, export, staged import and an epoch-opening commit; `comms doctor`; eight runbooks; two mutation-tested formal models and a 200-walk differential test.
- **Files changed:** `src/comms/core/{audit,keys,maintenance,backup}/*`, `src/comms/core/{credentials,doctor,installation,strict_json}.py`, `src/comms/core/storage/{migrations,rekey,db}.py`, `src/comms/core/{domains,refs,timeutil,validators}.py`, `src/comms/core/campaigns/{directory,events}.py`, `src/comms/core/delivery/{freeze,commitment}.py`, Telegram `telegram/{telethon_adapter,admin_rpc}.py`, `ipc/handlers/auth.py`, `storage/{identity,owner_state,settings,migrations}.py`, `runtime/legacy_retention.py`; `formal/{audit_model,keys_model}.py`, `formal/README.md`; `docs/runbooks/*`, `tests/fixtures/age/*`; tests throughout; `docs/verification/{comms-v0.3,comms-v0.3-rulings,telegram-rpc-review}.md`; `AGENT.md`, `CHANGELOG.md`.
- **Verification:** full gate at the Part B head (see the evidence for exact counts); Part B exit gate re-runs every owning test with no skip allowed; crash tables converge at every boundary; formal: 544 states / 22 assertions, the campaign model at 96,528 states, the audit model at 215,040 states, the keys model at 512 states; WhatsVault 450 passed. Evidence: `docs/verification/comms-v0.3.md`.
- **Follow-ups:** the R-A20 `.claude/settings.json` change awaits the owner; local tag `comms-v0.3-part-b` (not pushed); Part C (provider adapters) next; merge and push need the owner's approval. No production claim.

### 2026-09-25 (Australia/Sydney)
**Raouf:**
- **Scope:** Comms v0.3 Part C — provider adapters (branch `comms-v0.3`), plan rev 2 tasks C1–C34, inline and test-first.
- **Summary:** Four adapters behind the core provider protocols, each passing every contract it advertises with zero skips: the Telegram Bot API (pinned client, named-case classification, delivery, capability from real rights, admin tables for membership, rights and P §74 profiles, chat info, pins, invites, join requests and topics, an atomic-offset update poller, a local-only context source); the Telegram user actor over the one Telethon session (capability-keyed RPC sets with retries and auto-reconnect off, `random_id` sends persisted on the attempt before the call and reconciled once, delivery, capability across group kinds, admin, a bounded live context source, the update consumer); the WhatsApp Cloud API (a per-code error table, delivery with the mirrored window and frozen template binding, template and media operations with a Meta-only bounded downloader, account status and discovery-gated groups); and the Meta webhooks (a raw-bytes HMAC ingress, a durable inbox and a resumable fan-out). Core gains `OperationSemantics`, normalized rate limits, campaign-content rendering, template bindings, the window mirror and an inbound-body retention phase. The Meta contract oracle lives in WhatsVault's `fake_meta.py` (R-C27). Live acceptance is opt-in and evidence-only, with two runbooks. The registry is built from secret-store credentials.
- **Files changed:** `src/comms/core/providers/*`, `src/comms/core/{campaigns/{render,templates},delivery/{transport,engine,freeze,operations,window}}.py`, `src/comms/core/{domains,storage/migrations,audit/specs,maintenance/retention}.py`; `src/comms/transports/{net.py,telegram/{bot,user}/*,telegram/{peers,message_text,args,admin_profiles,chat_specs,capabilities,page_bounds}.py,telegram/telegram/{telethon_adapter,rights,send_attempt,updates_view}.py,telegram/runtime/composition.py,whatsapp/**}`; `src/comms/runtime/adapters.py`; `transports/whatsapp/src/whatsvault/providers/fake_meta.py` (R-C27 only); `tests/{conformance,transports,fixtures/providers}/**`, `tests/core/*`, `tests/security/{test_egress,test_adapter_canaries,test_v03_part_c_exit}.py`, `tests/integration/test_adapter_registry.py`; `docs/runbooks/live-acceptance-{telegram,whatsapp}.md`; `docs/verification/{comms-v0.3,comms-v0.3-rulings,telegram-rpc-review}.md`; `AGENT.md`, `CHANGELOG.md`.
- **Verification:** full gate at the Part C head (exact counts in the evidence); the Part C exit gate re-runs every owning test with no skip allowed and runs the whole conformance registry; privacy canaries over all four adapters; WhatsVault 450 passed. Evidence: `docs/verification/comms-v0.3.md`.
- **Follow-ups:** the live update-stream switch, the daemon's `build_comms_adapters` call, the WhatsVault archive binding, per-recipient template language and `chat.set_photo` move to Part D; live acceptance needs the owner's disposable accounts; the R-A20 `.claude/settings.json` change awaits the owner; local tag `comms-v0.3-part-c` (not pushed); merge and push need the owner's approval. No production claim.

### 2026-09-25 (Australia/Sydney)
**Raouf:**
- **Scope:** Comms v0.3 Part D — services, MCP, CLI, ingress, OAuth, smoke and client runbooks (branch `comms-v0.3`), plan rev 2 tasks D1–D38, inline and test-first.
- **Summary:** Built the typed service layer (`src/comms/services/`): the actor resolver, capability, the context engine, handles and cursors, groups, messages, campaigns, directory, templates, media, account and identity. It runs on one mutation executor with durable operation records, request-id idempotency, sagas and crash recovery. The 109-tool catalog is pinned by digest (`4380d085…`). `comms mcp` serves it in two ways: a privilege-free stdio proxy with `cml1` leases, and Streamable HTTP `/mcp`, with `cml1` locally and OAuth 2.1 remotely (EdDSA tokens, PKCE S256, owner code). The CLI is generated from the catalog and runs over the admin socket. There are three exact-path listeners. The operations model and the differential walk passed, as did the typed egress sweep. The smoke now drives the real comms composition, stdio, HTTP, OAuth and the CLI, and runs `verify --all` after every surface wrote. D38 added the `template` CLI group, the P §80 intent data, the P §81 deterministic ambiguity test, three client runbooks and the Part D exit gate. The ruling is R-D38.
- **Files changed:** `src/comms/{services,mcp,runtime,cli_commands}/*`, `src/comms/cli.py`, `src/comms/core/{auth,mutations,groups,objects,security,context_handles}*` and schema v4, the admin adapters (`transports/telegram/{bot,user}/admin_messages.py`, WhatsApp groups/templates/media/http), `formal/operations_model.py`, `scripts/e2e_smoke.py`, `docs/runbooks/{install,uninstall,clients-*}.md`, `docs/verification/comms-v0.3{,-rulings,-smoke-map}.*`, `tests/{services,mcp,cli,runtime,evaluation}/*` and the security, formal and integration tests named in the evidence, this file, `CHANGELOG.md`, `CLAUDE.md`.
- **Verification:** full gate at the Part D head: pytest 5023 passed, 4 skipped; smoke 74/74; formal 57 passed; ruff, format, mypy (275 files) and build clean; WhatsVault 450 passed. The Part D exit gate re-runs every owning test of design D.1–D.11 with no skip allowed. Evidence: `docs/verification/comms-v0.3.md`.
- **Follow-ups:**
  - The daemon must open `comms.db` and serve the composition. This blocks every P §88 row.
  - 13 tools are not offered.
  - Most operator commands are unwired.
  - `cml1` replay within 60 s is not tracked.
  - The proxy frames, backups and webhook responses were not egress-swept.
  - Carried from Part C: the update stream, the WhatsVault archive binding and per-recipient template language.
  - R-A20 awaits the owner.
  - The local tag `comms-v0.3-part-d` is not pushed; merge and push need the owner's approval.
  - No production claim.

### 2026-09-25 (Australia/Sydney)
**Raouf:**
- **Scope:** Comms v0.3 D39-PRE / Runtime Completion, Task E0 (branch `comms-v0.3-d39pre`): the owner's rulings, the host permission rules, and a correction.
- **Summary:**
  - The owner decided R-A20: host permission UX is defence in depth. `.claude/settings.json` now asks before every consequential comms tool, 43 of them, generated from the catalog. There is no blanket allow, and the CLI campaign-send rules are kept.
  - Also approved: R-E1 (a `cml1` lease may be reused within its window; write safety comes from the request id), R-E2 (template language is explicit per campaign) and R-E3 (a registered operator command works, or it is not registered).
  - **Correction:** the Parts A–D entries above say their tags are "not pushed". All four `comms-v0.3-part-*` tags, `main` (merge `272dd8b`) and the `comms-v0.3` branch were pushed with the owner's approval on 2026-09-25.
  - The D39-PRE plan was owner-approved with four amendments.
  - **Found:** two test files on `main` (`tests/services/handle_fixtures.py`, `test_ctx_handles.py`) had misordered imports that the gate never reported. Ruff's cache keys on file content and settings, but its first-party import detection reads the filesystem, so it kept a stale clean result. Both files are fixed, and the gate now runs `ruff check` and `ruff format --check` with `--no-cache`.
- **Files changed:** `.claude/settings.json`, `tests/security/test_host_permissions.py`, `docs/runbooks/clients-claude-code.md`, `docs/verification/comms-v0.3{,-rulings}.md`, `docs/superpowers/plans/2026-09-25-comms-v0.3-d39-pre.md`, `tests/services/{handle_fixtures,test_ctx_handles}.py` (import order), this file, `CHANGELOG.md`.
- **Verification:** full gate (see the E0 ledger line); `test_host_permissions` 4 passed after watching the ask-rule test fail.
- **Follow-ups:** E1–E11 of the plan; the 13-tool disposition plan must be done before D39-B. No production claim.
