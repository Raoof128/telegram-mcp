# Comms 5b-4 — Campaign Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Revision 3.** Rev 2 folded in the self-gauntlet (P1–P14). Rev 3 folds in the owner's gauntlet of rev 2 (G1–G30 below) and three new measurements (M9–M11).

**Goal:** A transport-neutral campaign core in `comms.core`. It covers:

- an encrypted `comms.db`;
- the location/audience/recipient directory;
- campaigns with an immutable generation per freeze;
- deduplicated, idempotent delivery jobs with attempts;
- one monotonic reducer and a derived delivery summary;
- cancel, retry, resolution and provider updates;
- scheduling;
- crash recovery that never resends blindly.

All of it is exercised end to end against fake transports only.

**Architecture:** Dependencies run one way:

1. shared primitives (`canonical`, `opaque`, `refs`, `timeutil`, `domains`);
2. storage;
3. directory;
4. drafts and events;
5. the transport interface;
6. the reducer;
7. freeze;
8. engine;
9. operations;
10. scheduling and recovery.

Every database write is a `BEGIN IMMEDIATE` transaction through one helper, which also refuses external I/O while it is open. The only external side effect in the package is `DeliveryTransport.deliver`, called with no transaction open, and it is the only place an exception is converted into a delivery outcome.

**Tech Stack:** Python 3.12, `sqlcipher3` 0.6.2 (SQLCipher 4.12.0 community, SQLite 3.51.1, already pinned), pytest, ruff, mypy. No new dependency.

**Spec:** [`2026-09-24-comms-5b4-campaign-core-design.md`](../specs/2026-09-24-comms-5b4-campaign-core-design.md) **rev 4**, committed at `b9d5e36`, SHA-256 `dc61a47c48d5654e90fd94da69dc22a0f4106ea7d83bb4e8c2c6f1500ba91851`. Section references below (§n, Rn, Sn) are to that document. **Pre-flight (G10):** before Task 1, `shasum -a 256` of the spec must equal that value. Otherwise stop: the plan is being executed against a different design. Task 15 records the check.

**Branch:** `comms-5b4`, from `main` at `0973915`.

## Measured before writing

| Risk | Result |
|---|---|
| **M1: SQLCipher fail-closed behaviour** (4.12.0 community) | `PRAGMA key` accepts a wrong key; the failure appears only at the first read (`DatabaseError: file is not a database`). A keyless open of a new file creates a **plaintext** database. An empty key raises `OperationalError`. `cipher_version` returns `4.12.0 community`. `BEGIN IMMEDIATE`, `foreign_keys` and `temp_store=MEMORY` work. The default `journal_mode` is `delete`. |
| **M2: cost** | Opening with a raw 32-byte hex key (no KDF): 28 ms. Inserting 5,000 jobs plus 5,000 origin rows with payloads in one `BEGIN IMMEDIATE`: 88 ms. The freeze budget is **3 s for 5,000 endpoints**, about 30× headroom. |
| **M3: the core guards** | Inside `src/comms/core`, `test_comms_layering.py` forbids imports of `comms.transports`, `telegram_mcp` and `whatsvault`, any string containing those names, and `import_module`/`__import__`/`importlib.util`. The transport names `"telegram"` and `"whatsapp"` are permitted. |
| **M4: prefix collisions** | WhatsVault's `ids.PREFIXES` includes `aud` and `job`. Telegram's `REF_PREFIXES` has nine live prefixes plus the tombstoned `tgu_`. Core prefixes collide with neither. |
| **M5: the moves** | 32 files import `transports.telegram.opaque` or `.canonical`. Both modules use the stdlib only and hold no `telegram_mcp` literal. |
| **M6: schema mechanics** | The circular FKs work and `foreign_key_check` is empty. The `IFNULL` expression unique index and partial unique indexes both enforce. `RAISE(ABORT)` and every constraint raise **`sqlcipher3.dbapi2.IntegrityError`, which is not `sqlite3.IntegrityError`**. |
| **M7: locking** | A second `BEGIN IMMEDIATE` on another connection waits 5 s, then raises `OperationalError: database is locked`. Connections are thread-bound, so races are tested as interleaved transactions on two connections in one thread. |
| **M8: tooling** | `mypy` fails on `import sqlcipher3` (`import-untyped`). The frozen-protocol collector matches only `^(telegram-mcp-\|tg-mcp-\|tgml1)`, so `comms-*` wire domains are unguarded today. |
| **M9: fault injection without a seam** (new) | A `TEMP` trigger on a main table (`CREATE TEMP TRIGGER … ON main.t … RAISE(ABORT, 'planted')`) aborts the statement inside an open `BEGIN IMMEDIATE`. It raises `sqlcipher3.dbapi2.IntegrityError`, the transaction is still open for `ROLLBACK`, and every earlier write in it is undone. The trigger lives in `sqlite_temp_master` only: it is invisible to other connections and never persisted. Tests therefore inject a mid-transaction failure through a real dependency, and `src` needs no fault hook for atomicity tests. |
| **M10: JSON1** (new) | `json_extract('["cau_a","dst_b"]', '$[#-1]')` returns `dst_b`. SQLite is 3.51.1. Origin-path `CHECK`s and triggers may use JSON1. |
| **M11: migration atomicity** (new) | Inside one `BEGIN IMMEDIATE`, a `CREATE TABLE`, a `schema_version` insert and a failing statement roll back together: no new table and no version row. |

## Gauntlet of plan rev 2 (the owner's review; all folded in below)

The review raised 30 items: 8 blockers, 14 hardening, 7 small and 1 provenance item.

| Verdict | Items |
|---|---|
| Adopted | 25 |
| Refined | G3, G5 |
| Provenance claim wrong but its gate adopted | G10 |
| Already true, now pinned by a test | G24 |

Verification of the plan found five more items (F1–F5).

