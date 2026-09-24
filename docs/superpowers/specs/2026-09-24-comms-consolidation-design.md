# Comms Consolidation Design (Phase 5b-0 contract, and the 5b→5e sequence)

**Status:** Revision 1. The owner approved it section by section on 2026-09-24, and all seven owner amendments are folded in (§0A).
**Date:** 2026-09-24 (Australia/Sydney)
**Supersedes, for sequencing only:** the Phase-5 design's 5b and 5c ordering. Its technical content (lifecycle, rotation, retention, chain epochs, backup and import) stands, and is re-sequenced here as **5c**.
**Sources measured at the time of writing:**
- Telegram MCP `main` at `255b8e8`: `1493 passed, 10 skipped`, smoke 60/60, formal 624 states / 18 assertions, Python 3.12.2.
- WhatsVault `b6fd51a` (`https://github.com/Raoof128/whatsvault.git`): `539 passed, 0 skipped, 0 failed`, counted from the result dots because the project's pytest config suppresses the summary line. That was measured locally on Python 3.14.7, and its CI matrix includes 3.12 on macOS with Homebrew SQLCipher.

## Controlling statement

This repository becomes **comms**: one operator-controlled communications system over Telegram and WhatsApp.

The migration obeys one rule:

> **No phase changes structure AND semantics AND external side effects at the same time.**

- **5b-1 and 5b-2 change only where code lives.** They change no meaning.
- **5b-3 is the only phase that changes meaning.** It is the constitutional amendment (comms spec v0.2).
- **5d is the only phase that adds real-world side effects** (live sends).

**Nothing here is a production claim.**

## 0. Decisions taken with the owner

| # | Decision | Why |
|---|---|---|
| C1 | This repo is renamed `comms`. WhatsVault's code moves in, and the WhatsVault repo is archived after 5b-2 is green. | One codebase for one communications control plane. |
| C2 | No Touch ID anywhere: operator commands **and** AI read disclosures. **Takes effect in 5b-3, not before.** | Owner decision. The consequence is recorded in §4.3. |
| C3 | Telegram sends as either the Society bot (Bot API) or the owner's user session (MTProto), chosen **per destination**. | Owner decision. |
| C4 | WhatsApp sends through Meta's WhatsApp Business Cloud API from a Society business number, with templates where the platform requires them. | WhatsVault's provider protocol already targets it (`providers/fake_meta.py`), and it is the only compliant route for bulk sends. |
| C5 | Staged sequence A, with lifecycle/audit hardening (5c) **before** live sends (5d). | Campaign audit events need chain epochs and truncation, which `verify_chain` cannot do today (Phase-5 design 0B G2, probe `docs/verification/probes/phase5_chain_epochs_probe.py`). |
| C6 | WhatsVault arrives intact as a transport subsystem. Duplicated primitives are consolidated only after the combined tree is green, one seam at a time. | Import, move and dependency consolidation as one change is how migrations hide defects. |

## 0A. Owner amendments folded into revision 1

| # | Amendment | Where |
|---|---|---|
| A1 | Python import compatibility is explicit: a thin `telegram_mcp` package that only forwards. | §2.3 |
| A2 | The core/transport rule also covers dynamic imports and inspection of transport internals, not just static imports. | §2.2 |
| A3 | Subtree provenance: no `--squash`, tree-hash equality proven before any edit inside the subtree. | §3.1 |
| A4 | SQLCipher native provenance is part of build evidence. | §3.3 |
| A5 | Governance precedence through 5b-2: two specs keep their semantics; 5b-0 governs only structure. | §1 |
| A6 | Presence behaviour is frozen through 5b-2. | §1 |
| A7 | Operations outside the repo are not in any exit gate. | §5 |

## 1. Governance through 5b-2

