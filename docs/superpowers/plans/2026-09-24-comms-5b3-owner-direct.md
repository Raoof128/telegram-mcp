# Comms 5b-3 — Owner-Direct Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the consent/Touch-ID subsystem and its pipeline step, without losing any safety property or any historical evidence.
- Receipts become explicitly versioned; v2 is `owner_direct`.
- v1 evidence stays verifiable.
- JCS is extracted as the single copy, byte-identical.
- The formal model gains the replacement invariants.
- The AI boundary is scoped precisely and guarded, and WhatsVault's dormant sender is proven unreachable.

**Architecture:** The dependency order is fixed: JCS independent → evidence dual-version → the budget binding re-keyed → the runtime stops depending on consent → the formal model → admin presence removed → the dead machinery deleted → guards → spec v0.2. Nothing is deleted until nothing depends on it.

**Tech Stack:** Python 3.12, SQLite (the documented 12-step table rebuild), pytest, ruff, mypy. No new dependency.

**Spec:** [`2026-09-24-comms-5b3-owner-direct-design.md`](../specs/2026-09-24-comms-5b3-owner-direct-design.md) rev 1. The normative output is `docs/comms-spec-v0.2.md` (Task 12).

**Branch:** `comms-5b3`, from `main` at the commit carrying this plan.

## Rehearsed before writing (throwaway worktree at `5b3 design` commit)

| Risk | Result |
|---|---|
| **R1 — JCS byte identity** | Byte-identical across the 9 Phase-2 cases (and their display inputs), 7 fatal inputs (same exception type and message) and 6 extra cases. The rehearsal also found that `authority/cursors.py` imports `jcs_dumps` **with a trailing comment**, so the repoint must be AST-based, not a plain text replace. |
| **R2 — v1 receipts across the v2 rebuild** | 3 real signed v1 receipts, each with ledger and audit children, verify before and after. Rows, IDs and consent values are byte-identical; both index/trigger objects are preserved; counts are equal; `foreign_key_check` is empty; `quick_check` is ok; the chain verifies. A table-level CHECK blocks both v1→v2 masquerades, and child foreign keys still enforce. The rehearsal also found that `migrate()` wraps everything in one `BEGIN`, so a rebuild needs `PRAGMA foreign_keys=OFF` **outside** the transaction. A `Migration.rebuild` flag is required (Task 2). |
| **R3 — dormant WhatsVault sender** | No network client exists anywhere in `whatsvault`. The three grep hits were the word "requests" in prose, so the guard must be AST-based. `providers` is only `base` plus `fake_meta`; `apps` is not importable from the root; the console scripts are exactly `comms` and `telegram-mcp`; nothing in `src/comms` imports `whatsvault`. |

## Global Constraints

- **No v1 evidence is rewritten.** v1 receipts, v1 audit history and retained key IDs stay byte-identical and verifiable.
- **The frozen identifiers that remain stay byte-identical:** TG-JCS-v1, `tgml1`, key-id formats, every audit, receipt, anchor, cursor, scope and grant domain string, and all logger names.
- **Retired identifiers are tombstoned.** The challenge wire, `ApprovalEnvelope`, the display digest `telegram-mcp-display-v1`, RV-1 and the `PROMPT` / `APPROVAL` / `DENIAL` frames must never be reassigned.
- **The WhatsVault subtree is untouched:** its tree stays `abdbcdc7…`.
- **Fail closed, commit only on a green gate** (fail-fast). The Task 1 RED commit and the deletion task's intermediate red states are recorded exceptions; nothing else is.
- **Test accounting (G2):** the collection manifest is captured before Task 1. Every test ID missing at the end is classified as `removed-with-consent` or `replaced-by-owner-direct`, and **unexpectedly missing = 0**.
- **The full gate:** `uv sync --locked`, contracts, `pytest`, smoke, formal, ruff, format, `mypy src/comms src/telegram_mcp`, build, **plus** the WhatsVault gate (539 passed).

## Review Focus