| # | Sev | Finding | Verdict and receipt | Fix (task) |
|---|---|---|---|---|
| G1 | 🔴 | Task 8 wrote job states before the reducer existed (Task 9). | Adopted. Rev 2's Task 8 unschedule/cancel versus Task 9. | The reducer is now **Task 8**, tested on direct schema fixtures. Freeze is **Task 9**. |
| G2 | 🔴 | The directory refused a second endpoint per identity, so the dedupe tests were unconstructible. | Adopted. Rev 2 Task 4 versus Tasks 5/8. See F2 for a second defect in the same rule. | Identity rows are **shared** (get-or-create); at most one *enabled* destination and one *enabled* contact point per identity (spec S1). Task 3 schema, Task 4. |
| G3 | 🔴 | "No applied transition lowers rank" contradicts `RETRY`. | **Refined.** The review missed that `ACCEPTED → FAILED_PERMANENT` also lowers rank. An `(attempt, rank)` order still fails for `RETRY`, because `attempt_count` increments at claim, not at retry. | The invariant names its exceptions exactly: `NAMED_DESCENTS = {(FAILED_TRANSIENT, RETRY) → PENDING, (ACCEPTED, PROVIDER FAILED_PERMANENT, current attempt) → FAILED_PERMANENT}`. Every other applied transition is rank-non-decreasing, and each named descent is tested to require its precondition. Task 8. |
| G4 | 🔴 | Transport isolation caught every `Exception`. | Adopted. | Only an exception raised by `deliver` becomes `OUTCOME_UNKNOWN`; all other exceptions propagate (spec S7). Task 10. |
| G5 | 🔴 | `still_valid` purity was unstated. | **Refined.** Spec §4 already said "pure… no network, no filesystem side effect", but the plan's interface didn't carry it, and a file *read* or another database was still allowed. | Tightened to "no I/O of any kind" (spec S3), written into the `DeliveryTransport` docstring. The fakes' `still_valid` records any I/O-hook call. Task 6. |
| G6 | 🔴 | A webhook arriving before `provider_message_ref` is persisted was lost. | Adopted. | `pending_match` events are reconciled through the reducer when the result binds the reference, in the same transaction. Ambiguous matches are refused (spec S4). Tasks 3, 8, 11. |
| G7 | 🔴 | `audience_digest == recipient_digest`. | Adopted. Rev 2 Task 8's text gave both the same preimage. | `target_digest`, `recipient_digest` and `snapshot_digest`, each with its own domain (spec S5). Task 9. |
| G8 | 🔴 | Event validation by string shape let `"hello"` through. | Adopted. | A typed field table: each key has a fixed type and a finite domain, and `reason` is a closed `ReasonCode` set (spec S6). Task 7. |
| G9 | 🔴 | Crash seams named in rev 2's Tasks 8/9 did not exist. `EngineCrash` and `SimulatedCrash` were confused. | Adopted. | Freeze and reducer atomicity use a planted `TEMP` trigger (M9), so `src` has no hook. Engine seam tests expect `EngineCrash`; `SimulatedCrash` belongs only to the fakes' `crash_after_accept`. Tasks 8, 9, 10. |
| G10 | 🔴 | Plan and spec revision were not pinned. | The review said the design file was "Revision 1"; that was a stale copy. Rev 3 was `70b4290`, now rev 4 `b9d5e36`. The gate is adopted. | Spec commit and SHA-256 are pinned in the header, with a pre-flight check. |
| G11 | 🟠 | The fake Telegram normalizer equated `chat:N` with `user:N` for any chat. | Adopted. Real Telegram raw peer IDs can coincide across kinds. | Marked IDs (spec S2): `user:N`, `private:N` → `N`; `group:N` → `-N`; `channel:N` → `-100N`. Task 6. |
| G12 | 🟠 | `Candidate.endpoint` was a single endpoint. | Adopted. | `Candidate.endpoint_refs: tuple[str, ...]`, sorted. Task 5. |
| G13 | 🟠 | No test for a direct destination whose location is later disabled. | Adopted. | Task 10 test. |
| G14 | 🟠 | A job could bind the wrong endpoint to the wrong identity. | Adopted. | `delivery_jobs` drops `endpoint_ref`. Each `job_origins` row stores `endpoint_ref`, with `CHECK(json_extract(path,'$[#-1]') = endpoint_ref)`. A `BEFORE INSERT` trigger refuses an origin whose endpoint does not resolve to the job's `(transport, identity_id)`, and another refuses a job or endpoint whose identity's transport differs (spec S10). Task 3. |
| G15 | 🟠 | `payload` / `payload_digest` / `skip_reason` were not tied together. | Adopted. | `CHECK`s for all three, plus the digest's shape. Task 3. |
| G16 | 🟠 | `current_generation_id` ownership was unprotected, and it was never cleared. | Adopted. | An ownership trigger, `CHECK((summary IS NULL) = (current_generation_id IS NULL))`, and cleared by unschedule and scheduled-cancel. Tasks 3, 9. |
| G17 | 🟠 | = G9 (seams). | Adopted. | See G9. |
| G18 | 🟠 | `recover` could not resume without transports. | Adopted. | `recover` returns the resumable campaign refs and the caller executes them (spec S9). Task 12. |
| G19 | 🟠 | The model's retry cap (2) differed from the library's (5). | Adopted. | `MODEL_RETRY_CAP = 2`; the walk calls `retry_failed(..., cap=2)`. Task 14. |
| G20 | 🟠 | The model had no eligibility, so it could not prove H1. | Adopted. | Each job carries an abstract `eligible` flag with an `invalidate` operation, and a property: no claim of an ineligible job. Task 14. |
| G21 | 🟠 | "`OUTCOME_UNKNOWN` never resent" was imprecise. | Adopted. | "Gains another attempt only after `RESOLVE_NOT_SENT`" (spec S12). Task 14. |
| G22 | 🟠 | `FAILED_TRANSIENT`'s safety meaning was not in the contract. | Adopted. Spec §7.2 had it; the plan's `ResultKind` did not. | Written as `ResultKind`'s docstring; 5d conformance is named. Task 6. |
| G23 | 🟠 | Migrations were not tested for crash atomicity. | Adopted (M11). | One `write_tx` per migration; failed-migration tests. Task 2. |
| G24 | 🟡 | Is `campaign.summary_changed` in `EVENT_TYPES`? | Already true (spec §9 lists it). | Pinned by `test_event_types_are_exactly_the_spec_list`. Task 7. |
| G25 | 🟠 | Hot-path indexes were missing. | Adopted. | Indexes on `job_origins(job_id)`, `destinations(location_id)`, `generations(campaign_id)`, `contact_points(recipient_id)`, `location_members(recipient_id)`, the attempt provider-ref and the pending-match lookup, plus an `EXPLAIN QUERY PLAN` test. Task 3. |
| G26 | 🟡 | Evidence listed M1–M5. | Adopted. | M1–M11. Task 15. |
| G27 | 🟡 | `FrozenDelivery` repr could print an identity. | Adopted. | `field(repr=False)` on every identity, content and payload field; a repr-canary test. Task 6. |
| G28 | 🟡 | A duplicate provider event with different contents was undefined. | Adopted. | `DUPLICATE_CONFLICT`, refused and recorded (spec S11). Task 11. |
| G29 | 🟡 | Randomness of the 32-byte key was unstated. | Adopted as a contract. Core cannot test entropy. | `open_comms_db`'s docstring: CSPRNG key material, never password-derived or padded; supplied by 5e. |
| G30 | 🟡 | Wire-domain constants should be central. | Adopted. | `comms/core/domains.py` holds every core `comms-*` domain. The wire test requires that core domains appear nowhere else. Tasks 1, 9. |
| F1 | 🔴 | Unscheduling a generation with a skipped job would set it `CANCELLED` and violate the payload `CHECK` (skipped jobs have no payload). | Found while verifying G1. | Unschedule and scheduled-cancel cancel only `PENDING` jobs (spec S8). Task 9 test with a skipped job. |
| F2 | 🔴 | `identity_id UNIQUE` on endpoints made R11's "disable old, create new" impossible for the same number: moving a person's WhatsApp to a new recipient was refused. | Found while verifying G2. | Partial unique indexes `WHERE enabled = 1` (G2). Task 4 test. |
| F3 | 🟠 | `provider_events.reported_status` had no `CHECK`. | Rev 2 schema. | `CHECK (reported_status IN ('ACCEPTED','DELIVERED','FAILED_PERMANENT'))`. Task 3. |
| F4 | 🟠 | Claim and attempt insert were two steps, so claim could succeed without an attempt. | Rev 2 Task 10 step 1. | `reduce(CLAIM)` performs the compare-and-set **and** inserts the attempt, returning its id. Task 8. |
| F5 | 🟡 | `campaign.transport_completed` had no emitter. | Spec §9 versus rev 2 Task 10. | The reducer emits it when a transport's last non-terminal job in the generation leaves `PENDING`/`IN_FLIGHT`. Task 8. |

Rev 2's self-gauntlet P1–P14 remain folded in. They are referenced below where they apply.

## Global Constraints

- **Fake transports only.** Nothing in `src/` opens a network connection. Fakes live in `tests/`.
- **No external side effect, network I/O, `await` or filesystem access inside a `comms.db` transaction** (R18). The adapter methods `normalize`, `prepare` and `still_valid` perform no I/O of any kind (spec S3). `DeliveryTransport.deliver` is called only with no transaction open: `engine.py` calls `io_guard(conn)` immediately before it (a production check, not a test flag).
- **The one exception boundary** (spec S7): only an exception raised *by* `deliver` becomes `OUTCOME_UNKNOWN`. Every other exception propagates and fails closed.
- **One copy of each shared rule.** `canonical` and `opaque` move into core; core wire domains live only in `comms/core/domains.py`. `reduce` is the only writer of `delivery_jobs.state`.
- **Opaque refs outside the database.** No return value, exception message, event, `repr` or log record contains a platform identity, message body or credential.
- **UTC only.** Naive datetimes are refused with `ValueError`; stored times are ISO-8601 `Z`.
- **Core text never names a transport package** (P14): no `comms.transports`, `telegram_mcp` or lowercase `whatsvault` in any core string, docstrings included.
- **sqlcipher3 errors are not sqlite3 errors** (P5): tests catch `sqlcipher3.dbapi2.*`.
- **Fail closed; commit only on a green gate** (the fail-fast gate script). No test-only branch in `src`. Atomicity faults are planted with `TEMP` triggers (M9). The engine's delivery crash points are a constructor argument that production never supplies (the coordinator's `crash_at` pattern).
- **The Telegram and WhatsVault behaviour does not change.** The WhatsVault subtree stays byte-identical; Telegram gains only import-path changes.
- **Full gate:** `uv sync --locked`, the contracts check, `pytest`, the smoke, formal, ruff, format, `mypy src/comms src/telegram_mcp` and build, plus the WhatsVault gate (539 passed).

## Review Focus

1. **A crash between `PENDING → IN_FLIGHT → deliver → durable outcome` produces a resend or a lost job.** *Test: Task 12's crash table, and Task 14's property that an unknown job gets a new attempt only after `RESOLVE_NOT_SENT`.*
2. **A provider update races the synchronous result.** *Test: Task 11, `test_webhook_before_result_is_reconciled_when_the_result_binds`.*
3. **The same delivery identity receives two messages on one transport**, through `dst_` and `rct_`, two audiences or a reschedule. *Test: Task 9, `test_same_identity_via_destination_and_contact_point_gets_one_job` and `test_edit_and_reschedule_mints_new_keys`.*
4. **A platform identity or body leaks** into an event, exception, `repr`, return value, log record or unencrypted side file. *Test: Task 13's canary sweep, and Task 7's typed event fields.*
5. **A core fault is disguised as a provider outcome.** *Test: Task 10, `test_a_core_error_propagates_and_is_never_an_outcome`.*

---

### Task 1: Shared primitives into core; guards

**Files:**
- Move (`git mv`): `src/comms/transports/telegram/canonical.py` → `src/comms/core/canonical.py`, and `src/comms/transports/telegram/opaque.py` → `src/comms/core/opaque.py`.
- Modify every importer: 32 files, found with `grep -rln "transports.telegram.opaque\|transports.telegram.canonical" src tests scripts`. Repoint import statements only, with an AST pass.
- Create `src/comms/core/refs.py`, `src/comms/core/timeutil.py`, `src/comms/core/domains.py`.
- Modify `tests/security/test_comms_layering.py` and `tests/security/test_ai_boundary.py`.
- Create `tests/security/test_comms_wire_frozen.py` (P2, G30).
- Create `tests/core/__init__.py`, `tests/core/test_refs_and_time.py` and `tests/unit/test_core_moves.py`.

**Interfaces (produced):**
```python
# comms/core/refs.py
CORE_PREFIXES: dict[str, str] = {"location": "loc_", "destination": "dst_", "recipient": "rcp_",
    "contact_point": "rct_", "audience": "cau_", "campaign": "cmp_", "generation": "gen_",
    "job": "djb_", "attempt": "dat_", "event": "cev_"}
def mint(kind: str) -> str            # mint_opaque_ref(CORE_PREFIXES[kind])
def check(ref: str, kind: str) -> str # shape and prefix; ValueError("unexpected ref") otherwise
def kind_of(ref: str) -> str          # the kind whose prefix the ref carries; ValueError otherwise
# comms/core/timeutil.py
def utc(dt: datetime) -> datetime     # refuse naive (ValueError), normalize to UTC
def iso(dt: datetime) -> str          # "YYYY-MM-DDTHH:MM:SS.ffffffZ"
def parse(text: str) -> datetime      # inverse of iso; refuses anything else
# comms/core/domains.py — the only home of core wire domains (G30); Task 9 adds four
```