- **Frozen spec v0.1.10** (SHA-256 `36b67f48…b0a`) governs **Telegram semantics**.
- **WhatsVault's frozen design** (`transports/whatsapp/docs/internal/specs/2026-08-27-whatsvault-design.md` after import) governs **WhatsApp semantics**.
- **This document governs only repository structure, build, provenance and compatibility.**
- If the two specs disagree about a shared primitive, **it is not reconciled in 5b-1 or 5b-2**. Both implementations stay, or consolidation waits for 5b-3.
- **No presence or consent semantics change in 5b-1 or 5b-2.** `PRESENCE_GATED`, the admin approver, the consent broker and WhatsVault's `INV-APPROVAL` stay byte- and behaviour-compatible until the 5b-3 amendment replaces them.

## 2. Phase 5b-1: rename and restructure (mechanical)

### 2.1 What does not change

These are protocol and host identifiers, not branding. Changing any of them would invalidate existing chains, receipts, pairings or installs.

- **Wire and domain strings:** every domain-separation constant (for example `telegram-mcp-audit-v1`, `telegram-mcp-audit-genesis-v1`, `telegram-mcp-anchor-v1`, `telegram-mcp-display-v1`, `telegram-mcp-admin-request/v1`, `telegram-mcp-checkpoint-v1`, `tg-mcp-grant/v1`), the `tgml1` lease prefix, key-id formats, opaque-ref prefixes, the ten tool names and the `contracts/*.json` bytes.
- **On-host identity:** the `telegram-mcpd` / tunnel service accounts, `/private/var/run/telegram-mcp`, runtime-dir and socket names, the consent-agent bundle ID and signing identity, Keychain service names, launchd labels, and the key-store file names.
- **Logger names** (`telegram_mcp.*`), because log consumers key on them.
- **Behaviour.** No handler, rule, default, error string or exit code changes.

### 2.2 Package map and the dependency rule

- The Python package `telegram_mcp` becomes `comms`.
- `comms/core/` holds exactly the modules a **mechanical** rule selects: a module is core if and only if its transitive import closure inside the package contains no Telegram-specific module. The 5b-1 plan computes the list with an AST import-graph script, commits the script and its output, and moves exactly that list. Nobody picks by eye.
- `comms/transports/telegram/` holds everything else.

**The dependency rule is permanent from 5b-1 onward, not a one-off migration check:**

- `comms.core` must not import `comms.transports.*` or the legacy `telegram_mcp` package, **statically or dynamically**. Calls to `importlib.import_module`, `__import__` and `importlib.util` inside `core` are rejected outright.
- `comms.core` must not name transport internals: no string literal containing `comms.transports` and no attribute access into a transport module.
- `comms.transports.*` may import `comms.core`.
- **Enforcement:** an AST guard runs over `src/comms/core/**` for static imports, dynamic-import calls and transport-path strings. A second guard computes the transitive closure of every `core` module and asserts it never reaches `transports`.

### 2.3 Compatibility surface (A1)

Guaranteed through 5b-2:

- **The CLI.** The `telegram-mcp` console script works unchanged, and `comms` is added as a second entry point to the same `main`.
- **The module entry point.** `python -m telegram_mcp.cli` keeps working.
- **On-host identity and protocol** (§2.1).

Python imports of `telegram_mcp.*` internals are **not** guaranteed. The legacy package is exactly:

```text
src/telegram_mcp/__init__.py   # docstring only: "legacy entry points; see comms"
src/telegram_mcp/cli.py        # from comms.transports.telegram.cli import main; __main__ guard
```

A guard test asserts that the legacy package contains only these two files and that `cli.py` holds no logic beyond the forward. This is measured, not assumed: at `255b8e8` the only module-path references outside the package are `pyproject.toml`'s console script and `python -m telegram_mcp.cli`, and there are no dynamic imports in `src`.

### 2.4 Proof that nothing changed