1. **A partially written v2 row, or a damaged v1 row, verifies as the other version.** *Test: Task 2's CHECK tests plus Task 3's dispatch-on-`proof_version` tests.*
2. **A reservation minted for one call is spent by another** once the consent digest is gone. *Test: Task 4, `test_a_reservation_is_bound_to_its_call`.*
3. **The soft-threshold decision drifts from the old decision point.** *Test: Task 5, `test_soft_flag_equals_the_pre_reservation_consult`.*
4. **Some runtime path still awaits a consent prompt**, and hangs or waits for a timeout. *Test: Task 5's end-to-end disclosures complete with no prompter wired, and the Task 9 import guard shows no `consent` module exists.*
5. **A `presence` argument sent by an old CLI or script is silently ignored instead of refused.** *Test: Task 8, `test_presence_argument_is_rejected`.*

---

### Task 0: Capture the pre-5b-3 collection manifest

```bash
uv run pytest --collect-only -q -p no:randomly | grep "::" | sort > docs/verification/comms-5b3-collected-before.txt
wc -l docs/verification/comms-5b3-collected-before.txt   # expected: 1529
git add docs/verification/comms-5b3-collected-before.txt
git commit -m "chore: 5b-3 pre-change test collection manifest (1529)"
```

---

### Task 1: Extract TG-JCS-v1 into `canonical.py` (the single copy)

**Files:**
- Create: `src/comms/transports/telegram/canonical.py`
- Modify: `consent/challenge.py` (import from canonical), plus every importer (AST repoint)
- Move: `tests/fixtures/consent/jcs_vectors.json` → `tests/fixtures/canonical/jcs_vectors.json`, and update its readers (`grep -rln jcs_vectors tests scripts`)
- Test: `tests/unit/test_canonical.py`

- [ ] **Step 1: The failing byte-oracle test**

```python
# tests/unit/test_canonical.py
"""TG-JCS-v1 lives once, in canonical.py, byte-identical to the pre-extraction encoder."""

import json
import subprocess
from pathlib import Path

import pytest

from comms.transports.telegram.canonical import jcs_dumps

ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "tests" / "fixtures" / "canonical" / "jcs_vectors.json"
BASE = (ROOT / "tests" / "fixtures" / "canonical" / "BASE").read_text().strip()


def _old_encoder():
    source = subprocess.run(
        ["git", "show", f"{BASE}:src/comms/transports/telegram/consent/challenge.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    start, end = source.index("def _walk_canonicalizable("), source.index("def display_digest(")
    namespace: dict = {}
    exec("import json\nfrom typing import Any\n" + source[start:end], namespace)  # noqa: S102
    return namespace["jcs_dumps"]


OLD = _old_encoder()
FATAL = [1.5, {"k": float("nan")}, "\ud800", {"é": 1}, {1: 2}, {"a": [object()]}, (1, 2)]
EXTRA = [{"b": 1, "a": [True, False, None, 0, 2**63], "ç": "x"}, {"z": '‮\x00"\\', "y": "🔍"}, [], {}, "", 0]


def _outcome(fn, value):
    try:
        return fn(value)
    except Exception as exc:  # noqa: BLE001 -- the outcome under comparison includes failures
        return (type(exc).__name__, str(exc))


def test_the_frozen_corpus_is_byte_identical():
    corpus = json.loads(VECTORS.read_text())
    for case in corpus["cases"]:
        assert jcs_dumps(case["input"]).hex() == case["jcs_hex"], case["name"]
        for key in ("input", "display_input"):
            if key in case:
                assert jcs_dumps(case[key]) == OLD(case[key]), (case["name"], key)


@pytest.mark.parametrize("value", FATAL, ids=repr)
def test_fatal_inputs_fail_identically(value):
    new, old = _outcome(jcs_dumps, value), _outcome(OLD, value)
    assert new == old and isinstance(new, tuple)


@pytest.mark.parametrize("value", EXTRA, ids=repr)
def test_extra_inputs_are_identical(value):
    assert _outcome(jcs_dumps, value) == _outcome(OLD, value)


def test_there_is_exactly_one_jcs_implementation():
    src = ROOT / "src"
    hits = [p for p in src.rglob("*.py") if "def jcs_dumps(" in p.read_text()]
    assert [p.relative_to(ROOT).as_posix() for p in hits] == [
        "src/comms/transports/telegram/canonical.py"
    ]
```

Write `tests/fixtures/canonical/BASE` containing the full SHA of `HEAD` before this task, run `git mv tests/fixtures/consent/jcs_vectors.json tests/fixtures/canonical/jcs_vectors.json`, and update its readers. Run the test: it FAILS with `ModuleNotFoundError: comms.transports.telegram.canonical`.