- [ ] **Step 0 (pre-flight, G10):** `shasum -a 256 docs/superpowers/specs/2026-09-24-comms-5b4-campaign-core-design.md` must equal `dc61a47c48d5654e90fd94da69dc22a0f4106ea7d83bb4e8c2c6f1500ba91851`.
- [ ] **Step 1: Failing tests.**
  - `tests/unit/test_core_moves.py::test_canonical_and_opaque_are_single_copies_in_core` checks that:
    - `comms.core.canonical.jcs_dumps` and `comms.core.opaque.mint_opaque_ref` import;
    - the old Telegram files do not exist;
    - no file under `src/` outside core defines a function named `jcs_dumps` or `mint_opaque_ref` (AST scan).
  - `test_core_canonical_is_byte_identical_to_the_pinned_base`: the existing BASE oracle, run over every vector in `tests/fixtures/canonical/jcs_vectors.json`.
  - `tests/core/test_refs_and_time.py`:
    - `test_core_prefixes_are_disjoint_from_telegram_and_whatsvault` checks against `REF_PREFIXES ∪ {"tgu_"}` and `{p + "_" for p in whatsvault.ids.PREFIXES}`;
    - `test_core_prefixes_are_unique`;
    - `test_mint_check_and_kind_of_round_trip_and_refuse_wrong_kind`;
    - `test_naive_datetimes_are_refused`;
    - `test_offsets_normalize_to_utc`: `2026-10-01T18:00:00+10:00` → `2026-10-01T08:00:00.000000Z`;
    - `test_iso_parse_round_trip` and `test_parse_refuses_non_canonical_text`.
  - `tests/security/test_comms_layering.py`: `test_core_has_no_production_implementation` becomes `test_core_is_transport_neutral`. The import, string and dynamic-import guards keep running over every core file.
  - `tests/security/test_comms_wire_frozen.py`: collect every `str`/`bytes` constant under `src/comms` matching `^comms-` and assert:
    - the multiset equals `PINNED + ADDED_IN_5B4` exactly (today `{"b'comms-call-binding/v1\\x00'": 1}` and an empty addition);
    - every `^comms-` constant under `src/comms/core` is defined in `comms/core/domains.py` and nowhere else (G30);
    - plus a planted-violation test.
  - `tests/security/test_ai_boundary.py::test_no_ai_surface_imports_the_campaign_core` (R21):
    - the modules checked are `src/comms/transports/*/{server,dispatch,sensitive_dispatch}.py`, any `mcp/` package, and `transports/whatsapp/apps/mcp/*.py`;
    - none of them may import anything starting with `comms.core.campaigns` or `comms.core.delivery`;
    - plus a planted violation.
- [ ] **Step 2: Run** `uv run pytest -q -p no:randomly tests/unit/test_core_moves.py tests/core tests/security/test_comms_layering.py tests/security/test_ai_boundary.py tests/security/test_comms_wire_frozen.py`. **Expected:** the move tests and refs/time tests fail because the modules are absent. The AI-boundary guard passes vacuously until campaigns exist; its planted test proves it has teeth.
- [ ] **Step 3: Implement.** `git mv` both files, AST-repoint the imports, and write `refs.py`, `timeutil.py` and an empty `domains.py`.
- [ ] **Step 4:** The targeted tests pass; the full gate is green. Commit: `refactor: TG-JCS-v1 and the opaque-ref minter move into comms.core (single copies); core prefix registry, time rules, wire-domain home`.

---

### Task 2: `comms.db`, the SQLCipher open and atomic migrations

**Files:**
- Create `src/comms/core/storage/__init__.py`, `db.py` and `migrations.py`.
- Modify `pyproject.toml` (P1).
- Test: `tests/core/test_comms_db.py`.

**Interfaces:**
```python
class CommsDbKeyError(Exception)          # fixed message: "comms database key is invalid"
class TransactionIOError(RuntimeError)
def open_comms_db(path: Path, key: bytes) -> sqlcipher3.Connection
    # docstring (G29): key is 32 bytes of CSPRNG key material supplied by 5e — never password-derived or padded
@contextmanager
def write_tx(conn) -> Iterator[Connection]   # BEGIN IMMEDIATE … COMMIT / ROLLBACK; RuntimeError if conn.in_transaction (P11)
def io_guard(conn) -> None                   # TransactionIOError if conn.in_transaction
@dataclass(frozen=True) class Migration: version: int; statements: tuple[str, ...]
def migrate(conn, migrations: tuple[Migration, ...] = MIGRATIONS) -> int
    # each pending migration: ONE write_tx running its statements and inserting its version row (G23, M11)
```

`open_comms_db`, in this order (§2, M1):
```python
if not isinstance(key, bytes) or len(key) != 32: raise CommsDbKeyError("comms database key is invalid")
fresh = not path.exists()
conn = sqlcipher3.connect(str(path), isolation_level=None, timeout=5.0)
conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
if not conn.execute("PRAGMA cipher_version").fetchone()[0]: conn.close(); raise CommsDbKeyError(...)
try: conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
except sqlcipher3.dbapi2.DatabaseError: conn.close(); raise CommsDbKeyError(...) from None
conn.execute("PRAGMA foreign_keys = ON"); conn.execute("PRAGMA temp_store = MEMORY")
if fresh:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
    if path.read_bytes()[:16] == b"SQLite format 3\x00": conn.close(); path.unlink(); raise CommsDbKeyError(...)
```

- [ ] **Step 1: Failing tests** in `tests/core/test_comms_db.py`:
  - `test_a_short_or_missing_key_is_refused_before_any_file_exists`, for the keys `b""`, 31 bytes, 33 bytes and a `str`;
  - `test_a_new_file_is_encrypted`, which prints `cipher_version` with `-s`;
  - `test_a_wrong_key_fails_closed_at_open`;
  - `test_a_keyless_sqlite_open_of_the_file_fails`;
  - `test_the_key_never_appears_in_errors`;
  - `test_write_tx_commits_and_rolls_back` and `test_write_tx_refuses_nesting`;
  - `test_io_guard_refuses_inside_a_transaction`;
  - `test_migrate_is_rerunnable_and_versioned`;
  - `test_failed_migration_does_not_advance_version` and `test_failed_migration_leaves_no_partial_schema` (G23): the second statement of a two-statement migration is invalid, and afterwards neither the first table nor the version row exists;
  - `test_lock_contention_raises_after_the_timeout`: two connections in one thread, with the timeout shortened on the test's own raw connection.
- [ ] **Step 2: Run.** **Expected:** all fail because the module is absent.
- [ ] **Step 3: Implement.** Add to `pyproject.toml`, beside the existing overrides (P1):
  ```toml
  [[tool.mypy.overrides]]
  module = "sqlcipher3.*"
  ignore_missing_imports = true
  ```
- [ ] **Step 4:** The tests pass and the gate is green. Commit: `feat: comms.db on SQLCipher, fail-closed open, write_tx, io_guard and atomic migrations`.

---

### Task 3: Schema v1 and its constraints

**Files:**
- Modify `src/comms/core/storage/migrations.py` (`MIGRATIONS = (Migration(1, SCHEMA_V1),)`).
- Create `tests/core/schema_fixtures.py`: raw-SQL builders for tests only.
- Test: `tests/core/test_schema.py`.

`SCHEMA_V1`, every statement:

```sql
CREATE TABLE locations (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)), created_at TEXT NOT NULL);
CREATE TABLE recipients (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)), created_at TEXT NOT NULL);
CREATE TABLE delivery_identities (id INTEGER PRIMARY KEY,
  transport TEXT NOT NULL CHECK (transport IN ('telegram','whatsapp')), identity TEXT NOT NULL,
  UNIQUE (transport, identity));
CREATE TABLE destinations (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
  transport TEXT NOT NULL CHECK (transport = 'telegram'), platform_identity TEXT NOT NULL,
  identity_id INTEGER NOT NULL REFERENCES delivery_identities(id) ON DELETE RESTRICT,
  display_name TEXT NOT NULL, capabilities TEXT NOT NULL DEFAULT '{}',
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)), created_at TEXT NOT NULL);
CREATE INDEX destinations_location ON destinations (location_id);
CREATE UNIQUE INDEX destinations_one_enabled_per_identity ON destinations (identity_id) WHERE enabled = 1;
CREATE TABLE contact_points (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  recipient_id INTEGER NOT NULL REFERENCES recipients(id) ON DELETE RESTRICT,
  transport TEXT NOT NULL CHECK (transport IN ('telegram','whatsapp')), platform_identity TEXT NOT NULL,
  identity_id INTEGER NOT NULL REFERENCES delivery_identities(id) ON DELETE RESTRICT,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)), opted_out_at TEXT, created_at TEXT NOT NULL);
CREATE INDEX contact_points_recipient ON contact_points (recipient_id);
CREATE UNIQUE INDEX contact_points_one_enabled_per_transport ON contact_points (recipient_id, transport) WHERE enabled = 1;
CREATE UNIQUE INDEX contact_points_one_enabled_per_identity ON contact_points (identity_id) WHERE enabled = 1;
CREATE TABLE location_members (location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
  recipient_id INTEGER NOT NULL REFERENCES recipients(id) ON DELETE RESTRICT, PRIMARY KEY (location_id, recipient_id));
CREATE INDEX location_members_recipient ON location_members (recipient_id);
CREATE TABLE audiences (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE audience_members (id INTEGER PRIMARY KEY,
  audience_id INTEGER NOT NULL REFERENCES audiences(id) ON DELETE RESTRICT,
  member_location_id INTEGER REFERENCES locations(id) ON DELETE RESTRICT,
  member_audience_id INTEGER REFERENCES audiences(id) ON DELETE RESTRICT,
  member_destination_id INTEGER REFERENCES destinations(id) ON DELETE RESTRICT,
  member_recipient_id INTEGER REFERENCES recipients(id) ON DELETE RESTRICT,
  CHECK ((member_location_id IS NOT NULL) + (member_audience_id IS NOT NULL)
       + (member_destination_id IS NOT NULL) + (member_recipient_id IS NOT NULL) = 1),
  CHECK (member_audience_id IS NULL OR member_audience_id <> audience_id));
CREATE UNIQUE INDEX audience_members_unique ON audience_members (audience_id,
  IFNULL(member_location_id,0), IFNULL(member_audience_id,0), IFNULL(member_destination_id,0), IFNULL(member_recipient_id,0));
CREATE TABLE campaigns (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
  lifecycle TEXT NOT NULL CHECK (lifecycle IN ('DRAFT','READY','SCHEDULED','SENDING','COMPLETE','CANCELLED')),
  content TEXT NOT NULL, targets TEXT NOT NULL, options TEXT NOT NULL,
  summary TEXT CHECK (summary IS NULL OR summary IN ('IN_PROGRESS','INDETERMINATE','SENT','PARTIAL','CANCELLED','FAILED')),
  current_generation_id INTEGER REFERENCES generations(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  CHECK ((summary IS NULL) = (current_generation_id IS NULL)));
CREATE TABLE generations (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL, send_at TEXT NOT NULL, content TEXT NOT NULL,
  snapshot_digest TEXT NOT NULL CHECK (length(snapshot_digest) = 64),
  status TEXT NOT NULL CHECK (status IN ('active','discarded')));
CREATE INDEX generations_campaign ON generations (campaign_id);
CREATE TABLE delivery_jobs (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  generation_id INTEGER NOT NULL REFERENCES generations(id) ON DELETE RESTRICT,
  transport TEXT NOT NULL CHECK (transport IN ('telegram','whatsapp')),
  identity_id INTEGER NOT NULL REFERENCES delivery_identities(id) ON DELETE RESTRICT,
  idempotency_key TEXT NOT NULL UNIQUE CHECK (length(idempotency_key) = 64),
  payload BLOB, payload_digest TEXT, skip_reason TEXT,
  state TEXT NOT NULL CHECK (state IN ('PENDING','IN_FLIGHT','ACCEPTED','DELIVERED','FAILED_TRANSIENT',
    'FAILED_PERMANENT','OUTCOME_UNKNOWN','CANCELLED','SKIPPED_PLATFORM_POLICY','SKIPPED_REVALIDATION')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  UNIQUE (generation_id, transport, identity_id),
  CHECK ((payload IS NULL) = (payload_digest IS NULL)),
  CHECK ((payload IS NULL) = (skip_reason IS NOT NULL)),
  CHECK ((state = 'SKIPPED_PLATFORM_POLICY') = (payload IS NULL)),
  CHECK (payload_digest IS NULL OR (length(payload_digest) = 64 AND payload_digest NOT GLOB '*[^0-9a-f]*')));
CREATE TABLE job_origins (id INTEGER PRIMARY KEY,
  job_id INTEGER NOT NULL REFERENCES delivery_jobs(id) ON DELETE RESTRICT,
  endpoint_ref TEXT NOT NULL, path TEXT NOT NULL,          -- TG-JCS-v1 array of refs, target first, endpoint last
  CHECK (json_valid(path) AND json_type(path) = 'array' AND json_array_length(path) >= 1),
  CHECK (json_extract(path, '$[#-1]') = endpoint_ref));
CREATE INDEX job_origins_job ON job_origins (job_id);
CREATE TABLE delivery_attempts (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  job_id INTEGER NOT NULL REFERENCES delivery_jobs(id) ON DELETE RESTRICT,
  attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1), started_at TEXT NOT NULL, finished_at TEXT,
  outcome TEXT CHECK (outcome IS NULL OR outcome IN ('ACCEPTED','DELIVERED','FAILED_TRANSIENT','FAILED_PERMANENT','OUTCOME_UNKNOWN')),
  provider_message_ref TEXT, UNIQUE (job_id, attempt_no),
  CHECK ((finished_at IS NULL) = (outcome IS NULL)));
CREATE INDEX delivery_attempts_provider_ref ON delivery_attempts (provider_message_ref) WHERE provider_message_ref IS NOT NULL;
CREATE TABLE provider_events (id INTEGER PRIMARY KEY,
  transport TEXT NOT NULL CHECK (transport IN ('telegram','whatsapp')), provider_event_ref TEXT NOT NULL,
  provider_message_ref TEXT NOT NULL,
  job_id INTEGER REFERENCES delivery_jobs(id) ON DELETE RESTRICT,
  attempt_id INTEGER REFERENCES delivery_attempts(id) ON DELETE RESTRICT,
  reported_status TEXT NOT NULL CHECK (reported_status IN ('ACCEPTED','DELIVERED','FAILED_PERMANENT')),
  disposition TEXT NOT NULL CHECK (disposition IN ('applied','recorded','refused','pending_match')),
  received_at TEXT NOT NULL, UNIQUE (transport, provider_event_ref),
  CHECK (disposition <> 'pending_match' OR attempt_id IS NULL),
  CHECK (disposition NOT IN ('applied','recorded') OR attempt_id IS NOT NULL));
CREATE INDEX provider_events_pending ON provider_events (transport, provider_message_ref) WHERE disposition = 'pending_match';
CREATE TABLE campaign_events (event_seq INTEGER PRIMARY KEY AUTOINCREMENT, event_ref TEXT NOT NULL UNIQUE,
  campaign_ref TEXT, event_type TEXT NOT NULL, ts TEXT NOT NULL, payload TEXT NOT NULL);
-- Transport agreement (G14): an endpoint or job must name its identity's transport.
CREATE TRIGGER destinations_transport_matches_identity BEFORE INSERT ON destinations
  WHEN (SELECT transport FROM delivery_identities WHERE id = NEW.identity_id) IS NOT NEW.transport
  BEGIN SELECT RAISE(ABORT, 'endpoint transport mismatch'); END;
CREATE TRIGGER contact_points_transport_matches_identity BEFORE INSERT ON contact_points
  WHEN (SELECT transport FROM delivery_identities WHERE id = NEW.identity_id) IS NOT NEW.transport
  BEGIN SELECT RAISE(ABORT, 'endpoint transport mismatch'); END;
CREATE TRIGGER delivery_jobs_transport_matches_identity BEFORE INSERT ON delivery_jobs
  WHEN (SELECT transport FROM delivery_identities WHERE id = NEW.identity_id) IS NOT NEW.transport
  BEGIN SELECT RAISE(ABORT, 'job transport mismatch'); END;
-- An origin's endpoint must resolve to its job's (transport, identity) (G14).
CREATE TRIGGER job_origins_endpoint_matches_job BEFORE INSERT ON job_origins
  WHEN NOT EXISTS (SELECT 1 FROM delivery_jobs j WHERE j.id = NEW.job_id AND (
    EXISTS (SELECT 1 FROM destinations d WHERE d.ref = NEW.endpoint_ref AND d.identity_id = j.identity_id AND d.transport = j.transport)
    OR EXISTS (SELECT 1 FROM contact_points c WHERE c.ref = NEW.endpoint_ref AND c.identity_id = j.identity_id AND c.transport = j.transport)))
  BEGIN SELECT RAISE(ABORT, 'origin endpoint does not match its job'); END;
-- The current generation belongs to its campaign and is active (G16).
CREATE TRIGGER campaigns_insert_has_no_generation BEFORE INSERT ON campaigns
  WHEN NEW.current_generation_id IS NOT NULL BEGIN SELECT RAISE(ABORT, 'generation belongs to another campaign'); END;
CREATE TRIGGER campaigns_generation_owned BEFORE UPDATE OF current_generation_id ON campaigns
  WHEN NEW.current_generation_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM generations g
    WHERE g.id = NEW.current_generation_id AND g.campaign_id = NEW.id AND g.status = 'active')
  BEGIN SELECT RAISE(ABORT, 'generation belongs to another campaign'); END;
-- Immutability (R11, §7.1).
CREATE TRIGGER destinations_identity_immutable BEFORE UPDATE OF transport, platform_identity, identity_id, location_id, ref
  ON destinations BEGIN SELECT RAISE(ABORT, 'destination identity is immutable'); END;
CREATE TRIGGER contact_points_identity_immutable BEFORE UPDATE OF transport, platform_identity, identity_id, recipient_id, ref
  ON contact_points BEGIN SELECT RAISE(ABORT, 'contact point identity is immutable'); END;
CREATE TRIGGER delivery_identities_immutable BEFORE UPDATE ON delivery_identities
  BEGIN SELECT RAISE(ABORT, 'delivery identity is immutable'); END;
CREATE TRIGGER generations_frozen BEFORE UPDATE OF ref, campaign_id, created_at, send_at, content, snapshot_digest
  ON generations BEGIN SELECT RAISE(ABORT, 'generation is frozen'); END;
CREATE TRIGGER generations_status_one_way BEFORE UPDATE OF status ON generations
  WHEN NOT (OLD.status = 'active' AND NEW.status = 'discarded')
  BEGIN SELECT RAISE(ABORT, 'generation is frozen'); END;
CREATE TRIGGER jobs_binding_frozen BEFORE UPDATE OF ref, generation_id, transport, identity_id,
  idempotency_key, payload, payload_digest, skip_reason ON delivery_jobs
  BEGIN SELECT RAISE(ABORT, 'job binding is frozen'); END;
CREATE TRIGGER job_origins_frozen_u BEFORE UPDATE ON job_origins BEGIN SELECT RAISE(ABORT, 'origins are frozen'); END;
CREATE TRIGGER job_origins_frozen_d BEFORE DELETE ON job_origins BEGIN SELECT RAISE(ABORT, 'origins are frozen'); END;
CREATE TRIGGER attempts_binding_frozen BEFORE UPDATE OF ref, job_id, attempt_no, started_at ON delivery_attempts
  BEGIN SELECT RAISE(ABORT, 'attempt binding is frozen'); END;
CREATE TRIGGER attempts_provider_ref_set_once BEFORE UPDATE OF provider_message_ref ON delivery_attempts
  WHEN OLD.provider_message_ref IS NOT NULL BEGIN SELECT RAISE(ABORT, 'provider reference is frozen'); END;
CREATE TRIGGER provider_events_only_pending_resolves BEFORE UPDATE ON provider_events
  WHEN OLD.disposition <> 'pending_match' OR NEW.transport IS NOT OLD.transport
    OR NEW.provider_event_ref IS NOT OLD.provider_event_ref OR NEW.provider_message_ref IS NOT OLD.provider_message_ref
    OR NEW.reported_status IS NOT OLD.reported_status OR NEW.received_at IS NOT OLD.received_at
  BEGIN SELECT RAISE(ABORT, 'provider event is frozen'); END;
CREATE TRIGGER provider_events_no_delete BEFORE DELETE ON provider_events
  BEGIN SELECT RAISE(ABORT, 'provider event is frozen'); END;
-- Endpoints are disabled, never deleted (§2): job origins name them by ref, not by FK.
CREATE TRIGGER destinations_never_deleted BEFORE DELETE ON destinations
  BEGIN SELECT RAISE(ABORT, 'endpoints are disabled, never deleted'); END;
CREATE TRIGGER contact_points_never_deleted BEFORE DELETE ON contact_points
  BEGIN SELECT RAISE(ABORT, 'endpoints are disabled, never deleted'); END;
CREATE TRIGGER campaign_events_append_only_u BEFORE UPDATE ON campaign_events
  BEGIN SELECT RAISE(ABORT, 'event log is append-only'); END;
CREATE TRIGGER campaign_events_append_only_d BEFORE DELETE ON campaign_events
  BEGIN SELECT RAISE(ABORT, 'event log is append-only'); END;
```
(`campaigns` references `generations` before it is declared; measured to work, M6.)