- **AST equivalence modulo import paths.** For every moved module, the normalised AST after the move equals the AST before, once every import path is rewritten through the committed move map. The script is committed, and its output is part of the evidence. This is how "no semantic diff" is demonstrated rather than asserted.
- **Byte identity:** `contracts/*.json`, `SECURITY-MANIFEST.json` (except path fields listed in the plan), the agent bundle and every domain constant (grep-pinned).
- **The full gate:** `1493 passed, 10 skipped`, smoke 60/60, formal 624/18, plus ruff, format, mypy (retargeted to `src/comms`) and build.
- **The dependency-rule guards (§2.2) are green.**

### 2.5 Exit criteria

1. The Telegram source is mechanically relocated per the committed map.
2. Protocol and domain strings are identical.
3. On-host identity is identical.
4. The `telegram-mcp` CLI and `python -m telegram_mcp.cli` work, and the `comms` alias works.
5. `1493 passed, 10 skipped`; smoke 60/60; formal 624/18.
6. The core→transport guards are green.
7. AST equivalence holds for every moved module.

## 3. Phase 5b-2: import WhatsVault intact

### 3.1 Import and provenance (A3)

- Run `git subtree add --prefix=transports/whatsapp https://github.com/Raoof128/whatsvault.git b6fd51a`, **without `--squash`**, so the full history is kept.
- **Before any edit inside the prefix**, prove `git rev-parse HEAD:transports/whatsapp` == `git rev-parse b6fd51a^{tree}`.
- `docs/provenance/whatsvault.md` records:
  - source URL;
  - source commit `b6fd51a`;
  - source tree hash;
  - the imported merge commit;
  - prefix `transports/whatsapp`;
  - the measured baseline (539 passed).
- A test re-derives the tree-hash equality from git while the subtree is untouched. That test is retired, with a ledger note, only at the first deliberate seam change.

### 3.2 Layout

The layout is kept intact, and WhatsVault stays importable as `whatsvault`:

```text
transports/whatsapp/
    src/whatsvault/
    apps/
    ios/
    tests/
    docs/
```

It is **not** moved into `src/comms/transports/whatsapp` during 5b-2. The combined pytest configuration runs both test trees, each with its own `conftest.py` and root.

### 3.3 One environment and native provenance (A4)

- **One interpreter: Python 3.12.** WhatsVault's CI proves 3.12/macOS.
- **One `uv.lock`**, re-resolved with WhatsVault's dependencies (`sqlcipher3`, `keyring`, `python-ulid`, `apscheduler<4`, and ranges for `mcp`, `cryptography` and `uvicorn`). The existing pins satisfy them: `mcp==2.2.0` meets `>=2.1,<3`, `cryptography==50.0.1` meets `>=43`, and `uvicorn==0.53.0` meets `>=0.30`. The new packages get the spec §7 dependency review.
- The gate evidence records the **native substrate**: Python version and architecture, the `sqlcipher3` version, and the runtime SQLCipher version (`PRAGMA cipher_version`), plus the Homebrew SQLCipher formula version. `uv.lock` cannot pin a Homebrew library, so a changed native substrate has to show up in evidence.

### 3.4 Seam consolidation (after the combined baseline is green)

Only after exit criteria 1–7 below pass does consolidation begin, **one seam per commit**, each with both suites green. A seam is consolidated only when the two implementations are shown to be identical in contract: same inputs, same outputs, and same failure behaviour on the vectors of both projects. Examples: JCS, opaque-ref format, audit framing.

Platform-specific mechanisms are **never** consolidated into `core`. Examples: Meta webhook verification, template state, Cloud API delivery semantics, and MTProto sessions.

### 3.5 Exit criteria

1. The exact `b6fd51a` tree is imported with history, and tree-hash equality is proven and recorded.
2. WhatsVault is importable as `whatsvault`.
3. `1493 passed, 10 skipped` **and** `539 passed`, in one repo and one Python 3.12 environment.
4. Smoke 60/60.
5. The combined lock has been reviewed.
6. SQLCipher native provenance is recorded.
7. There is still no semantic change on either side (§1).

## 4. Phases 5b-3 to 5e (sequence and scope; each gets its own design and plan)