- [ ] **Step 2: Implement** (as rehearsed)

- Create `canonical.py`: a module docstring, `import json`, `from typing import Any`, `__all__ = ["jcs_dumps"]`, and `_walk_canonicalizable` plus `jcs_dumps` **cut verbatim** from `consent/challenge.py`.
- In `challenge.py`, replace them with `from comms.transports.telegram.canonical import jcs_dumps` and keep `jcs_dumps` in its `__all__` until Task 9 deletes the module.
- Repoint every other importer **by AST**: for each `ImportFrom(module="comms.transports.telegram.consent.challenge")` whose names are exactly `["jcs_dumps"]`, rewrite that statement's source span to `from comms.transports.telegram.canonical import jcs_dumps`, keeping any trailing comment.
- Mixed imports (`seams.py` imports `display_digest, jcs_dumps`) are split by hand. `display_digest` stays on consent until Task 6 removes its use.

- [ ] **Step 3:** Run `uv run pytest tests/unit/test_canonical.py -q`: 15 passed. Run the full gate: `1529 + 15 = 1544 passed, 10 skipped`, WhatsVault 539. Commit: `refactor: extract TG-JCS-v1 into canonical.py (single copy; byte-identical)`.

---

### Task 2: Migration v2: versioned receipts (rebuild), enforced by CHECK

**Files:** `storage/migrations.py` and `storage/db.py` (if `open_db` gates on the version); Test: `tests/unit/test_receipt_migration.py`

- [ ] **Step 1: The failing upgrade test.** Port the R2 rehearsal (`scratchpad/r2.py`) into `tests/unit/test_receipt_migration.py` as four tests:
  - `test_v1_receipts_verify_before_and_after_the_migration`: three real signed v1 receipts, each with ledger and audit children.
  - `test_the_rebuild_preserves_rows_ids_indexes_triggers_and_counts`.
  - `test_a_v1_row_cannot_masquerade_as_v2`: both CHECK violations.
  - `test_children_still_enforce_the_foreign_key`.

  The fixture builds the database at the **v1 schema** by calling `migrate(conn, migrations=MIGRATIONS[:1])`, inserts the receipts, then calls `migrate(conn)`.

- [ ] **Step 2: Implement.**
  - Give `Migration` a `rebuild: bool = False` field.
  - In `migrate()`, for a rebuild migration, run `conn.commit(); conn.execute("PRAGMA foreign_keys=OFF")` before `BEGIN`. Inside the transaction, after its statements, require `PRAGMA foreign_key_check` to return no rows, raising `sqlite3.IntegrityError("foreign key check failed after rebuild")` otherwise. In `finally`, run `PRAGMA foreign_keys=ON` and assert it reads back `1`.
  - Append `Migration(SCHEMA_VERSION + 1, _RECEIPTS_V2, rebuild=True)`, where `_RECEIPTS_V2` is exactly the rehearsed sequence:
    1. `CREATE TABLE disclosure_receipts_v2 (…)`: every v1 column, with the two consent columns nullable, plus `proof_version INTEGER NOT NULL DEFAULT 1 CHECK(proof_version IN (1,2))`, `soft_threshold_exceeded INTEGER CHECK(… IS NULL OR … IN (0,1))`, and the table CHECK tying v1 to non-null consent with a null flag, and v2 to null consent with a non-null flag.
    2. `INSERT INTO disclosure_receipts_v2 (<v1 columns>, proof_version) SELECT <v1 columns>, 1 FROM disclosure_receipts`.
    3. `DROP TABLE disclosure_receipts`, then `ALTER TABLE disclosure_receipts_v2 RENAME TO disclosure_receipts`.
    4. Re-create the `disclosure_receipts_tuple_consistency_insert` trigger via the existing `_guard(...)` builder, as the single definition.
  - `SCHEMA_VERSION` constants and any version assertion (`grep -rn SCHEMA_VERSION src tests`) move to the new value.

- [ ] **Step 3:** The four tests pass. The full gate is green (+4). Commit: `feat: migration v2: explicitly versioned receipts; v1 evidence preserved (rebuild with FK check)`.

---

### Task 3: Receipt proof v2, with verification that dispatches on `proof_version`