- [ ] **Step 1: Failing tests** in `tests/core/test_schema.py`. Each one violates one constraint and expects `sqlcipher3.dbapi2.IntegrityError`:
  - every `CHECK` on state, lifecycle, summary, transport, enabled, status and `reported_status`;
  - the audience-member exactly-one `CHECK` (with zero members and with two), and self-membership;
  - `UNIQUE(transport, identity)`;
  - a second enabled destination for one identity, and a second enabled contact point for one identity. A disabled one, and one destination plus one contact point sharing the identity, are **allowed** (G2, F2);
  - two enabled WhatsApp points for one recipient;
  - every endpoint- and job-transport mismatch;
  - an origin whose endpoint belongs to another identity, and an origin whose path's last element is not its `endpoint_ref` (G14);
  - all three payload `CHECK`s and a malformed digest (G15);
  - a campaign pointed at another campaign's generation, or at a discarded one, and the summary/pointer `CHECK` (G16);
  - `UNIQUE(generation_id, transport, identity_id)`, `UNIQUE(job_id, attempt_no)`, and `UNIQUE(transport, provider_event_ref)`, with the same ref allowed under two transports;
  - `ON DELETE RESTRICT`;
  - every immutability trigger, including a discarded generation re-activated, a provider reference set twice, and a resolved provider event updated;
  - event update and delete.

  Plus:
  - `test_foreign_key_check_is_empty_after_migrate`;
  - `test_hot_path_queries_use_indexes` (G25): `EXPLAIN QUERY PLAN` for the origin lookup by `job_id`, destinations by `location_id`, generations by `campaign_id`, the pending-match lookup and the attempt provider-ref lookup each contain `USING INDEX` or `USING COVERING INDEX`, never a bare `SCAN`.
- [ ] **Step 2: Run.** **Expected:** fail, because there is no schema.
- [ ] **Step 3: Implement** `SCHEMA_V1` and the raw-SQL fixtures.
- [ ] **Step 4:** The tests pass and the gate is green. Commit: `feat: comms.db schema v1 — constraints, identity and origin binding triggers, immutability, append-only event log, hot-path indexes`.

---

### Task 4: Directory — locations, destinations, recipients, contact points, audiences

**Files:**
- Create `src/comms/core/campaigns/__init__.py` and `src/comms/core/campaigns/directory.py`.
- Test: `tests/core/test_directory.py`.

**Interfaces:**
```python
Normalizer = Callable[[str], str]           # a transport's pure normalize(), injected
class DirectoryError(ValueError)             # fixed, non-enumerating messages
def add_location(conn, name, *, now) -> str                                   # loc_
def add_destination(conn, location_ref, transport, platform_identity, display_name, *, normalize, now) -> str  # dst_
def add_recipient(conn, *, now) -> str                                        # rcp_
def add_contact_point(conn, recipient_ref, transport, platform_identity, *, normalize, now) -> str            # rct_
def set_enabled(conn, ref, enabled: bool) -> None                            # loc_/dst_/rcp_/rct_
def opt_out(conn, contact_point_ref, *, now) -> None
def add_location_member(conn, location_ref, recipient_ref) -> None
def remove_location_member(conn, location_ref, recipient_ref) -> None
def add_audience(conn, name, *, now) -> str                                   # cau_
def add_audience_member(conn, audience_ref, member_ref) -> None               # DirectoryError("audience cycle")
def remove_audience_member(conn, audience_ref, member_ref) -> None
```

Every write is a `write_tx`. The endpoint adders **get or create** the `delivery_identities` row for `(transport, normalize(platform_identity))` (spec S1).

Their fixed messages:
- a second enabled endpoint of the same kind for one identity → `DirectoryError("delivery identity already has an enabled endpoint")`;
- a second enabled contact point for the recipient on that transport → `DirectoryError("recipient already has an enabled contact point on this transport")`.

Violations are caught as `sqlcipher3.dbapi2.IntegrityError` and re-raised under the fixed message. No message ever contains an identity.

- [ ] **Step 1: Failing tests:**
  - each function round-trips;
  - `test_destination_and_contact_point_share_one_identity`: the fake Telegram `user:12345` and `private:12345` give one `delivery_identities` row (G2);
  - `test_second_enabled_endpoint_of_one_kind_for_an_identity_is_refused`;
  - `test_disable_old_create_new_with_the_same_identity_is_allowed` (F2), which moves a WhatsApp number to a new recipient;
  - `test_changing_an_identity_is_refused`;
  - `test_a_second_enabled_contact_point_per_transport_is_refused`;
  - `test_audience_cycle_is_refused` (direct, two-step and three-step);
  - `test_unknown_refs_fail_closed`;
  - `test_errors_never_contain_the_identity`;
  - property test `test_random_dags_accept_and_every_back_edge_is_refused`, seeded `random.Random(0..199)` with ≤ 12 audiences.
- [ ] **Step 2: Run.** **Expected:** fail.
- [ ] **Step 3: Implement.** The cycle check is an iterative DFS from the new member over the member-audience edges.
- [ ] **Step 4:** The tests pass and the gate is green. Commit: `feat: comms directory — shared immutable delivery identities, one enabled endpoint per identity and kind, acyclic audiences`.

---

### Task 5: Resolution with origin paths; sendability

**Files:**
- Create `src/comms/core/campaigns/resolve.py`.
- Test: `tests/core/test_resolve.py`.

**Interfaces:**
```python
Targets = Mapping[str, Sequence[str]]    # {"audiences": [cau_…], "locations": [loc_…], "destinations": [dst_…], "recipients": [rcp_…]}
@dataclass(frozen=True) class Origin: endpoint_ref: str; path: tuple[str, ...]   # path[-1] == endpoint_ref
@dataclass(frozen=True) class Candidate:
    transport: str; identity_id: int
    endpoint_refs: tuple[str, ...]        # sorted, distinct (G12)
    origins: tuple[Origin, ...]           # sorted by (path)
def resolve_targets(conn, targets: Targets, transports: frozenset[str]) -> list[Candidate]   # sorted by (transport, identity_id)
def path_is_valid(conn, path: tuple[str, ...]) -> bool
```

Rules (§3, §5.3), each a test:
- A disabled location contributes no members.
- A destination is sendable only if it **and its location** are enabled, however it was reached.
- A contact point is sendable only if it and its recipient are enabled and it is not opted out.
- A path is valid only if every node is enabled, every edge still exists, and a destination's owning location is enabled even when the location is not on the path (G13).
- Telegram candidates come from sendable destinations and sendable Telegram contact points. WhatsApp candidates come from sendable WhatsApp contact points. Only the requested transports are resolved.
- Candidates are grouped by `(transport, identity_id)`, with every endpoint ref and origin merged (R3).
- A cycle planted by raw SQL raises `DirectoryError("audience cycle")`. An unknown target raises `DirectoryError("unknown target")`.

- [ ] **Step 1: Failing tests:**
  - an all-locations audience resolves only configured, enabled locations;
  - a disabled location is excluded;
  - a disabled destination is excluded;
  - a destination reached directly but sitting in a disabled location is excluded;
  - a recipient in two locations, one disabled, is still included with the valid path only;
  - an opted-out contact point is excluded;
  - a duplicate WhatsApp recipient reached through four audiences gives one candidate with four origins;
  - `test_same_person_via_contact_point_and_private_chat_destination_is_one_candidate_with_both_endpoint_refs` (R3, G12);
  - the same person on both transports gives two candidates;
  - an unknown audience fails closed, and so does a planted cycle;
  - `path_is_valid` is false after removing an audience membership or a location membership, after disabling any node, and after disabling a direct destination's location (G13);
  - `test_resolution_is_deterministic`: two runs give equal output.
- [ ] **Step 2: Run.** **Expected:** fail.
- [ ] **Step 3: Implement** an iterative DFS that carries the path.
- [ ] **Step 4:** The tests pass and the gate is green. Commit: `feat: target resolution with origin paths, merged endpoint refs and sendability rules`.

---

### Task 6: The transport interface, fakes, and the no-I/O rule

**Files:**
- Create `src/comms/core/delivery/__init__.py` and `src/comms/core/delivery/transport.py`.
- Create `tests/core/fakes.py`.
- Test: `tests/core/test_transport_contract.py`.

