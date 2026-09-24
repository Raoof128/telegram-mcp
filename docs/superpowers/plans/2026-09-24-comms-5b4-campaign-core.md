# Comms 5b-4 — Campaign Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A transport-neutral campaign core in `comms.core`: an encrypted `comms.db`, the location/audience/recipient directory, campaigns with an immutable per-freeze generation, deduplicated idempotent delivery jobs with attempts, one monotonic reducer, a derived delivery summary, cancel/retry/resolution/provider updates, scheduling, and crash recovery that never resends blindly — exercised end to end against fake transports only.

**Architecture:** Dependencies run one way: shared primitives (`canonical`, `opaque`, `refs`, `timeutil`) → storage (`comms.db`, migrations) → directory → drafts and events → the transport interface → freeze → reducer → engine → operations → scheduling and recovery. Every database write is a `BEGIN IMMEDIATE` transaction through one helper that also refuses external I/O while open. The only external side effect in the whole package is `DeliveryTransport.deliver`, called with no transaction open.

**Tech Stack:** Python 3.12, `sqlcipher3` 0.6.2 (SQLCipher 4.12.0, already pinned), pytest, ruff, mypy. No new dependency.

**Spec:** [`2026-09-24-comms-5b4-campaign-core-design.md`](../specs/2026-09-24-comms-5b4-campaign-core-design.md) rev 3. Section references below (§n, Rn) are to that document.

**Branch:** `comms-5b4`, from `main` at `0973915`.

## Measured before writing

| Risk | Result |
|---|---|
| **M1 — SQLCipher fail-closed behaviour** (4.12.0 community) | A wrong key is accepted by `PRAGMA key` and fails only at the first read (`DatabaseError: file is not a database`). A keyless open of a new file creates a **plaintext** database. An empty key raises `OperationalError`. `cipher_version` returns `4.12.0 community`. `BEGIN IMMEDIATE`, `foreign_keys` and `temp_store=MEMORY` work. Default `journal_mode` is `delete`. |
| **M2 — cost** | Opening with a raw 32-byte hex key (no KDF): 28 ms. Inserting 5,000 jobs plus 5,000 origin rows with payloads in one `BEGIN IMMEDIATE`: 88 ms. The freeze budget in Task 14 is therefore **3 s for 5,000 endpoints** — about 30× headroom for resolution, `prepare` and hashing, while a pathological regression still fails it. |
| **M3 — the core guards** | `test_comms_layering.py` forbids, inside `src/comms/core`, imports of `comms.transports`, `telegram_mcp` and `whatsvault`, the strings containing them, and `import_module`/`__import__`/`importlib.util`. Transport names `"telegram"` and `"whatsapp"` are permitted strings. |
| **M4 — prefix collisions** | WhatsVault `ids.PREFIXES` includes `aud` and `job`; Telegram `REF_PREFIXES` has nine live prefixes plus tombstoned `tgu_`. Core prefixes (§1) collide with neither. |
| **M5 — the moves** | 32 files import `transports.telegram.opaque` or `.canonical`; both modules are self-contained (stdlib only). |

## Global Constraints