**Files:** `disclosure/receipts.py`, `disclosure/verify.py`; Test: `tests/unit/test_receipts_v2.py`

- [ ] **Step 1: Failing tests:**
  - `build_proof_payload_v2(**fields)` produces `schema == "tg-mcp-disclosure/v2"`, `authorization_mode == "owner_direct"` and `soft_threshold_exceeded: bool`, with **no** `consent_*` keys.
  - v1's `build_proof_payload` is unchanged. A v1 payload fixture captured before the change is still byte-identical under `jcs_dumps`.
  - `verify_persisted_receipt` dispatches on the `proof_version` column: a v2 row verifies; a v2 row whose `authorization_mode` is tampered to `"consent"` fails; v1 rows verify as before; and a row whose version and field shape disagree fails **before** any signature work. That last case is constructed by bypassing the CHECK on an in-memory copy with `PRAGMA ignore_check_constraints=ON`.

- [ ] **Step 2: Implement.**
  - Add `PROOF_SCHEMA_V2 = "tg-mcp-disclosure/v2"` and `APPENDIX_K_FIELDS_V2` (v1's fields minus `consent_verified`, `consent_key_id` and `consent_challenge_digest`, plus `authorization_mode` and `soft_threshold_exceeded`).
  - Add `_DERIVED_V2 = {"schema": PROOF_SCHEMA_V2, "commit_status": "committed", "authorization_mode": "owner_direct"}`.
  - Add `build_proof_payload_v2`, which validates exactly like v1 against the v2 field set.
  - In `verify.py`, select `proof_version` and `soft_threshold_exceeded`, branch on the version, and check the shape: v1 needs both consent fields non-null and a null flag; v2 needs both null and a flag in (0,1). A mismatch returns `False`.

- [ ] **Step 3:** Tests pass; the full gate is green. Commit: `feat: receipt proof v2 (owner_direct), verification dispatches on proof_version`.

---

### Task 4: Re-key the reservation binding without consent (design §2 / spec §23C.3 amendment)

**Files:** `disclosure/budget.py`; Test: `tests/unit/test_disclosure_budget.py` (extend)

- [ ] **Step 1: Failing test** `test_a_reservation_is_bound_to_its_call`: a reservation made with `binding_digest=A` cannot be committed under `binding_digest=B`, and `consent_challenge_digest` is no longer a parameter.
- [ ] **Step 2: Implement.** Rename `Reservation.consent_challenge_digest` and `reserve(consent_challenge_digest=…)` to `binding_digest`. The docstring reason becomes: *stops a reservation minted for one call being spent by a different call.* Add `call_binding_digest(tool_name, validated_args, nonce) -> str`, which is SHA-256 over `b"comms-call-binding/v1\0" + jcs_dumps({"args": validated_args, "nonce": nonce, "tool": tool_name})`. Update every caller (`grep -rn consent_challenge_digest src/comms`) except `receipts.py`, `verify.py` and `migrations.py`, which keep v1 column names.
- [ ] **Step 3:** Green gate. Commit: `refactor: bind reservations to the call, not to a consent challenge`.

---

### Task 5: Remove the consent step from the coordinator (v2 receipts, soft flag)

**Files:** `disclosure/coordinator.py`, `runtime/composition.py`, `tests/coordinator_fixtures.py`, `tests/unit/test_runtime.py`, and the dispositions below.

- [ ] **Step 1: Failing tests** in `tests/integration/test_owner_direct_disclosure.py`:
  - `test_a_disclosure_completes_with_no_consent_seam`: `DisclosureCoordinator(...)` has no `consent` parameter, `disclose()` releases, and the persisted receipt has `proof_version = 2` and verifies.
  - `test_soft_flag_equals_the_pre_reservation_consult`: with ledger state putting the worst case in `elevated`, the receipt and audit event carry `soft_threshold_exceeded = 1`; with `normal`, `0`. The tier is the `ledger.consult(worst_case)` result immediately before `reserve`.
  - `test_hard_refusal_still_precedes_retrieval`: in `refuse`, the adapter's `retrieve` is never called.

- [ ] **Step 2: Implement.**
  - Delete the consent loop (`coordinator.py` from `approval = None` through the `for … else` block).
  - Keep one `consult` and then `reserve` with no `await` between them, binding `call_binding_digest(tool_name, request.validated_args, secrets.token_hex(16))`.
  - `_prepare_proof` calls `build_proof_payload_v2(..., soft_threshold_exceeded=(decision == "elevated"))`.
  - `_insert_receipt` writes `proof_version = 2`, `NULL` consent columns and the flag.
  - The audit event carries the flag in `error_code`. That is the only free column in the frozen event schema; its value is `None` or `"soft_threshold_exceeded"`. Pin this in the test.
  - Remove the `consent` constructor parameter and the `consent_issue` / `consent_consume` checkpoints. Remove `ConsentRefusal` handling, and delete `ConsentRefusal` if nothing else uses it.
  - In `composition.py`, `DisclosureCoordinator(..., consent=...)` loses the argument.

- [ ] **Step 3: Test dispositions.** Each is recorded in the manifest classification in Task 13.
  - `tests/coordinator_fixtures.py`: remove the consent stub. Any helper that returned a stub approval is deleted.
  - `tests/integration/test_coordinator_consent.py`: **delete** (removed-with-consent).
  - `tests/unit/test_runtime.py`: drop `consent=` from the construction.
  - Crash-injection tests naming `consent_issue` / `consent_consume` (`grep -rn "consent_issue\|consent_consume" tests`): delete those parametrised cases (removed-with-consent). The remaining crash points are unchanged.
  - Receipt assertions expecting `consent_verified` in **new** receipts: rewrite them to v2 fields (replaced-by-owner-direct).

- [ ] **Step 4:** The full gate is green. Commit: `feat: the disclosure pipeline has no consent step; v2 receipts with the soft-threshold flag`.

---

### Task 6: Seams without consent

`disclosure/seams.py`: delete `CoordinatorConsent`, the `display_digest` / `build_display` use and `synthetic_exposure_digest`. If `exposure.exposure_digest` / `exposure_snapshot_digest` are then unused (check with `grep -rn`), delete them and their tests (removed-with-consent). Green gate. Commit: `refactor: seams carry no consent`.

---

### Task 7: Formal model without consent, with replacement invariants

**Files:** `formal/model.py`, `tests/formal/test_state_machine.py`, `SECURITY-MANIFEST.json`

- [ ] **Step 1: Failing test.** `tests/formal/test_state_machine.py` asserts the new assertion **names**: the 16 kept, plus the six below; `ConsentConsumedAtMostOnce` and `NoReceiptWithoutVerifiedConsent` are gone.
- [ ] **Step 2: Implement.**
  - Remove `consent_state` and the `consent_*` transitions.
  - Add a `receipt_version` state variable (0 before commit, 2 for new commits) and a `v1_retained` flag (a pre-existing v1 receipt in every initial state).
  - Add the assertions:
    - `OwnerDirectReceiptNeverClaimsConsent`;
    - `ReceiptVersionsDistinguishable`;
    - `V2RequiresOwnerDirect`;
    - `HardRefusalPrecedesRetrieval` (a `retrieved` flag is never set when the budget decision is refuse);
    - `ReservationCommitsAtMostOnce`;
    - `NoHandoffBeforeCommitAndAnchor`.
  - Update `SECURITY-MANIFEST.json` `formal_model.assertions` and `reachable_states` to the **printed** values.
- [ ] **Step 3:** `uv run pytest tests/formal -q -s` prints the new counts, and every assertion holds on every state. Green gate. Commit: `feat: formal model without consent; six replacement invariants`.

---

### Task 8: Admin authority is socket peer credentials alone

**Files:** `ipc/admin.py`, `runtime/composition.py`, `runtime/daemon.py`, and the admin tests

- [ ] **Step 1: Failing test** `test_presence_argument_is_rejected`: `router.dispatch({"cmd": "lock", "args": {"presence": {}}})` returns `MALFORMED_REQUEST` ("unknown argument"). `lock` runs without any proof.
- [ ] **Step 2: Implement.**
  - Delete `PRESENCE_GATED` and its checks, and the `presence_verifier` and `request_verifier` parameters.
  - Delete the `approver` parameter of `serve_admin` and its call site in `daemon.py`.
  - In `AdminRouter.dispatch`, `"presence"` in `args` returns `MALFORMED_REQUEST`.
  - The handlers' `presence` stripping (`_wrapper._bare`, `_args`, the policy `_bare`, the inspect `_body`, the audit handlers) is deleted, so a stray `presence` is refused by the router first.
- [ ] **Step 3: Test dispositions.**
  - Tests that pass `presence` or `presence_verifier` (`test_admin_handlers.py`, `test_ipc.py`, `test_handler_wrapper.py`, `test_phase5a_completeness.py`, `test_cli.py`, `test_phase4a_end_to_end.py`): drop the argument (replaced-by-owner-direct).
  - `test_the_presence_set_is_exactly_the_mutating_set` and `test_the_presence_set_is_unchanged_in_5a`: **delete** (removed-with-consent).
  - `tests/unit/test_admin_summaries.py` and `test_admin_approval.py`: **delete**, together with the modules in Task 9.
  - The smoke's `call()` drops `presence`.
- [ ] **Step 4:** Green gate. Commit: `feat: admin commands run on peer-credential authority alone`.

---

### Task 9: Delete the consent machinery (only now nothing depends on it)

- [ ] **Step 1: Delete.**
  - Source: `consent/` (the whole package, since JCS left in Task 1), `ipc/rendezvous.py` and `keys/pairing.py`.
  - The agent: `agent/`, `scripts/package_agent.sh` and `scripts/consent-agent.plist`.
  - Tests: `tests/agent/`, `tests/integration/test_join_gate.py`, `tests/integration/test_phase4a_touch_id.py`, `tests/unit/test_consent.py`, `tests/unit/test_consent_phase4.py`, `tests/unit/test_prompter.py`, and the pairing parts of `tests/unit/test_keys.py` (delete those test functions; keep the others).
  - The smoke's `phase2_consent` section, and the agent-driven part of `phase4a_catalogue`. The latter is rewritten to call the coordinator directly over the real ingress, with no prompt.
- [ ] **Step 2: Clean up references.**
  - `runtime/lifecycle.py`: remove `bind_consent_socket`, `handshake_consent` and the `consent_ui` parameter.
  - `runtime/daemon.py`: remove consent serving and pairing pins.
  - `composition.py`: remove `serve_consent`, `broker`, `prompter` and `approver` from `RuntimeServices`.
  - `cli.py`: remove the `pair` verb and its tests.
  - `keys/registry.py`: `challenge-key`, `agent-approval-key` and `agent-transport-key` become `retired=True` rows (design §2.5). `provision_missing` skips retired rows; `doctor` reports them as retired, not missing.
  - `tests/conftest.py`: remove the agent-bundle fixtures.
- [ ] **Step 3: Guard.** In `tests/security/test_comms_layering.py`, add `test_no_consent_modules_remain`: no module path under `src/comms` contains `consent`, `rendezvous` or `pairing`, and no import names them.
- [ ] **Step 4:** Green gate. Commit: `chore: delete the consent subsystem (agent, broker, prompter, RV-1, pairing, approver)`.

---

### Task 10: Doctor cleanup

Remove `_check_pairing` and `_check_consent_selftest`. The key check treats `retired` rows as expected. Update `tests/unit/test_doctor.py` and the smoke's doctor rows, whose unmet-check counts change: record the new numbers. Green gate. Commit.

---

### Task 11: Boundary guards: no MCP send primitive, WhatsVault dormancy, the Claude Code ask rule

**Files:** `tests/security/test_ai_boundary.py`, `.claude/settings.json`

```python
# tests/security/test_ai_boundary.py
"""comms spec v0.2 §AI-boundary: exactly what is proven, and no more (design §3, §4)."""

import ast
import importlib.metadata
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WHATSVAULT = ROOT / "transports" / "whatsapp" / "src" / "whatsvault"
SEND_VERBS = ("send", "transmit", "post", "publish", "forward", "reply", "broadcast", "dispatch")
NETWORK = {"urllib.request", "http.client", "httpx", "requests", "aiohttp", "socket"}


def test_no_telegram_mcp_tool_is_a_transmission_primitive():
    from comms.transports.telegram.contract import EXPECTED_TOOLS

    assert len(EXPECTED_TOOLS) == 10
    for name in EXPECTED_TOOLS:
        assert not any(verb in name for verb in SEND_VERBS), name


def test_no_whatsvault_mcp_tool_is_a_transmission_primitive():
    registered = set()
    for path in (WHATSVAULT / "mcp").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith(("whatsapp_", "vault_")):
                registered.add(node.value)
    assert registered, "the WhatsVault tool registry must be discoverable"
    for name in registered:
        assert not any(verb in name for verb in SEND_VERBS), name


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_whatsvault_has_no_network_client():
    for path in WHATSVAULT.rglob("*.py"):
        assert not (_imports(path) & NETWORK), path


def test_whatsvault_providers_are_only_the_protocol_and_the_fake():
    names = sorted(p.name for p in (WHATSVAULT / "providers").glob("*.py"))
    assert names == ["__init__.py", "base.py", "fake_meta.py"]


def test_the_dormant_dispatcher_is_unreachable_from_the_root():
    assert importlib.util.find_spec("apps") is None
    scripts = sorted(e.name for e in importlib.metadata.distribution("telegram-mcp").entry_points)
    assert scripts == ["comms", "telegram-mcp"]
    for path in (ROOT / "src" / "comms").rglob("*.py"):
        assert not any(n.startswith("whatsvault") for n in _imports(path)), path


def test_send_commands_are_always_ask_in_claude_code():
    """A UX interlock, not the authorization boundary (spec v0.2 §AI-boundary)."""
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    ask = settings["permissions"]["ask"]
    for rule in ("Bash(comms campaign send:*)", "Bash(uv run comms campaign send:*)"):
        assert rule in ask
```

Before running, confirm the WhatsVault tool-name prefixes with `grep -n "name=" transports/whatsapp/src/whatsvault/mcp/*.py | head`. If they are not `whatsapp_` / `vault_`, use the real prefixes and record a ruling. The assertion (no send verbs) is what matters.

Write `.claude/settings.json` with `{"permissions": {"ask": ["Bash(comms campaign send:*)", "Bash(uv run comms campaign send:*)", "Bash(comms campaign retry-failed:*)", "Bash(uv run comms campaign retry-failed:*)"]}}`. If the file already exists, merge into it rather than replacing it.

Green gate. Commit: `test: AI-boundary and WhatsVault-dormancy guards; Claude Code always-ask for sends`.

---

### Task 12: `docs/comms-spec-v0.2.md` and the tombstones

- **The spec: a clause-by-clause delta.**
  - v0.1.10 §9.8 (consent), §23C (consent tiers → the soft flag), §23C.3 (the reservation binding), Appendix K (proof v2), §33 (presence) and Gate D (→ the no-MCP-send guard, read-only transport until 5d).
  - WhatsVault `INV-APPROVAL` (retired for comms operator sends; its dormant path is proven unreachable).
  - The AI-boundary section, worded exactly as design §3.
  - The tombstone list.
- **CLAUDE.md:** the "Frozen wires" list drops the challenge/display/RV-1/prompt frames and adds a "Tombstoned" line.
- **Test** `tests/security/test_tombstones.py`: `telegram-mcp-display-v1`, `ApprovalEnvelope`, `RV-1` (as `b"telegram-mcp-rv1"` if that is the domain; check with `git grep -n "rv1\|RV-1" $(cat tests/fixtures/canonical/BASE) -- src`) and the frame names `PROMPT` / `APPROVAL` / `DENIAL` appear **nowhere** under `src/`, and all appear in the spec's tombstone list.

Green gate. Commit: `docs: comms spec v0.2 (owner-direct delta; tombstones)`.

---

### Task 13: Test accounting and evidence

```bash
uv run pytest --collect-only -q -p no:randomly | grep "::" | sort > docs/verification/comms-5b3-collected-after.txt
comm -23 docs/verification/comms-5b3-collected-before.txt docs/verification/comms-5b3-collected-after.txt > /tmp/missing.txt
```

`docs/verification/comms-5b3.md` classifies **every** line of `/tmp/missing.txt` as `removed-with-consent` (with the deleted module or behaviour) or `replaced-by-owner-direct` (naming the replacing test). **Unexpectedly missing = 0.** A committed test pins it: `tests/security/test_test_accounting.py` re-derives the missing set from the two manifests and asserts it equals the union of the two classified lists in `docs/verification/comms-5b3-classification.json`.

The evidence also records:
- the R1–R3 rehearsals;
- the formal counts;
- v1 verification after migration;
- both gates' output lines;
- the rulings.

Update `AGENT.md` and `CHANGELOG.md`. Run the full gate plus the WhatsVault gate, then commit.