**Interfaces (the 5d contract):**
```python
class ResultKind(StrEnum):
    """What deliver() may report (§7.2, G22). A 5d adapter conformance test enforces each meaning.
    ACCEPTED / DELIVERED: the provider took / delivered the message.
    FAILED_TRANSIENT: ONLY when (a) the adapter can prove no provider-side send occurred, or
        (b) the provider's idempotency makes resending this exact FrozenDelivery under this key safe.
    FAILED_PERMANENT: the provider definitively refused.
    OUTCOME_UNKNOWN: everything ambiguous — timeouts, loss after writing, ambiguous responses, any exception."""
@dataclass(frozen=True) class DeliveryIntent:
    transport: str; identity: str = field(repr=False); content: Mapping[str, Any] = field(repr=False)
@dataclass(frozen=True) class PreparedPayload: data: bytes = field(repr=False); digest: str
@dataclass(frozen=True) class Skip: reason: str                     # a SkipReason value
class SkipReason(StrEnum): PLATFORM_INELIGIBLE = "platform_ineligible"
@dataclass(frozen=True) class FrozenDelivery:
    job_ref: str; generation_ref: str; transport: str; identity: str = field(repr=False)
    payload: PreparedPayload = field(repr=False); idempotency_key: str = field(repr=False); attempt_no: int
@dataclass(frozen=True) class DeliveryResult: kind: ResultKind; provider_message_ref: str | None = None
@runtime_checkable
class DeliveryTransport(Protocol):
    """normalize, prepare and still_valid perform NO I/O of any kind — no network, no filesystem read
    or write, no other database, no RPC, no subprocess, no await — and are deterministic; they run
    inside comms.db transactions (§4, S3). deliver is the only external side-effect boundary and is
    called with no transaction open."""
```

`tests/core/fakes.py`:
- `FakeTelegram` and `FakeWhatsApp`, each with `script: dict[identity, list[Behaviour]]`. `Behaviour` is one of `accept`, `deliver`, `transient_unsent`, `permanent`, `raise`, `unknown`, `ineligible`, `window_closes_at(t)` and `crash_after_accept`, which raises `SimulatedCrash(BaseException)` after recording a transmission.
- Every call records `(method, identity, conn_in_transaction)`.
- `sent` lists every transmission.
- `io_calls` stays empty unless a test's deliberately impure fake variant touches an I/O hook (G5).
- `FakeTelegram.normalize` (G11) maps `user:N`/`private:N` → `N`, `group:N` → `-N` and `channel:N` → `-100N`, and refuses anything else.
- `FakeWhatsApp.normalize` canonicalizes to E.164.
- `FakeLock` has `held()`.
- `plant_failure(conn, table, op)` and `clear_planted(conn)` create and drop a `TEMP` trigger (M9).

- [ ] **Step 1: Failing tests:**
  - both fakes satisfy the protocol;
  - every dataclass is frozen;
  - `test_fake_telegram_marks_peer_kinds`: user equals private, and user, group and channel are pairwise different (G11);
  - `test_fakes_record_transaction_state`;
  - `test_deliver_inside_a_transaction_is_refused_by_io_guard`;
  - `test_reprs_hold_no_identity_or_payload` (G27);
  - `test_result_kind_contract_is_documented`: the docstring states the two `FAILED_TRANSIENT` conditions (G22);
  - `test_planted_failure_aborts_and_rolls_back` (M9).
- [ ] **Step 2: Run.** **Expected:** fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** The tests pass and the gate is green. Commit: `feat: DeliveryTransport contract (no-I/O prepare/still_valid, FrozenDelivery, ResultKind semantics) and scriptable fakes`.

---

### Task 7: Campaign drafts, lifecycle edits and the typed event log

**Files:**
- Create `src/comms/core/campaigns/drafts.py` and `src/comms/core/campaigns/events.py`.
- Test: `tests/core/test_drafts_and_events.py`.

**Interfaces:**
```python
EVENT_TYPES: frozenset[str]    # exactly the spec §9 list, campaign.summary_changed included (G24)
class ReasonCode(StrEnum):     # the closed reason set (G8)
    OPERATOR; DUPLICATE_CONFLICT; AMBIGUOUS_MATCH; NOT_CURRENT_ATTEMPT; REDUCER_REFUSED; RETRY_EXHAUSTED
EVENT_FIELDS: Mapping[str, Validator]   # key → validator; nothing else is accepted (G8, spec S6)
#   generation/job/attempt → refs.check(v, kind); transport → {"telegram","whatsapp"}
#   target_digest/recipient_digest/snapshot_digest → ^[0-9a-f]{64}$
#   job_count/pending_count/skipped_count/cancelled_count/success_count/failure_count/unknown_count/
#     in_flight_count/already_sent_count/requeued_count/exhausted_count → int >= 0 (bool refused)
#   attempt_no → int >= 1; summary → SUMMARIES; lifecycle → LIFECYCLES; reason → ReasonCode
#   send_at → timeutil.parse round-trips; verdict → {"sent","not_sent"}; status → {"ACCEPTED","DELIVERED","FAILED_PERMANENT"}
def append_event(conn, event_type: str, campaign_ref: str | None, payload: Mapping[str, Any], *, now) -> str
    # asserts conn.in_transaction (P11); ValueError on an unknown type, an unknown key or a failed validator
def create_campaign(conn, title, *, now) -> str
def set_content(conn, cmp, *, canonical=None, fa=None, en=None, links=None, media=None, now) -> None  # DRAFT only
def set_targets(conn, cmp, targets: Targets, transports: frozenset[str], *, now) -> None               # DRAFT only
def validate(conn, cmp, *, now) -> None    # DRAFT → READY: a body, ≥1 transport, ≥1 target, known refs
def edit(conn, cmp, *, now) -> None        # READY → DRAFT
class LifecycleError(ValueError)           # fixed messages
```
Media descriptors are `{sha256: 64-hex, mime, name, size: int}`. Content is stored as TG-JCS-v1 text.

- [ ] **Step 1: Failing tests:**
  - the happy path, with each event emitted in the same transaction;
  - `test_content_edits_are_refused_outside_draft`;
  - `test_validate_refuses_missing_body_transport_or_targets`;
  - `test_unknown_target_fails_closed_at_validate`;
  - `test_event_types_are_exactly_the_spec_list` (G24);
  - `test_event_payload_refuses_free_text_under_every_key` (G8): `"hello"`, `"alice"`, a phone number, a body and a raw Telegram ID are each refused under **every** string-typed key, `reason` included;
  - `test_event_payload_refuses_unknown_keys_bool_counts_and_wrong_ref_kinds`;
  - `test_event_is_rolled_back_with_its_transaction`;
  - `test_event_seq_is_global_and_increasing`.
- [ ] **Step 2: Run.** **Expected:** fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** The tests pass and the gate is green. Commit: `feat: campaign drafts and the atomic event log with typed, finite-domain payload fields`.

---

### Task 8: The reducer and the derived summary (moved before freeze, G1)

**Files:**
- Create `src/comms/core/delivery/reducer.py`.
- Test: `tests/core/test_reducer.py`, built on `tests/core/schema_fixtures.py`: raw-SQL campaigns, generations and jobs in any state.

**Interfaces:**
```python
class Evidence(StrEnum): CLAIM; RESULT; PROVIDER; RESOLVE_SENT; RESOLVE_NOT_SENT; CANCEL; SKIP_REVALIDATION; RETRY; RECOVER
@dataclass(frozen=True) class Transition:
    new_state: str | None; attempt_outcome: str | None; disposition: str   # applied / recorded / refused
    attempt_id: int | None = None                                           # set by an applied CLAIM (F4)
RANK = {"PENDING": 0, "IN_FLIGHT": 1, "FAILED_TRANSIENT": 2, "OUTCOME_UNKNOWN": 2, "ACCEPTED": 3, "DELIVERED": 4}
TERMINAL = frozenset({"FAILED_PERMANENT", "CANCELLED", "SKIPPED_PLATFORM_POLICY", "SKIPPED_REVALIDATION"})
NAMED_DESCENTS = frozenset({("FAILED_TRANSIENT", "RETRY", "PENDING"),
                            ("ACCEPTED", "PROVIDER", "FAILED_PERMANENT")})    # G3, each precondition-tested
def decide(job_state: str, *, evidence: Evidence, value: str | None, is_current_attempt: bool,
           attempt_count: int = 0, cap: int = 5) -> Transition                  # PURE
def reduce(conn, job_id: int, attempt_id: int | None, evidence: Evidence, value: str | None, *, now, cap: int = 5) -> Transition
def bind_provider_ref(conn, attempt_id: int, provider_message_ref: str, *, now) -> int   # G6: sets the ref, then
    # applies every pending_match provider event for (job transport, ref) through reduce(PROVIDER) in id order;
    # returns how many were reconciled; asserts conn.in_transaction
def summarize(states: Sequence[str], any_attempt_ever: bool) -> str   # PURE, §6.3; ValueError on empty (R8)
def complete_if_idle(conn, campaign_id, *, now) -> None
```

`reduce`:
- is the only writer of `delivery_jobs.state`, and asserts `conn.in_transaction`;
- writes with compare-and-set: `UPDATE … WHERE id=? AND state=?`, using the state `decide` saw;
- for `CLAIM`, also requires the campaign lifecycle to be `'SENDING'` and the job's generation to be the campaign's current one; increments `attempt_count`; and **inserts the attempt row**, returning its id (F4);
- treats `rowcount == 0` as `refused`;
- records the evidence on the attempt (`outcome`, `finished_at`);
- appends `delivery.provider_update_refused` (reason `NOT_CURRENT_ATTEMPT` or `REDUCER_REFUSED`) for a refused `PROVIDER`;
- recomputes and stores the summary **in the same transaction**, appending `campaign.summary_changed` if the campaign is `COMPLETE` and the summary moved;
- emits `campaign.transport_completed` when this transport's last `PENDING`/`IN_FLIGHT` job in the generation leaves those states (F5).

`decide` encodes §6.4/§7.3:
- `RESULT` applies only from `IN_FLIGHT`, and only for the current attempt.
- Success evidence from any attempt raises the state if that is higher; `DELIVERED` never regresses.
- Failure evidence applies only to the current attempt.
- `ACCEPTED → FAILED_PERMANENT` happens only via `PROVIDER` on the current attempt.
- Resolutions apply only from `OUTCOME_UNKNOWN`: `sent` → `ACCEPTED`, `not_sent` → `FAILED_TRANSIENT`.
- `RETRY` applies only from `FAILED_TRANSIENT`, and only while `attempt_count < cap`.
- `CANCEL`, `SKIP_REVALIDATION` and `CLAIM` apply only from `PENDING`.
- `RECOVER` applies only from `IN_FLIGHT` and yields `OUTCOME_UNKNOWN`.
- Everything else is `refused`.