### 4.1 5b-3: comms spec v0.2 (the constitutional amendment)

5b-3 writes **comms spec v0.2**, which replaces both frozen specs for everything it covers:

- An authenticated operator command is the authorization. There is no Touch ID anywhere (C2).
- The AI has drafting and inspection capabilities and **no transmission primitive**.
- Telegram sends by bot or by user session, per destination (C3).
- WhatsApp sends through the Cloud API (C4).
- The read-only guard (Gate D) is replaced by a narrower guard: **no send primitive is reachable from any MCP tool**, while operator send paths are explicit.

`PRESENCE_GATED` and WhatsVault's per-send approval are retired **here**, with tests that pin the replacement rule. The old tests are rewritten against the new rule, never simply deleted.

### 4.2 5b-4: campaign core (fake transports only)

5b-4 builds the campaign core, following the pasted comms spec §§4–19 and §32:

- locations, audiences (with cycle rejection), destinations and campaigns;
- immutable send snapshots, recipient resolution and per-transport deduplication;
- idempotency keys of `(campaign, transport, canonical recipient)`;
- queue states, scheduling, cancellation (`cancelled_before_send` / `already_sent` / `in_flight`);
- restart recovery that never blindly resends.

It is exercised end to end against fake transports only.

### 4.3 5c: lifecycle and audit hardening (before any live send)

5c is the Phase-5 design's former 5b and 5c technical content:

- chain epochs and truncation (G2);
- session revoke and recovery;
- key rotation, with `privacy-key` budget continuity (G3);
- retention in foreign-key order (G1);
- backup and import;
- plus durable, restart-safe audit treatment for campaign events (`campaign.send_started`, `delivery.accepted`, `campaign.partial`, `campaign.completed`, …).

**Why before 5d:** campaign events become real-world side effects in 5d, and their audit trail must not depend on a chain that cannot rotate or truncate.

**C2 consequence, stated here so it is not lost:** once 5b-3 removes AI-read consent, any AI client holding a valid client credential can read in-scope private content with no human in the loop. What remains is:

- the credential boundary;
- per-project grants and egress levels;
- exposure budgets;
- the receipt/audit trail, which shows *after the fact* what was read.

5c keeps all of them intact.

### 4.4 5d: real send adapters

- A Telegram Bot API adapter, a Telegram user-session send adapter, and a WhatsApp Cloud API adapter, all consuming one frozen `DeliveryJob` contract.
- Failure isolation per transport is mandatory.
- The order is: fake transport, then the dedicated test infrastructure (Telegram Test DC / test bot; a Meta test number), then operator-run live acceptance.
- WhatsVault's `fake_meta.py` becomes the contract oracle for the Meta side.

### 4.5 5e: operator and AI surfaces

- `comms campaign …`, location and audience administration, transport configuration, delivery reports, `comms doctor`, and the AI drafting tools, with evidence.
- **Structural tests:**
  - the AI tool registry contains no send primitive;
  - the operator CLI sends with no second approval ceremony.

## 5. Operations outside the repository (A7)

Each of these is a separately approved operational step, confirmed by the owner when it comes up. **None is in any phase's exit gate.** The repository must be internally complete under its current external name, and a postponed operation never makes a phase red.

- Renaming the GitHub repository `telegram-mcp` to `comms`.
- Archiving `whatsvault` on GitHub (after 5b-2 is green).
- Renaming the local folder `~/Desktop/Raouf/Telegram` to `Comms`. Claude Code's per-project memory is keyed by path (`~/.claude/projects/-Users-raoof-r12-Desktop-Raouf-Telegram/`), so the memory directory is migrated in the same step.
- Updating Zurvan's project tag (`telegram-mcp` → `comms`).

## 6. Out of scope for this document

- Everything semantic about sending, audiences or campaigns beyond the sequence above, which belongs to the 5b-3 and 5b-4 designs.
- The installed-client and tunnel work (Phase 6), and the release gauntlet (Phase 7).