- **Fake transports only.** Nothing in `src/` opens a network connection. Fakes live in `tests/`.
- **No external side effect, network I/O, `await` or filesystem write inside a `comms.db` transaction** (R18). `DeliveryTransport.deliver` is called only with no transaction open, and `engine.py` asserts `not conn.in_transaction` immediately before calling it (a production check, not a test flag).
- **One copy of each shared rule.** `canonical` and `opaque` move into core; nothing duplicates them. One reducer writes job state.
- **Opaque refs outside the database.** No return value, exception message, event or log record contains a platform identity, message body or credential.
- **UTC only.** Naive datetimes are refused with `ValueError`; stored times are ISO-8601 `Z`.
- **Fail closed; commit only on a green gate** (fail-fast gate script). No test-only branch in `src`: crash injection is a constructor argument that production never supplies (the coordinator's `crash_at` pattern).
- **The Telegram and WhatsVault behaviour does not change.** The WhatsVault subtree stays byte-identical; Telegram gains only two import-path changes.
- **Full gate:** `uv sync --locked`, contracts check, `pytest`, smoke, formal, ruff, format, `mypy src/comms src/telegram_mcp`, build, plus the WhatsVault gate (539 passed).

## Review Focus

1. **A crash between `PENDING → IN_FLIGHT → deliver → durable outcome` produces a resend or a lost job.** *Test: Task 12's crash table, one test per row, plus Task 14's model property "`OUTCOME_UNKNOWN` never resent".*
2. **A transition combining schedule, cancel, retry and `OUTCOME_UNKNOWN` reaches a state the spec forbids.** *Test: Task 14's bounded model and the differential random walk against the real implementation.*
3. **The same delivery identity receives two messages on one transport** — through `dst_` and `rct_`, two audiences, or a reschedule. *Test: Task 8, `test_same_identity_via_destination_and_contact_point_gets_one_job`, `test_edit_and_reschedule_mints_new_keys`.*
4. **A platform identity or body leaks** into an event, exception, return value, log record or an unencrypted side file. *Test: Task 13's canary sweep over every file in the database directory and every observable output.*
5. **A scheduled campaign sends early** after a restart. *Test: Task 12, `test_restart_before_send_at_sends_nothing`.*

---

### Task 1: Shared primitives into core; guards

**Files:**
- Move: `src/comms/transports/telegram/canonical.py` → `src/comms/core/canonical.py`; `src/comms/transports/telegram/opaque.py` → `src/comms/core/opaque.py` (`git mv`).
- Modify: every importer (32 files, found with `grep -rln "transports.telegram.opaque\|transports.telegram.canonical" src tests scripts`), repointed by the AST-based rewriter used in 5b-1/5b-3 (`PYTHONPATH=scripts python -m migration.rewrite` style, or a one-off AST pass): import statements only.
- Create: `src/comms/core/refs.py`, `src/comms/core/timeutil.py`.
- Modify: `tests/security/test_comms_layering.py`, `tests/security/test_ai_boundary.py`.
- Create: `tests/core/__init__.py`, `tests/core/test_refs_and_time.py`, `tests/unit/test_core_moves.py`.

**Interfaces (produced):**
```python
# comms/core/refs.py
CORE_PREFIXES: dict[str, str] = {"location": "loc_", "destination": "dst_", "recipient": "rcp_",
    "contact_point": "rct_", "audience": "cau_", "campaign": "cmp_", "generation": "gen_",
    "job": "djb_", "attempt": "dat_", "event": "cev_"}
def mint(kind: str) -> str            # mint_opaque_ref(CORE_PREFIXES[kind])
def check(ref: str, kind: str) -> str # validate shape and prefix; ValueError("unexpected ref") otherwise
# comms/core/timeutil.py
def utc(dt: datetime) -> datetime     # refuse naive (ValueError), normalize to UTC
def iso(dt: datetime) -> str          # utc(dt) as "YYYY-MM-DDTHH:MM:SS.ffffffZ"
def parse(text: str) -> datetime      # inverse of iso; refuses anything else
```

- [ ] **Step 1: Failing tests.**
  - `tests/unit/test_core_moves.py::test_canonical_and_opaque_are_single_copies_in_core`: `comms.core.canonical.jcs_dumps` and `comms.core.opaque.mint_opaque_ref` import; `comms/transports/telegram/{canonical,opaque}.py` do not exist; no file under `src/` defines a function named `jcs_dumps` or `mint_opaque_ref` outside core (AST scan).
  - `test_core_canonical_is_byte_identical_to_the_pinned_base`: reuse `tests/unit/test_canonical.py`'s BASE oracle against the new path for every vector in `tests/fixtures/canonical/jcs_vectors.json`.
  - `tests/core/test_refs_and_time.py`: `test_core_prefixes_are_disjoint_from_telegram_and_whatsvault` (against `comms.transports.telegram.authority.refs.REF_PREFIXES ∪ {"tgu_"}` and `{p + "_" for p in whatsvault.ids.PREFIXES}`); `test_core_prefixes_are_unique`; `test_mint_and_check_round_trip_and_refuse_wrong_kind`; `test_naive_datetimes_are_refused`; `test_offsets_normalize_to_utc` (`2026-10-01T18:00:00+10:00` → `2026-10-01T08:00:00.000000Z`); `test_iso_parse_round_trip`.
  - `tests/security/test_comms_layering.py`: replace `test_core_has_no_production_implementation` with `test_core_is_transport_neutral` (core may define functions and classes; the import, string and dynamic-import guards keep running over every core file — they already do).
  - `tests/security/test_ai_boundary.py::test_no_ai_surface_imports_the_campaign_core` (R21): AST imports of every module matching `src/comms/transports/*/server.py`, `dispatch.py`, `sensitive_dispatch.py`, any `mcp/` package, and `transports/whatsapp/apps/mcp/*.py` contain nothing starting with `comms.core.campaigns` or `comms.core.delivery`; plus a planted-violation test proving the guard is not vacuous.
- [ ] **Step 2: Run** `uv run pytest -q -p no:randomly tests/unit/test_core_moves.py tests/core tests/security/test_comms_layering.py tests/security/test_ai_boundary.py`. Expected: the move tests and refs/time tests fail (modules absent); the import guard passes vacuously until campaigns exist (its planted test proves teeth).
- [ ] **Step 3: Implement.** `git mv` both files; AST-repoint imports; write `refs.py` and `timeutil.py`. The `test_comms_protocol_frozen` multiset is unchanged (the literals moved within `src/comms`); if a KEEP_LITERALS path entry names the old file, add the move to `RETIRED_KEEP_LITERALS`-style named mapping, not a loosening.
- [ ] **Step 4:** The targeted tests pass; full gate green. Commit: `refactor: TG-JCS-v1 and the opaque-ref minter move into comms.core (single copies); core prefix registry and time rules`.

---

### Task 2: `comms.db` — SQLCipher open and the migration runner

**Files:** Create `src/comms/core/storage/__init__.py`, `src/comms/core/storage/db.py`, `src/comms/core/storage/migrations.py`; Test `tests/core/test_comms_db.py`.

**Interfaces:**
```python
class CommsDbKeyError(Exception)          # fixed message: "comms database key is invalid"
class TransactionIOError(RuntimeError)    # raised by io_guard when a side effect is attempted inside a transaction
def open_comms_db(path: Path, key: bytes) -> sqlcipher3.Connection
@contextmanager
def write_tx(conn) -> Iterator[sqlcipher3.Connection]   # BEGIN IMMEDIATE ... COMMIT / ROLLBACK
def io_guard(conn) -> None                # raise TransactionIOError if conn.in_transaction
@dataclass(frozen=True) class Migration: version: int; statements: tuple[str, ...]
def migrate(conn, migrations: tuple[Migration, ...] = MIGRATIONS) -> int
```

`open_comms_db`, in this order (§2, M1):
```python
if not isinstance(key, bytes) or len(key) != 32: raise CommsDbKeyError("comms database key is invalid")
fresh = not path.exists()
conn = sqlcipher3.connect(str(path), isolation_level=None)   # autocommit; write_tx owns BEGIN/COMMIT
conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
if not conn.execute("PRAGMA cipher_version").fetchone()[0]: raise CommsDbKeyError(...)
try: conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
except sqlcipher3.DatabaseError: conn.close(); raise CommsDbKeyError(...) from None
conn.execute("PRAGMA foreign_keys = ON"); conn.execute("PRAGMA temp_store = MEMORY")
if fresh:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")  # forces a page write
    if path.read_bytes()[:16] == b"SQLite format 3\x00": conn.close(); path.unlink(); raise CommsDbKeyError(...)
```
The key never appears in an exception, `repr` or log.

- [ ] **Step 1: Failing tests** (`tests/core/test_comms_db.py`):
  - `test_a_short_or_missing_key_is_refused_before_any_file_exists` (keys `b""`, 31 bytes, 33 bytes, a `str`): `CommsDbKeyError`, and the path does not exist afterwards.
  - `test_a_new_file_is_encrypted` (first 16 bytes ≠ plaintext header; `cipher_version` non-empty, printed with `-s`).
  - `test_a_wrong_key_fails_closed_at_open` (not at first use).
  - `test_a_keyless_sqlite_open_of_the_file_fails` (plain `sqlite3.connect` read raises).
  - `test_the_key_never_appears_in_errors` (the hex key absent from `str(exc)` and `repr(exc)`).
  - `test_write_tx_commits_and_rolls_back`, `test_io_guard_refuses_inside_a_transaction`, `test_migrate_is_rerunnable_and_versioned`.
- [ ] **Step 2: Run; expected: all fail (module absent).**
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4:** Pass; gate green. Commit: `feat: comms.db on SQLCipher, fail-closed open (wrong key, keyless create, short key), write_tx and io_guard`.

---

### Task 3: Schema v1 and its constraints

**Files:** Modify `src/comms/core/storage/migrations.py` (`MIGRATIONS = (Migration(1, SCHEMA_V1),)`); Test `tests/core/test_schema.py`.

`SCHEMA_V1` (every statement below, verbatim intent; identity columns are `TEXT NOT NULL`, times are `TEXT NOT NULL` ISO-Z):

```sql
CREATE TABLE locations (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)), created_at TEXT NOT NULL);
CREATE TABLE recipients (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)), created_at TEXT NOT NULL);
CREATE TABLE delivery_identities (id INTEGER PRIMARY KEY, transport TEXT NOT NULL CHECK (transport IN ('telegram','whatsapp')),
  identity TEXT NOT NULL, UNIQUE (transport, identity));
CREATE TABLE destinations (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
  transport TEXT NOT NULL CHECK (transport = 'telegram'), platform_identity TEXT NOT NULL,
  identity_id INTEGER NOT NULL UNIQUE REFERENCES delivery_identities(id) ON DELETE RESTRICT,
  display_name TEXT NOT NULL, capabilities TEXT NOT NULL DEFAULT '{}',
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)), created_at TEXT NOT NULL);
CREATE TABLE contact_points (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  recipient_id INTEGER NOT NULL REFERENCES recipients(id) ON DELETE RESTRICT,
  transport TEXT NOT NULL CHECK (transport IN ('telegram','whatsapp')), platform_identity TEXT NOT NULL,
  identity_id INTEGER NOT NULL UNIQUE REFERENCES delivery_identities(id) ON DELETE RESTRICT,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)), opted_out_at TEXT, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX contact_points_one_enabled_per_transport
  ON contact_points (recipient_id, transport) WHERE enabled = 1;
CREATE TABLE location_members (location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
  recipient_id INTEGER NOT NULL REFERENCES recipients(id) ON DELETE RESTRICT, PRIMARY KEY (location_id, recipient_id));
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
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE generations (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL, send_at TEXT NOT NULL, content TEXT NOT NULL, snapshot_digest TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','discarded')));
CREATE TABLE delivery_jobs (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  generation_id INTEGER NOT NULL REFERENCES generations(id) ON DELETE RESTRICT,
  transport TEXT NOT NULL CHECK (transport IN ('telegram','whatsapp')),
  identity_id INTEGER NOT NULL REFERENCES delivery_identities(id) ON DELETE RESTRICT,
  endpoint_ref TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
  payload BLOB, payload_digest TEXT, skip_reason TEXT,
  state TEXT NOT NULL CHECK (state IN ('PENDING','IN_FLIGHT','ACCEPTED','DELIVERED','FAILED_TRANSIENT',
    'FAILED_PERMANENT','OUTCOME_UNKNOWN','CANCELLED','SKIPPED_PLATFORM_POLICY','SKIPPED_REVALIDATION')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  UNIQUE (generation_id, transport, identity_id),
  CHECK ((state = 'SKIPPED_PLATFORM_POLICY') = (payload IS NULL)));
CREATE TABLE job_origins (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES delivery_jobs(id) ON DELETE RESTRICT,
  path TEXT NOT NULL);   -- TG-JCS-v1 array of refs, target first, endpoint last
CREATE TABLE delivery_attempts (id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE,
  job_id INTEGER NOT NULL REFERENCES delivery_jobs(id) ON DELETE RESTRICT,
  attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1), started_at TEXT NOT NULL, finished_at TEXT,
  outcome TEXT CHECK (outcome IS NULL OR outcome IN ('ACCEPTED','DELIVERED','FAILED_TRANSIENT','FAILED_PERMANENT','OUTCOME_UNKNOWN')),
  provider_message_ref TEXT, UNIQUE (job_id, attempt_no));
CREATE TABLE provider_events (id INTEGER PRIMARY KEY, transport TEXT NOT NULL, provider_event_ref TEXT NOT NULL,
  job_id INTEGER REFERENCES delivery_jobs(id) ON DELETE RESTRICT,
  attempt_id INTEGER REFERENCES delivery_attempts(id) ON DELETE RESTRICT,
  reported_status TEXT NOT NULL, disposition TEXT NOT NULL CHECK (disposition IN ('applied','recorded','refused')),
  received_at TEXT NOT NULL, UNIQUE (transport, provider_event_ref));
CREATE TABLE campaign_events (event_seq INTEGER PRIMARY KEY AUTOINCREMENT, event_ref TEXT NOT NULL UNIQUE,
  campaign_ref TEXT, event_type TEXT NOT NULL, ts TEXT NOT NULL, payload TEXT NOT NULL);
-- Immutability triggers (R11, §7.1): identities, generations and job bindings never change.
CREATE TRIGGER destinations_identity_immutable BEFORE UPDATE OF transport, platform_identity, identity_id, location_id
  ON destinations BEGIN SELECT RAISE(ABORT, 'destination identity is immutable'); END;
CREATE TRIGGER contact_points_identity_immutable BEFORE UPDATE OF transport, platform_identity, identity_id, recipient_id
  ON contact_points BEGIN SELECT RAISE(ABORT, 'contact point identity is immutable'); END;
CREATE TRIGGER delivery_identities_immutable BEFORE UPDATE ON delivery_identities
  BEGIN SELECT RAISE(ABORT, 'delivery identity is immutable'); END;
CREATE TRIGGER generations_frozen BEFORE UPDATE OF campaign_id, created_at, send_at, content, snapshot_digest
  ON generations BEGIN SELECT RAISE(ABORT, 'generation is frozen'); END;
CREATE TRIGGER jobs_binding_frozen BEFORE UPDATE OF generation_id, transport, identity_id, endpoint_ref,
  idempotency_key, payload, payload_digest ON delivery_jobs BEGIN SELECT RAISE(ABORT, 'job binding is frozen'); END;
CREATE TRIGGER campaign_events_append_only_u BEFORE UPDATE ON campaign_events
  BEGIN SELECT RAISE(ABORT, 'event log is append-only'); END;
CREATE TRIGGER campaign_events_append_only_d BEFORE DELETE ON campaign_events
  BEGIN SELECT RAISE(ABORT, 'event log is append-only'); END;
```
(`campaigns` references `generations` before it is declared; SQLite resolves foreign keys at use time, which Task 3's tests confirm. If the migration runner rejects the order, move `current_generation_id` into a follow-up `ALTER TABLE ... ADD COLUMN` inside the same migration and record a ruling.)

- [ ] **Step 1: Failing tests** (`tests/core/test_schema.py`), each violating one constraint once and expecting `IntegrityError` (or `DatabaseError` for `RAISE(ABORT)`): every `CHECK` on state/lifecycle/summary/transport/enabled; the audience-member exactly-one `CHECK` (zero and two members); self-membership; `UNIQUE(transport, identity)`; two enabled WhatsApp points for one recipient (and that a disabled second point is allowed); `UNIQUE(generation_id, transport, identity_id)`; `UNIQUE(job_id, attempt_no)`; `UNIQUE(transport, provider_event_ref)` with the same ref under two transports allowed; `ON DELETE RESTRICT` on a destination referenced by a job; every immutability trigger; event update and delete; the skip/payload `CHECK`. Plus `test_foreign_key_check_is_empty_after_migrate`.
- [ ] **Step 2: Run; expected: fail (no schema).**
- [ ] **Step 3: Implement `SCHEMA_V1`.**
- [ ] **Step 4:** Pass; gate green. Commit: `feat: comms.db schema v1 — constraints, immutability triggers, append-only event log`.

---

### Task 4: Directory — locations, destinations, recipients, contact points, audiences

**Files:** Create `src/comms/core/campaigns/__init__.py`, `src/comms/core/campaigns/directory.py`; Test `tests/core/test_directory.py`.

**Interfaces:**
```python
Normalizer = Callable[[str], str]           # a transport's pure normalize(), injected (core never imports a transport)
class DirectoryError(ValueError)             # fixed, non-enumerating messages
def add_location(conn, name, *, now) -> str                                   # loc_
def add_destination(conn, location_ref, transport, platform_identity, display_name, *, normalize, now) -> str  # dst_
def add_recipient(conn, *, now) -> str                                        # rcp_
def add_contact_point(conn, recipient_ref, transport, platform_identity, *, normalize, now) -> str            # rct_
def set_enabled(conn, ref, enabled: bool) -> None                            # loc_/dst_/rcp_/rct_
def opt_out(conn, contact_point_ref, *, now) -> None
def add_location_member(conn, location_ref, recipient_ref) -> None
def add_audience(conn, name, *, now) -> str                                   # cau_
def add_audience_member(conn, audience_ref, member_ref) -> None               # refuses a cycle (DirectoryError("audience cycle"))
def remove_audience_member(conn, audience_ref, member_ref) -> None
def remove_location_member(conn, location_ref, recipient_ref) -> None
```
Every write is a `write_tx`. `add_destination`/`add_contact_point` insert the `delivery_identities` row from `normalize(platform_identity)`; a second endpoint with the same `(transport, identity)` is refused with `DirectoryError("delivery identity already registered")` — the message never contains the identity.

- [ ] **Step 1: Failing tests:** each function round-trips; `test_same_identity_cannot_be_registered_twice` (a destination and a contact point whose identities normalize equal — the fake Telegram normalizer maps user `12345` and private chat `12345` to the same identity, R3); `test_changing_an_identity_is_refused` (trigger); `test_a_second_enabled_contact_point_per_transport_is_refused` and `test_disable_then_add_is_allowed`; `test_audience_cycle_is_refused` (direct, two-step, three-step); `test_unknown_refs_fail_closed`; `test_errors_never_contain_the_identity` (assert the raw identity is absent from every raised message); **property test** `test_random_dags_accept_and_every_back_edge_is_refused` — seeded `random.Random(0..199)`: build a random DAG of ≤ 12 audiences by adding edges from lower to higher index (always accepted), then attempt one edge from a node to one of its ancestors (always refused).
- [ ] **Step 2: Run; expected: fail.**
- [ ] **Step 3: Implement.** Cycle check: before inserting `audience A ∋ audience B`, walk the member-audience graph from `B` (iterative DFS, visited set); refuse if `A` is reachable.
- [ ] **Step 4:** Pass; gate green. Commit: `feat: comms directory — immutable delivery identities, one enabled contact point per transport, acyclic audiences`.

---

### Task 5: Resolution with origin paths; sendability

**Files:** Create `src/comms/core/campaigns/resolve.py`; Test `tests/core/test_resolve.py`.

**Interfaces:**
```python
@dataclass(frozen=True) class Endpoint:
    transport: str; identity_id: int; endpoint_ref: str        # dst_ or rct_
@dataclass(frozen=True) class Candidate:
    endpoint: Endpoint; paths: tuple[tuple[str, ...], ...]      # every origin path, sorted
def resolve_targets(conn, targets: Targets, transports: frozenset[str]) -> list[Candidate]
def path_is_valid(conn, path: tuple[str, ...]) -> bool
```
`Targets` = `{"audiences": [cau_…], "locations": [loc_…], "destinations": [dst_…], "recipients": [rcp_…]}`.

Rules (§3, §5.3), each a test:
- A disabled location contributes no members; a destination is sendable only if it **and its location** are enabled, however reached; a contact point is sendable only if it, its recipient are enabled and it is not opted out; a path is valid only if every node is enabled and every edge still exists.
- `telegram` endpoints = sendable destinations + sendable Telegram contact points; `whatsapp` endpoints = sendable WhatsApp contact points; only the requested transports.
- Candidates are grouped by `(transport, identity_id)` with all paths merged (R3); output is sorted by `(transport, identity_id)`.
- Resolution re-checks acyclicity and raises `DirectoryError("audience cycle")` on a cycle planted by raw SQL; an unknown target ref raises `DirectoryError("unknown target")`.

- [ ] **Step 1: Failing tests:** all-locations audience resolves only configured, enabled locations (source §31); disabled location excluded; disabled destination excluded; destination reached directly but in a disabled location excluded; recipient in two locations, one disabled, still included with the valid path only; opted-out contact point excluded; duplicate WhatsApp recipient through four audiences → one candidate with four paths; the same person via Telegram contact point and via a private-chat destination → one Telegram candidate (R3); the same person on both transports → two candidates (source §15); unknown audience fails closed; planted cycle fails closed; `path_is_valid` false after removing an audience membership, a location membership, or disabling any node on the path.
- [ ] **Step 2: Run; expected: fail.**
- [ ] **Step 3: Implement** with an explicit DFS that carries the path; no recursion depth assumption (iterative).
- [ ] **Step 4:** Pass; gate green. Commit: `feat: target resolution with origin paths and sendability rules`.

---

### Task 6: The transport interface, fakes, and the no-I/O rule

**Files:** Create `src/comms/core/delivery/__init__.py`, `src/comms/core/delivery/transport.py`; Create `tests/core/fakes.py`; Test `tests/core/test_transport_contract.py`.

**Interfaces (the 5d contract):**
```python
class ResultKind(StrEnum): ACCEPTED; DELIVERED; FAILED_TRANSIENT; FAILED_PERMANENT; OUTCOME_UNKNOWN
@dataclass(frozen=True) class DeliveryIntent:
    transport: str; identity: str; content: Mapping[str, Any]           # frozen content (canonical JSON-able)
@dataclass(frozen=True) class PreparedPayload: data: bytes; digest: str  # digest = sha256(data).hexdigest()
@dataclass(frozen=True) class Skip: reason: str                          # fixed code, e.g. "platform_ineligible"
@dataclass(frozen=True) class FrozenDelivery:
    job_ref: str; generation_ref: str; transport: str; identity: str
    payload: PreparedPayload; idempotency_key: str; attempt_no: int
@dataclass(frozen=True) class DeliveryResult:
    kind: ResultKind; provider_message_ref: str | None = None
class DeliveryTransport(Protocol):
    name: str
    def normalize(self, platform_identity: str) -> str: ...
    def prepare(self, intent: DeliveryIntent, send_at: datetime) -> PreparedPayload | Skip: ...
    def still_valid(self, payload: PreparedPayload, now: datetime) -> bool | str: ...
    def deliver(self, delivery: FrozenDelivery) -> DeliveryResult: ...
```
`tests/core/fakes.py`: `FakeTelegram` and `FakeWhatsApp`, each with `script: dict[identity, list[Behaviour]]` where `Behaviour` ∈ `accept`, `deliver`, `transient_unsent`, `permanent`, `raise`, `unknown`, `ineligible` (prepare → `Skip`), `window_closes_at(t)` (still_valid false after t), `crash_after_accept` (raises `SimulatedCrash` after recording a provider send). Every call records `(method, identity, conn_in_transaction)`; `sent` lists every identity the fake "transmitted". `FakeTelegram.normalize` strips a `user:`/`chat:` prefix so user `12345` and private chat `12345` normalize equal; `FakeWhatsApp.normalize` canonicalizes to E.164 (`+61…`).

- [ ] **Step 1: Failing tests:** the protocol is satisfied by both fakes (`isinstance` via `runtime_checkable` and a structural test); `FrozenDelivery` and the others are frozen; `test_fakes_record_transaction_state` (a call inside `write_tx` records `True`); `test_deliver_inside_a_transaction_is_refused_by_the_engine_guard` (`io_guard` raises `TransactionIOError`).
- [ ] **Step 2: Run; expected: fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Pass; gate green. Commit: `feat: DeliveryTransport contract (prepare/still_valid/deliver, FrozenDelivery) and scriptable fakes`.

---

### Task 7: Campaign drafts, lifecycle edits and the event log

**Files:** Create `src/comms/core/campaigns/drafts.py`, `src/comms/core/campaigns/events.py`; Test `tests/core/test_drafts_and_events.py`.

**Interfaces:**
```python
EVENT_TYPES: frozenset[str]   # exactly the §9 list
def append_event(conn, event_type: str, campaign_ref: str | None, payload: Mapping[str, Any], *, now) -> str
    # must be called inside write_tx; refuses a payload key outside SAFE_KEYS and any str value
    # that is not a ref, digest, transport name, count, ISO time or fixed code (ValueError)
SAFE_KEYS = frozenset({"generation", "job", "transport", "audience_digest", "recipient_digest",
    "snapshot_digest", "recipient_count", "destination_count", "success_count", "failure_count",
    "skipped_count", "cancelled_count", "unknown_count", "summary", "lifecycle", "reason", "send_at", "attempt_no"})
def create_campaign(conn, title, *, now) -> str                         # cmp_, lifecycle DRAFT, event campaign.created
def set_content(conn, cmp, *, canonical=None, fa=None, en=None, links=None, media=None, now) -> None  # DRAFT only
def set_targets(conn, cmp, targets: Targets, transports: frozenset[str], *, now) -> None               # DRAFT only
def validate(conn, cmp, *, now) -> None      # DRAFT → READY; requires a body, ≥1 transport, ≥1 target, known refs
def edit(conn, cmp, *, now) -> None          # READY → DRAFT
class LifecycleError(ValueError)             # fixed messages
```
Media descriptors are `{sha256: 64-hex, mime, name, size: int}`; anything else is refused. Content is stored as TG-JCS-v1 text.

- [ ] **Step 1: Failing tests:** create/set/validate/edit happy path, each emitting its event in the same transaction; `test_content_edits_are_refused_outside_draft` for `READY`, `SCHEDULED`, `SENDING`, `COMPLETE`, `CANCELLED`; `test_validate_refuses_missing_body_transport_or_targets`; `test_unknown_target_fails_closed_at_validate`; `test_event_payload_refuses_unsafe_keys_and_values` (a phone number, a body string, an unknown key); `test_event_is_rolled_back_with_its_transaction` (raise inside `write_tx` after `append_event`: no event row); `test_event_seq_is_global_and_increasing`.
- [ ] **Step 2: Run; expected: fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Pass; gate green. Commit: `feat: campaign drafts (DRAFT ⇄ READY, content frozen outside DRAFT) and the atomic, privacy-checked event log`.

---

### Task 8: Freeze — send, schedule, unschedule, pre-send cancel

**Files:** Create `src/comms/core/delivery/freeze.py`; Test `tests/core/test_freeze.py`.

**Interfaces:**
```python
class NoEligibleEndpoints(LifecycleError)           # "NO_ELIGIBLE_ENDPOINTS"
def idempotency_key(cmp: str, gen: str, transport: str, identity: str) -> str
    # sha256(b"comms-delivery-idem/v1\0" + jcs_dumps({"campaign":…, "delivery_identity":…, "generation":…, "transport":…})).hexdigest()
def snapshot_digest(*, cmp, gen, send_at, content, transports, jobs) -> str      # §5.4 exactly
def send(conn, cmp, transports: Mapping[str, DeliveryTransport], *, now) -> str   # READY → SENDING; returns gen_
def schedule(conn, cmp, at: datetime, transports, *, now) -> str                  # READY → SCHEDULED; returns gen_
def unschedule(conn, cmp, *, now) -> None      # SCHEDULED → READY; generation discarded; jobs CANCELLED (kept)
def cancel(conn, cmp, *, now) -> CancelReport  # DRAFT/READY → CANCELLED; SCHEDULED → discard + CANCELLED; SENDING → Task 11
```
Freeze body, inside one `write_tx` (§5.1): resolve → candidates → `prepare(intent, send_at)` for each → `PENDING` job with payload, or `SKIPPED_PLATFORM_POLICY` job with `skip_reason` → if none `PENDING`, raise `NoEligibleEndpoints` (rolls back) → insert generation, jobs, `job_origins`, snapshot digest → lifecycle → `SENDING`/`SCHEDULED` → summary `IN_PROGRESS` → event `campaign.send_started` / `campaign.scheduled` with counts and digests (`audience_digest` and `recipient_digest` are `sha256` over TG-JCS-v1 of the **sorted endpoint refs**, never identities). `prepare` is called inside the transaction and is pure; the fakes assert no `deliver` happens here.

- [ ] **Step 1: Failing tests:** `test_one_send_creates_one_immutable_generation` (source §31); `test_same_identity_via_destination_and_contact_point_gets_one_job` (R3); `test_duplicate_whatsapp_recipient_gets_one_job` (source §31); `test_same_person_gets_one_job_per_selected_transport` (source §31); `test_zero_eligible_endpoints_refuses_and_leaves_ready` (R8, including all-skipped); `test_platform_ineligible_becomes_a_skipped_job_not_a_failure` (source §13); `test_no_deliver_happens_during_freeze` (R18); `test_snapshot_digest_is_stable_under_a_fixed_clock` (twice in-process and once in a subprocess, byte-identical) and `test_snapshot_digest_moves_with_every_field` (R14); `test_idempotency_key_binds_generation`; `test_edit_and_reschedule_mints_new_keys` (R6: schedule → unschedule → edit → validate → schedule; disjoint keys; the first generation `discarded` and its jobs `CANCELLED`, still present); `test_scheduled_content_is_frozen` (source §31: `set_content` refused while `SCHEDULED`); `test_cancel_before_send_from_each_pre_send_state`; `test_every_freeze_path_is_one_transaction` (crash seam raising after job inserts: no generation, no jobs, lifecycle unchanged).
- [ ] **Step 2: Run; expected: fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Pass; gate green. Commit: `feat: freeze — generations, deduplicated jobs with origin paths, idempotency keys and the snapshot digest`.

---

### Task 9: The reducer and the derived summary

**Files:** Create `src/comms/core/delivery/reducer.py`; Test `tests/core/test_reducer.py`.

**Interfaces:**
```python
class Evidence(StrEnum): CLAIM; RESULT; PROVIDER; RESOLVE_SENT; RESOLVE_NOT_SENT; CANCEL; SKIP_REVALIDATION; RETRY; RECOVER
@dataclass(frozen=True) class Transition: new_state: str | None; attempt_outcome: str | None; disposition: str  # applied/recorded/refused
def decide(job_state: str, *, evidence: Evidence, value: str | None, is_current_attempt: bool) -> Transition   # PURE
def reduce(conn, job_id: int, attempt_id: int | None, evidence: Evidence, value: str | None, *, now) -> Transition
    # the only writer of delivery_jobs.state; must be called inside write_tx; also recomputes the summary
def summarize(states: Sequence[str], any_attempt_ever: bool) -> str   # PURE, §6.3; raises on an empty sequence
def complete_if_idle(conn, campaign_id, *, now) -> None   # SENDING → COMPLETE + campaign.completed / summary_changed events
```
`decide` encodes §6.4/§7.3 exactly: rank `PENDING < IN_FLIGHT < {FAILED_TRANSIENT, OUTCOME_UNKNOWN} < ACCEPTED < DELIVERED`; terminal `FAILED_PERMANENT`, `CANCELLED`, skips; success evidence from any attempt raises; failure evidence applies only to the current attempt; `DELIVERED` never regresses; `ACCEPTED → FAILED_PERMANENT` only as `PROVIDER` evidence on the current attempt; resolutions only from `OUTCOME_UNKNOWN`; `RETRY` only from `FAILED_TRANSIENT`; `CANCEL` and `SKIP_REVALIDATION` only from `PENDING`; `CLAIM` only from `PENDING`; `RECOVER` only from `IN_FLIGHT` (→ `OUTCOME_UNKNOWN`); everything else `refused`.

- [ ] **Step 1: Failing tests:** **exhaustive** `test_decide_over_every_state_evidence_value_and_attempt_flag` — the full product (10 states × 9 evidence × each value × 2) checked against an explicit expected table written in the test as data (not recomputed by the same logic), plus the invariants: no applied transition lowers rank; nothing leaves `DELIVERED`; nothing leaves a terminal state except the named edges. `test_summarize_table` over every multiset of ≤ 3 job states plus the `any_attempt_ever` flag against §6.3; `test_summarize_refuses_empty` (R8); `test_unknown_is_never_failed`; `test_late_webhook_delivered_then_sync_accepted_keeps_delivered` (R4); `test_late_failure_from_an_earlier_attempt_does_not_touch_the_job` (R4); `test_summary_is_recomputed_in_the_same_transaction` (crash seam after the job update: neither persisted).
- [ ] **Step 2: Run; expected: fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Pass; gate green. Commit: `feat: one reducer for every job-state write; the delivery summary derived in the same transaction`.

---

### Task 10: The engine — lease, claim, revalidation, delivery, isolation, crash seams

**Files:** Create `src/comms/core/delivery/engine.py`; Test `tests/core/test_engine.py`.

**Interfaces:**
```python
class ExecutorLease: ...          # an opaque token; 5e mints it from the runtime lock. Tests mint via ExecutorLease.for_tests()? NO —
                                  # the constructor is public and takes the lock object; tests pass a real in-memory lock object.
CRASH_POINTS = ("after_claim", "during_deliver", "after_deliver_before_record")
class Engine:
    def __init__(self, conn, transports: Mapping[str, DeliveryTransport], *, clock: Callable[[], datetime],
                 crash_at: str | None = None) -> None
    def execute(self, lease: ExecutorLease, cmp: str) -> ExecutionReport
```
`ExecutorLease(lock)` requires a lock object exposing `held() -> bool`; `execute`, `recover` and `run_due` raise `PermissionError("executor lease required")` unless `lease.held()`. Tests use a small real lock class in `tests/core/fakes.py` (no test-only branch in `src`).

`execute`: for each transport in sorted order, independently (a failure or exception in one transport's loop is caught at the loop boundary, recorded as the job outcome per §7.2, and never stops the other transports): for each `PENDING` job of the campaign's current generation in `id` order:
1. `write_tx`: claim by compare-and-set (`UPDATE delivery_jobs SET state='IN_FLIGHT', attempt_count=attempt_count+1 WHERE id=? AND state='PENDING' AND (SELECT lifecycle FROM campaigns …)='SENDING'`, `rowcount == 1` else skip); revalidate (`any(path_is_valid(p) for p in origins)` and `transport.still_valid(payload, now)`); on failure `reduce(..., SKIP_REVALIDATION)` instead of claiming; else insert the attempt row.
2. `io_guard(conn)`; `result = transport.deliver(frozen)`; any exception → `ResultKind.OUTCOME_UNKNOWN` (R5).
3. `write_tx`: `reduce(..., RESULT, result.kind)`; attempt `finished_at`, `provider_message_ref`; `complete_if_idle`.

- [ ] **Step 1: Failing tests:** happy path both transports → `SENT`, `COMPLETE`; `test_telegram_failure_does_not_stop_whatsapp` and the reverse (source §31, §29) including a raising adapter; `test_an_exception_is_outcome_unknown_never_failed` (R5); `test_revalidation_suppresses_a_member_removed_after_freeze` (R9); `test_revalidation_never_adds` (a recipient added after the freeze gets no job); `test_window_closed_between_schedule_and_send_skips` (R19); `test_claim_is_compare_and_set` (two engines on two connections to the same file race one job: exactly one `deliver`); `test_cancelled_job_is_not_claimed` (R20); `test_execute_requires_the_lease` (R20); `test_deliver_is_never_called_inside_a_transaction` (every recorded call has `in_transaction == False`); crash seams: each `CRASH_POINTS` entry raises `SimulatedCrash` at that point and leaves the database in the §10 state (asserted here; recovery is Task 12).
- [ ] **Step 2: Run; expected: fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Pass; gate green. Commit: `feat: delivery engine — leased execution, compare-and-set claim, revalidation, transport isolation, crash seams`.

---

### Task 11: Operations — cancel while sending, retry, resolution, provider updates

**Files:** Create `src/comms/core/delivery/operations.py`; Test `tests/core/test_operations.py`.

**Interfaces:**
```python
@dataclass(frozen=True) class CancelReport: cancelled_before_send: int; already_sent: int; currently_in_flight: int
def cancel_sending(conn, cmp, *, now) -> CancelReport            # freeze.cancel delegates here for SENDING
@dataclass(frozen=True) class RetryReport: requeued: int; retry_exhausted: int
def retry_failed(conn, cmp, *, now, cap: int = 5) -> RetryReport # FAILED_TRANSIENT below cap → PENDING; COMPLETE → SENDING
def resolve_outcome(conn, job_ref, verdict: Literal["sent", "not_sent"], *, now) -> None
def record_provider_update(conn, transport, provider_event_ref, provider_message_ref, status, *, now) -> str
    # returns disposition applied/recorded/refused; duplicate (transport, event_ref) → "duplicate", no change
```

- [ ] **Step 1: Failing tests:** `test_cancel_stops_only_unsent_work` (source §31) with the three counts; `test_cancel_after_some_delivered_ends_partial_not_cancelled`; `test_retry_never_duplicates_successful_delivery` (source §31: only `FAILED_TRANSIENT` requeued; `deliver` call counts per identity); `test_retry_never_touches_outcome_unknown`; `test_retry_reuses_the_key_and_adds_an_attempt`; `test_retry_exhausted_at_the_cap` (R22); `test_resolve_sent_and_not_sent`; `test_resolve_refused_unless_outcome_unknown`; `test_duplicate_webhook_is_harmless` (R13); `test_same_event_ref_on_two_transports_is_two_events` (R13); `test_webhook_matching_no_attempt_is_refused_and_recorded`; `test_accepted_then_provider_failure_moves_summary_sent_to_partial_without_touching_lifecycle` (R4); `test_indeterminate_becomes_sent_on_provider_confirmation`; each operation writes its event in its transaction.
- [ ] **Step 2: Run; expected: fail.**
- [ ] **Step 3: Implement** (every job-state change via `reduce`).
- [ ] **Step 4:** Pass; gate green. Commit: `feat: cancel while sending, retry of provably-unsent failures, operator resolution, idempotent provider updates`.

---

### Task 12: Scheduling and recovery; the crash table

**Files:** Create `src/comms/core/delivery/scheduling.py`, `src/comms/core/delivery/recovery.py`; Test `tests/core/test_scheduling_and_recovery.py`.

**Interfaces:**
```python
def run_due(lease, conn, engine: Engine, *, now) -> list[str]    # SCHEDULED with send_at <= now → SENDING (own tx) → execute
def recover(lease, conn, *, now) -> RecoveryReport              # §10 steps 1–3, one transaction per campaign
```

- [ ] **Step 1: Failing tests:** `test_scheduled_campaign_needs_no_second_approval` (source §31: `run_due` sends with no further call); `test_restart_before_send_at_sends_nothing` (C6: schedule for Friday, `recover` + `run_due` on Thursday → zero `deliver` calls, lifecycle still `SCHEDULED`); `test_offset_schedule_time_compares_as_utc` (R16); `test_restart_resumes_unfinished_campaign_safely` (source §31); `test_delivery_idempotency_survives_restart` (source §31: same keys, no second `deliver` for `ACCEPTED`); **the crash table, one test per §10 row**: before freeze commit; after freeze before claim; after claim before deliver → `OUTCOME_UNKNOWN`; during deliver → `OUTCOME_UNKNOWN`; provider accepted before record (the fake's `sent` shows one transmission) → `OUTCOME_UNKNOWN` and **no second transmission after recover + execute**; after the last outcome commit → already `COMPLETE`; the stranded state built by raw SQL (all jobs terminal, lifecycle `SENDING`) → `recover` makes it `COMPLETE` with the right summary (R7); recover closes the open attempt (`finished_at`, outcome) (R7); `test_recover_requires_the_lease`.
- [ ] **Step 2: Run; expected: fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Pass; gate green. Commit: `feat: run_due with the time gate; recovery that never resends and never strands; the crash table`.

---

### Task 13: Privacy canaries and the source §31 checklist

**Files:** Test `tests/core/test_privacy.py`, `tests/core/test_source_checklist.py`.

- [ ] **Step 1: Tests:**
  - `test_canaries_absent_from_every_file_in_the_database_directory` (R12): register `+61400000001` and Telegram `987654321`, set a body `CANARY-BODY-سلام`, run a full send with a crash mid-way and a `wal_checkpoint`, then with `journal_mode` both `delete` and `wal` (parametrized), scan every file in the directory (main, `-wal`, `-shm`, `-journal`, anything else) for each canary's UTF-8 bytes: absent.
  - `test_canaries_absent_from_events_returns_exceptions_and_logs`: capture every return value, every raised exception message (drive each error path once), every `campaign_events` row, and `caplog` at DEBUG: no canary.
  - `test_transport_credentials_absent_from_campaign_data` (source §31): a fake credential string configured on a fake transport never reaches the database file bytes (decrypted dump via the key) or any event.
  - `test_source_checklist.py`: one test per source §31 line not already named above, each named after the line; a module docstring maps every §31 line to its test (the evidence table in Task 15 is generated from it).
- [ ] **Step 2: Run.** These are regression guards over Tasks 4–12; any failure is a defect in the owning task's code (fix there, RED first).
- [ ] **Step 3:** Gate green. Commit: `test: privacy canaries across every database file and output; the source §31 checklist`.

---

### Task 14: Bounded model, mutation tests, differential walk, freeze budget

**Files:** Create `formal/campaign_model.py`; Test `tests/formal/test_campaign_model.py`, `tests/formal/test_campaign_model_mutations.py`, `tests/core/test_differential_walk.py`, `tests/core/test_freeze_budget.py`.

- [ ] **Step 1: The model.** Pure Python, no imports from `src` except `comms.core.delivery.reducer.decide` and `summarize` **is not allowed** — the model encodes the rules independently (a second, smaller statement of them). State: lifecycle; clock phase (`before`/`after` `send_at`); generation id (0–2); up to 3 jobs, each `(state, attempts ≤ 2, current_attempt, keys)`; a `sent` counter per `(generation, job)`; a `transmissions` count per logical key. Operations: freeze (send/schedule), claim, deliver with each result, crash at each point, recover, provider update (late, duplicate, earlier-attempt), cancel, retry, resolve, unschedule, reschedule, advance clock, run_due. Exhaustive BFS.
- [ ] **Step 2: Properties** (each an assertion evaluated on every reachable state; §11): no job below `DELIVERED` once there; `OUTCOME_UNKNOWN` never auto-retried or resent; one job per `(generation, transport, identity)` and no key names two payloads; no execution before `send_at`; no endpoint after the freeze; no `SENDING` with only terminal jobs after `recover`; no empty generation; `SENT` only if every job succeeded; unknown never `FAILED`. Plus `test_no_property_is_unreachable` as in 5b-3.
- [ ] **Step 3: Mutation tests.** For each property, a source mutation of the model that removes the guard it protects; the exploration must report that property (5b-3's `test_invariant_mutations.py` pattern).
- [ ] **Step 4: Differential walk.** `tests/core/test_differential_walk.py`: 300 seeded random operation sequences (length ≤ 25) applied to both the model and the real library (in a temp `comms.db` with fakes); after every step the job states, lifecycle and summary must agree. A disagreement prints the seed and the sequence.
- [ ] **Step 5: Freeze budget** (R22, M2): `test_freeze_of_5000_endpoints_is_within_budget` builds 5,000 WhatsApp contact points in one audience, freezes, asserts < 3.0 s, and prints the measured time into the evidence (`-s`).
- [ ] **Step 6:** Record the explored-state count and property count printed by the model; gate green. Commit: `test: bounded campaign model with mutation-tested properties; differential walk against the library; freeze budget`.

---

### Task 15: Evidence

**Files:** Create `docs/verification/comms-5b4.md`; Modify `AGENT.md`, `CHANGELOG.md`, `CLAUDE.md` (status paragraph, verify counts, Map row for `comms.core`).

- [ ] **Step 1:** The evidence maps every design finding (R1–R22, C1–C6, H1–H7) and every source §31 line to its test; records M1–M5, the model's state and property counts, the measured freeze time, `cipher_version`, and both gates' output lines; lists every ledger ruling and deferred minor.
- [ ] **Step 2:** Full gate plus WhatsVault gate green. Commit: `docs: 5b-4 evidence`.
- [ ] **Step 3:** Ask the owner before merging or pushing.