- [ ] **Step 1: Failing tests:**
  - **Exhaustive:** `test_decide_over_every_state_evidence_value_and_attempt_flag` runs the full product against an explicit expected table written as data. It also checks the invariants (G3):
    - every applied transition either does not lower `RANK` or is in `NAMED_DESCENTS`;
    - nothing leaves `DELIVERED` or a `TERMINAL` state;
    - `test_named_descents_require_their_preconditions`: `RETRY` at the cap is refused, and `PROVIDER FAILED_PERMANENT` for a non-current attempt is refused.
  - `test_summarize_table` over every multiset of ≤ 3 states × `any_attempt_ever`;
  - `test_summarize_refuses_empty`;
  - `test_unknown_is_never_failed`;
  - `test_claim_inserts_the_attempt_atomically` (F4);
  - `test_claim_refused_unless_sending_and_current_generation`;
  - `test_late_webhook_delivered_then_sync_accepted_keeps_delivered` and `test_late_failure_from_an_earlier_attempt_does_not_touch_the_job` (R4);
  - `test_bind_provider_ref_reconciles_pending_events_in_order` (G6);
  - `test_bind_provider_ref_refuses_a_second_binding`;
  - `test_summary_is_recomputed_in_the_same_transaction` (G9): a planted failure on `UPDATE OF summary ON campaigns` leaves the job state unchanged;
  - `test_transport_completed_is_emitted_once_per_transport` (F5);
  - `test_reduce_outside_a_transaction_is_refused`.
- [ ] **Step 2: Run.** **Expected:** fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** The tests pass and the gate is green. Commit: `feat: one reducer for every job-state write (named descents, atomic claim+attempt, pending-match reconciliation); summary derived in the same transaction`.

---

### Task 9: Freeze — send, schedule, unschedule, pre-send cancel

**Files:**
- Create `src/comms/core/delivery/freeze.py`.
- Modify `src/comms/core/domains.py` and `tests/security/test_comms_wire_frozen.py`.
- Test: `tests/core/test_freeze.py`.

**Interfaces:**
```python
# domains.py gains (G7, G30): IDEM = b"comms-delivery-idem/v1\0"; SNAPSHOT = b"comms-campaign-snapshot/v1\0";
#   TARGET = b"comms-campaign-target/v1\0"; RECIPIENTS = b"comms-campaign-recipients/v1\0"
class NoEligibleEndpoints(LifecycleError)            # "NO_ELIGIBLE_ENDPOINTS"
def idempotency_key(cmp: str, gen: str, transport: str, identity: str) -> str       # §7.1
def snapshot_digest(*, cmp, gen, send_at, content, transports, jobs) -> str        # §5.4 exactly
def target_digest(targets: Targets, transports: frozenset[str]) -> str             # §9 (S5)
def recipient_digest(jobs: Sequence[tuple[str, str, tuple[str, ...], str]]) -> str # (job_ref, transport, endpoint_refs, state); §9
def send(conn, cmp, transports: Mapping[str, DeliveryTransport], *, now) -> str   # READY → SENDING; gen_
def schedule(conn, cmp, at: datetime, transports, *, now) -> str                  # READY → SCHEDULED; gen_
def unschedule(conn, cmp, *, now) -> None      # SCHEDULED → READY; generation discarded; PENDING jobs CANCELLED; pointer cleared (F1, G16)
def cancel(conn, cmp, *, now) -> CancelReport  # DRAFT/READY → CANCELLED; SCHEDULED → as unschedule + CANCELLED; SENDING → Task 11
```

The freeze body runs inside one `write_tx` (§5.1):
1. resolve;
2. `prepare(intent, send_at)` for each candidate;
3. create a `PENDING` job with its payload, or a `SKIPPED_PLATFORM_POLICY` job with its `skip_reason`;
4. if none is `PENDING`, raise `NoEligibleEndpoints`, which rolls back;
5. insert the generation, the jobs, one `job_origins` row per origin (with `endpoint_ref`) and the snapshot digest;
6. set `current_generation_id`, lifecycle and summary `IN_PROGRESS`;
7. append the event, carrying `generation`, `job_count`, `pending_count`, `skipped_count`, `target_digest`, `recipient_digest` and `snapshot_digest`, plus `send_at` for a schedule.

Every job and cancel state change goes through `reduce` (G1). Unschedule applies `CANCEL` only to `PENDING` jobs.

- [ ] **Step 1: Failing tests:**
  - `test_one_send_creates_one_immutable_generation`;
  - `test_same_identity_via_destination_and_contact_point_gets_one_job`, whose origins name both endpoint refs;
  - `test_duplicate_whatsapp_recipient_gets_one_job`;
  - `test_same_person_gets_one_job_per_selected_transport`;
  - `test_zero_eligible_endpoints_refuses_and_leaves_ready`, including the all-skipped case;
  - `test_platform_ineligible_becomes_a_skipped_job_not_a_failure`;
  - `test_no_deliver_happens_during_freeze`;
  - `test_snapshot_digest_is_stable`: in-process twice and in a subprocess (P7);
  - `test_snapshot_digest_moves_with_every_field`;
  - `test_the_freeze_commits_the_digest_of_what_it_wrote`;
  - `test_three_digests_are_distinct_and_domain_separated` (G7): for one freeze the three differ, and each recomputes from its own preimage;
  - `test_digest_builders_accept_only_refs`: `target_digest` and `recipient_digest` run every input through `refs.check`, so a phone number or raw Telegram ID in place of a ref raises `ValueError`;
  - `test_idempotency_key_binds_generation`;
  - `test_edit_and_reschedule_mints_new_keys`;
  - `test_unschedule_with_a_skipped_job_cancels_only_pending` (F1);
  - `test_unschedule_and_scheduled_cancel_clear_the_current_generation` (G16);
  - `test_scheduled_content_is_frozen`;
  - `test_cancel_before_send_from_each_pre_send_state`;
  - `test_freeze_is_one_transaction` (G9): a planted failure on `INSERT ON campaign_events`, the freeze's last write, leaves no generation, no jobs, no origins and the lifecycle `READY`;
  - wire test: `ADDED_IN_5B4` now names the four domains.
- [ ] **Step 2: Run.** **Expected:** fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** The tests pass and the gate is green. Commit: `feat: freeze — generations, deduplicated jobs with endpoint-bound origins, idempotency keys, three distinct digests`.

---

### Task 10: The engine — lease, claim, revalidation, delivery, isolation, crash seams

**Files:**
- Create `src/comms/core/delivery/engine.py`.
- Test: `tests/core/test_engine.py`.

**Interfaces:**
```python
class SupportsHeld(Protocol): def held(self) -> bool: ...
class ExecutorLease:  def __init__(self, lock: SupportsHeld) -> None; def held(self) -> bool
class EngineCrash(BaseException)          # raised only by the crash_at seam (P4, G9)
CRASH_POINTS = ("after_claim", "during_deliver", "after_deliver_before_record")
@dataclass(frozen=True) class ExecutionReport: claimed: int; skipped_revalidation: int; outcomes: Mapping[str, int]
class Engine:
    def __init__(self, conn, transports: Mapping[str, DeliveryTransport], *, clock: Callable[[], datetime],
                 crash_at: str | None = None) -> None      # crash_at ∉ CRASH_POINTS → ValueError
    def execute(self, lease: ExecutorLease, cmp: str) -> ExecutionReport
```

`execute`, `recover` and `run_due` raise `PermissionError("executor lease required")` unless `lease.held()`.

`execute` runs each transport in sorted order. For each `PENDING` job of the campaign's current generation, in `id` order:
1. In a `write_tx`, revalidate: `any(path_is_valid(o.path) for o in origins)` and `bool(transport.still_valid(payload, now)) is True`.
   - On failure: `reduce(SKIP_REVALIDATION)`.
   - Otherwise: `reduce(CLAIM)` (compare-and-set plus attempt insert, F4). If it is refused, go to the next job.
   - Any exception here propagates: the transaction rolls back and the job stays `PENDING` (G4).
2. `io_guard(conn)`. Then `try: result = transport.deliver(frozen)` and `except Exception: result = DeliveryResult(ResultKind.OUTCOME_UNKNOWN)`. **This `try` wraps exactly the `deliver` call and nothing else** (spec S7). `BaseException` propagates.
3. In a `write_tx`: `reduce(RESULT, result.kind)`; if `result.provider_message_ref`, `bind_provider_ref(...)` (G6); then `complete_if_idle`.

Isolation holds because a persisted outcome, including an unknown from a raising `deliver`, leaves the loop running. A core exception stops `execute` entirely and fails closed.

- [ ] **Step 1: Failing tests:**
  - `test_engine_crash_escapes_execute` (P4);
  - `test_simulated_crash_from_a_fake_escapes_execute` (G9: two distinct classes);
  - the happy path on both transports ends `SENT` and `COMPLETE`;
  - `test_a_raising_telegram_deliver_does_not_stop_whatsapp`, and the reverse;
  - `test_a_raised_deliver_is_outcome_unknown_never_failed` (R5);
  - `test_a_core_error_propagates_and_is_never_an_outcome` (G4): (a) a planted failure on `INSERT ON delivery_attempts`, and (b) a fake whose `still_valid` raises `RuntimeError`. Each propagates out of `execute`, leaves the job `PENDING`, and makes no `deliver` call on either transport;
  - `test_revalidation_suppresses_a_member_removed_after_freeze` (R9);
  - `test_revalidation_suppresses_a_direct_destination_whose_location_was_disabled` (G13);
  - `test_revalidation_never_adds`;
  - `test_window_closed_between_schedule_and_send_skips` (R19);
  - `test_claim_is_compare_and_set`: two connections, one thread (M7), exactly one `deliver`;
  - `test_cancelled_job_is_not_claimed`;
  - `test_execute_requires_the_lease`;
  - `test_deliver_is_never_called_inside_a_transaction`;
  - crash seams: each `CRASH_POINTS` entry raises **`EngineCrash`** at that point and leaves the §10 state (G9).
- [ ] **Step 2: Run.** **Expected:** fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** The tests pass and the gate is green. Commit: `feat: delivery engine — leased execution, compare-and-set claim, revalidation, deliver-only exception boundary, crash seams`.

---

### Task 11: Operations — cancel while sending, retry, resolution, provider updates

**Files:**
- Create `src/comms/core/delivery/operations.py`.
- Test: `tests/core/test_operations.py`.

**Interfaces:**
```python
@dataclass(frozen=True) class CancelReport: cancelled_before_send: int; already_sent: int; currently_in_flight: int
def cancel_sending(conn, cmp, *, now) -> CancelReport
@dataclass(frozen=True) class RetryReport: requeued: int; retry_exhausted: int
def retry_failed(conn, cmp, *, now, cap: int = 5) -> RetryReport
def resolve_outcome(conn, job_ref, verdict: Literal["sent", "not_sent"], *, now) -> None
ProviderDisposition = Literal["applied", "recorded", "refused", "pending_match", "duplicate", "duplicate_conflict"]
def record_provider_update(conn, transport, provider_event_ref, provider_message_ref, status, *, now) -> ProviderDisposition
```

`record_provider_update` works in one `write_tx`:
- An existing `(transport, provider_event_ref)` with identical contents → `duplicate`, with no change.
- An existing one with different contents → `duplicate_conflict`, with event reason `DUPLICATE_CONFLICT` and no change (G28).
- Otherwise it inserts the row and matches the attempt by joining the job's transport with `provider_message_ref`:
  - no match → `pending_match` (G6);
  - two or more matches → `refused`, with reason `AMBIGUOUS_MATCH`;
  - one match → `reduce(PROVIDER, status)`, whose disposition it returns.

- [ ] **Step 1: Failing tests:**
  - `test_cancel_stops_only_unsent_work`;
  - `test_cancel_after_some_delivered_ends_partial_not_cancelled`;
  - `test_retry_never_duplicates_successful_delivery`;
  - `test_retry_never_touches_outcome_unknown`;
  - `test_retry_reuses_the_key_and_adds_an_attempt`;
  - `test_retry_exhausted_at_the_cap`;
  - `test_resolve_sent_and_not_sent`;
  - `test_resolve_refused_unless_outcome_unknown`;
  - `test_resolve_not_sent_then_retry_makes_a_new_attempt` (S12);
  - `test_duplicate_webhook_is_harmless` and `test_duplicate_webhook_with_different_contents_is_a_recorded_conflict` (G28);
  - `test_same_event_ref_on_two_transports_is_two_events`;
  - **`test_webhook_before_result_is_reconciled_when_the_result_binds`** (G6): a fake `deliver` calls `record_provider_update(... "DELIVERED")` on a **second connection** before returning `ACCEPTED`. The update is `pending_match`; after the engine's result transaction the job is `DELIVERED`, the event is `applied`, and the summary is `SENT`;
  - `test_ambiguous_provider_reference_is_refused`;
  - `test_accepted_then_provider_failure_moves_summary_sent_to_partial_without_touching_lifecycle`;
  - `test_indeterminate_becomes_sent_on_provider_confirmation`;
  - each operation writes its event in its transaction.
- [ ] **Step 2: Run.** **Expected:** fail.
- [ ] **Step 3: Implement.** Every job-state change goes through `reduce`.
- [ ] **Step 4:** The tests pass and the gate is green. Commit: `feat: cancel while sending, retry of provably-unsent failures, operator resolution, provider updates with pending-match reconciliation`.

---

### Task 12: Scheduling and recovery; the crash table

**Files:**
- Create `src/comms/core/delivery/scheduling.py` and `src/comms/core/delivery/recovery.py`.
- Test: `tests/core/test_scheduling_and_recovery.py`.

**Interfaces:**
```python
def run_due(lease, conn, engine: Engine, *, now) -> list[str]   # SCHEDULED with send_at <= now → SENDING (own tx) → engine.execute
@dataclass(frozen=True) class RecoveryReport:
    marked_unknown: int; completed: tuple[str, ...]; resumable: tuple[str, ...]   # cmp_ refs (G18)
def recover(lease, conn, *, now) -> RecoveryReport   # §10 steps 1–3; one transaction per campaign; never delivers
```

- [ ] **Step 1: Failing tests:**
  - `test_scheduled_campaign_needs_no_second_approval`;
  - `test_restart_before_send_at_sends_nothing` (C6);
  - `test_offset_schedule_time_compares_as_utc`;
  - `test_recover_never_calls_a_transport`, and returns `resumable` (G18);
  - `test_restart_resumes_unfinished_campaign_safely`: `recover`, then `engine.execute` for each `resumable`;
  - `test_delivery_idempotency_survives_restart`;
  - **the crash table, one test per §10 row:**
    - before the freeze commits (planted failure);
    - after the freeze, before claim;
    - after claim, before deliver → `OUTCOME_UNKNOWN`;
    - during deliver → `OUTCOME_UNKNOWN`;
    - provider accepted before the outcome is recorded → `OUTCOME_UNKNOWN`, with **no second transmission after recover + execute**;
    - after the last outcome commits;
    - the stranded state built by raw SQL → `COMPLETE`, with the right summary;
    - recover closes the open attempt;
    - unschedule, cancel, retry and resolution under a planted failure: all or nothing;
  - `test_recover_requires_the_lease`.
- [ ] **Step 2: Run.** **Expected:** fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** The tests pass and the gate is green. Commit: `feat: run_due with the time gate; recovery that repairs state, reports resumable work and never resends; the crash table`.

---

### Task 13: Privacy canaries and the source §31 checklist

**Files:**
- Test: `tests/core/test_privacy.py` and `tests/core/test_source_checklist.py`.

- [ ] **Step 1: Tests:**
  - `test_canaries_absent_from_every_file_in_the_database_directory` (R12):
    - register `+61400000001` and Telegram `user:987654321`, and set the body `CANARY-BODY-سلام`;
    - run a full send with a crash midway and a `wal_checkpoint`, parametrized over `journal_mode` `delete` and `wal`;
    - scan every file in the directory: absent.
  - `test_canaries_absent_from_events_returns_reprs_exceptions_and_logs`: every return value and its `repr` (G27), every raised message, every `campaign_events` row, and `caplog` at DEBUG.
  - `test_transport_credentials_absent_from_campaign_data`.
  - `test_source_checklist.py`: one test per source §31 line not named elsewhere. Its module docstring maps every §31 line to a test.
- [ ] **Step 2: Run.** Any failure is a defect in the owning task's code: fix it there, RED first.
- [ ] **Step 3:** The gate is green. Commit: `test: privacy canaries across every database file and output; the source §31 checklist`.

---

### Task 14: Bounded model, mutation tests, differential walk, freeze budget

**Files:**
- Create `formal/campaign_model.py`.
- Test: `tests/formal/test_campaign_model.py`, `tests/formal/test_campaign_model_mutations.py`, `tests/core/test_differential_walk.py` and `tests/core/test_freeze_budget.py`.

- [ ] **Step 1: The model.** It is pure Python and imports nothing from `src`.
  - **State:**
    - lifecycle;
    - clock phase (before/after `send_at`);
    - generation id (0–2);
    - up to 3 jobs, each holding `(state, attempts ≤ MODEL_RETRY_CAP = 2, current_attempt, eligible: bool, resolved_not_sent: bool)` (G19, G20);
    - a `transmissions` count per logical key;
    - a set of pending provider updates (G6).
  - **Operations:**
    - freeze (send or schedule);
    - invalidate eligibility (G20);
    - claim;
    - deliver with each result;
    - a provider update before the result is recorded (G6), plus late, duplicate and earlier-attempt updates;
    - crash at each point;
    - recover;
    - cancel, retry, resolve;
    - unschedule, reschedule;
    - advance the clock;
    - `run_due`.
  - Exhaustive BFS.
- [ ] **Step 2: Properties**, each checked on every reachable state:
  - no job falls below `DELIVERED` once there;
  - **an `OUTCOME_UNKNOWN` job gains another attempt only after `RESOLVE_NOT_SENT`** (G21);
  - one job per `(generation, transport, identity)`, and no key names two payloads;
  - no execution before `send_at`;
  - no claim of an ineligible job, and no endpoint after the freeze (H1, G20);
  - no `SENDING` with only terminal jobs after `recover`;
  - no empty generation;
  - `SENT` only if every job succeeded;
  - unknown never yields `FAILED`;
  - a provider update received before the result is never lost (G6);

  plus `test_no_property_is_unreachable`.
- [ ] **Step 3: Mutation tests.** For each property, a source mutation of the model removes its guard, and the exploration must report the violation.
- [ ] **Step 4: Differential walk.** 300 seeded sequences of length ≤ 25, run on both the model and the real library, with **`retry_failed(..., cap=2)`** (G19). Job states, lifecycle and summary must agree after every step. A disagreement prints the seed and the sequence.
- [ ] **Step 5: Freeze budget** (R22, M2, P13): 5,000 WhatsApp contact points in one audience. Only the freeze is timed; it must take < 3.0 s, and the time is printed.
- [ ] **Step 6:** Record the state count, property count and both runtimes. Ceilings (P12): model ≤ 60 s and ≤ 2,000,000 states; walk ≤ 45 s. Exceeding one reduces its bound under a ledgered ruling. The gate is green. Commit: `test: bounded campaign model with mutation-tested properties; differential walk; freeze budget`.

---

### Task 15: Evidence

**Files:**
- Create `docs/verification/comms-5b4.md`.
- Modify `AGENT.md`, `CHANGELOG.md` and `CLAUDE.md`.

- [ ] **Step 1:** Write the evidence document. It:
  - maps every design finding (R1–R22, S1–S12, C1–C6, H1–H7) and every source §31 line to its test;
  - records **M1–M11** (G26), the spec-pin check (G10), the model's counts, the measured freeze time, `cipher_version` and both gates' output lines;
  - lists every ledger ruling and deferred minor.
- [ ] **Step 2:** The full gate and the WhatsVault gate are green. Commit: `docs: 5b-4 evidence`.
- [ ] **Step 3:** Ask the owner before merging or pushing.
