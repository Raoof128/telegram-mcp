# WhatsVault Phase 1a — Vault Core Implementation Plan (rev 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the encrypted local vault foundation — two SQLCipher databases with numbered transactional migrations, at-rest encryption with provision/require key separation, self-enforcing evidence immutability, dedupe ledger, status-reduction lattice, and integrity checks — that the importer and search subsystems build on.

**Architecture:** Two SQLCipher databases (`vault.db` = evidence, `control.db` = send-authoritative state; the split is operational, not an authorisation boundary). Symmetric DB/attachment keys live in the macOS Keychain and are **provisioned once, then required** (never silently regenerated). Evidence immutability is enforced by deny-by-default SQL triggers. Identifiers are prefixed ULIDs; time is uncertainty intervals; the 24h-window projection lives in `control.db` and is driven by an explicit `window_eligible` evidence flag — never by `origin`.

**Tech Stack:** Python ≥3.11, `sqlcipher3` (source build against SQLCipher via `brew install sqlcipher`), `cryptography` (AES-256-GCM), `keyring` (macOS Keychain), `python-ulid`, `pytest`. Isolated in a project virtualenv, dependencies locked to exact versions.

**Spec:** `docs/internal/specs/2026-08-27-whatsvault-design.md` (implements §2.4, §3; prepares hooks for §4, §7, §8). The plan argues from the spec; executors read both.

## Global Constraints

Copied verbatim / distilled from the spec; every task implicitly includes these.

- **INV-ATREST** — *Every store of message content at rest — `vault.db`, `control.db`, and the attachment blob store — is encrypted under keys held only in the Mac Keychain / Secure Enclave. No plaintext message content, media, credential, or signing key ever touches disk, environment variables, logs, git, or MCP responses.*
- **INV-SENDPOLICY** — *The final send decision never depends on mutable policy data outside the transaction protecting nonce consumption and send-attempt creation.* → **all send-authoritative mutable state (including the 24h-window projection) lives in `control.db`.**
- **INV-IMPORT / I5** — *the 24h window is driven by an explicit `window_eligible` flag set only by the live inbound-message normaliser; imports and history-sync are never window-eligible; `origin` is not authority.*
- **The database split is operational, not an authorisation boundary.** Authority is the signature, never a row or table location.
- **Evidence immutability (§3.6)** — enforced by triggers, deny-by-default. Only explicitly named projection columns may change; every other evidence field, and the `ingest_events` / `message_revisions` rows, are immutable. `audit_log` (in `control.db`) forbids UPDATE and DELETE.
- **Identifiers (§3.2)** — `<prefix><26-char uppercase Crockford Base32 ULID>`, validated at every application boundary. ULID ordering is a stable tie-breaker only, never chronology.
- **Time (§3.3)** — `ts_ingested_ms` never orders. Provider timestamps are **seconds** → `*1000`, precision `'s'`.
- **Dedupe identity (§3.8/§3.9)** — `text_normalised` (search) NEVER participates. Semantic keys are family-specific and domain-tagged.
- **Signed byte contract (§6.3)** — nonces and hashes are raw 32-byte values; stored as `BLOB CHECK(length=32)`, not hex TEXT. Drafts bind `account_id`, `phone_number_id`, `recipient_wa_id`, `template_params_sha256`, `attachments_digest` (P7).
- **Python isolation** — project venv, dependencies locked to exact versions in `requirements-lock.txt`; never global `pip install`.
- **No fake security** — never issue a PRAGMA or config that silently no-ops (`cipher_secure_delete` is NOT a real SQLCipher pragma and is banned). Verify each control actually took effect.
- **Changelog protocol** — `CHANGELOG.md` is opened in Task 1 and closed in Task 12; any task making a security-relevant change appends a `Raouf:` line. (This supersedes any per-task changelog requirement.)

---

### Task 1: Scaffold, deterministic SQLCipher install, capability gate

**Files:**
- Create: `pyproject.toml`, `requirements.in`, `requirements-lock.txt`, `.gitignore`, `CHANGELOG.md`
- Create: `src/whatsvault/__init__.py`, `tests/__init__.py`
- Create: `src/whatsvault/capabilities.py`
- Test: `tests/test_capabilities.py`

**Interfaces:**
- Produces: `whatsvault.capabilities.assert_sqlite_capabilities(conn) -> dict` returning `{"sqlcipher", "cipher_version", "cipher_provider", "fts5", "trigram", "fts_secure_delete", "core_secure_delete", "foreign_keys", "sqlite_version"}`; raises `CapabilityError` if any required capability is missing. **Probe order matters:** connection-state pragmas (`foreign_keys`, `secure_delete`) are probed BEFORE the FTS DDL probes, because `PRAGMA foreign_keys` cannot change once a transaction is open (the `CREATE VIRTUAL TABLE` probes open one).

- [ ] **Step 1: Create scaffold files**

`pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "whatsvault"
version = "0.0.1"
requires-python = ">=3.11"
# Runtime deps are declared loosely here; the AUTHORITATIVE, reproducible set is
# requirements-lock.txt (exact versions, committed). See Step 2.
dependencies = [
    "sqlcipher3",
    "cryptography",
    "keyring",
    "python-ulid",
]

[project.optional-dependencies]
dev = ["pytest"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
"whatsvault.db" = ["migrations/vault/*.sql", "migrations/control/*.sql"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]   # required for the src/ layout; without it `import whatsvault` fails
```

`requirements.in` (human-edited floors):
```
sqlcipher3>=0.6.2
cryptography>=43
keyring>=25
python-ulid>=2.7
pytest>=8.3
```

`.gitignore`:
```
.venv/
.venv-*/
__pycache__/
*.pyc
*.db
*.db-wal
*.db-shm
imports/
.env
```

`src/whatsvault/__init__.py`: `__all__ = []`
`tests/__init__.py`: (empty)

`CHANGELOG.md`:
```markdown
# Changelog

## Unreleased

- Raouf: open Phase 1a vault core (scaffold, deterministic SQLCipher install, capability gate).
```

- [ ] **Step 2: Prerequisite + deterministic install (one-time)**

The `sqlcipher3` binding is a **source build** against the SQLCipher C library. `sqlcipher3-binary` has no wheel for this target (macOS arm64) — do not use it. Force the source build against Homebrew's SQLCipher so the crypto provider is known (verified: cipher_version `4.12.0 community`, provider `openssl`; a fresh Homebrew installs a newer 4.x — the gate reads the version at runtime, it is not hardcoded):
```bash
brew install sqlcipher
cd /path/to/WhatsVault
python3 -m venv .venv
SQLCIPHER_PREFIX="$(brew --prefix sqlcipher)"
C_INCLUDE_PATH="$SQLCIPHER_PREFIX/include" LIBRARY_PATH="$SQLCIPHER_PREFIX/lib" \
  .venv/bin/pip install -r requirements.in   # source-builds sqlcipher3 (no wheel for this target); do NOT add --no-binary, which breaks the build backend
.venv/bin/pip install -e .
# Freeze the EXACT working set into the committed lock (this is the real pin):
.venv/bin/pip freeze --exclude-editable > requirements-lock.txt
```
Expected: install succeeds; `requirements-lock.txt` now contains exact `==` versions. Commit that file. If the build cannot find SQLCipher, the `C_INCLUDE_PATH`/`LIBRARY_PATH` flags above are the fix.

- [ ] **Step 3: Write the failing capability test**

`tests/test_capabilities.py`:
```python
import sqlcipher3
import pytest
from whatsvault.capabilities import assert_sqlite_capabilities, CapabilityError


def _keyed_conn():
    conn = sqlcipher3.connect(":memory:")
    conn.execute("PRAGMA key = \"x'%s'\"" % ("00" * 32))
    return conn


def test_build_has_sqlcipher_and_all_required_features():
    caps = assert_sqlite_capabilities(_keyed_conn())
    assert caps["sqlcipher"] is True
    assert caps["cipher_version"]           # non-empty string
    assert caps["fts5"] and caps["trigram"]
    assert caps["fts_secure_delete"] is True
    assert caps["core_secure_delete"] is True
    assert caps["foreign_keys"] is True


def test_missing_capability_raises(monkeypatch):
    def broken(_conn):  # MUST match _probe_fts5(conn) arity, or it raises TypeError for the wrong reason
        raise sqlcipher3.OperationalError("no such module: fts5")
    monkeypatch.setattr("whatsvault.capabilities._probe_fts5", broken)
    with pytest.raises(CapabilityError):
        assert_sqlite_capabilities(_keyed_conn())
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_capabilities.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'whatsvault.capabilities'`.

- [ ] **Step 5: Write the minimal implementation**

`src/whatsvault/capabilities.py`:
```python
"""Empirically probe that the installed SQLCipher build has the features the
vault and search subsystems require. We verify by running SQL, never by
trusting a version string. Every probe reflects a control that must actually
take effect (spec 'No fake security')."""


class CapabilityError(RuntimeError):
    pass


def _probe_fts5(conn) -> bool:
    conn.execute("CREATE VIRTUAL TABLE _cap_fts USING fts5(x)")
    conn.execute("DROP TABLE _cap_fts")
    return True


def _probe_trigram(conn) -> bool:
    conn.execute("CREATE VIRTUAL TABLE _cap_tri USING fts5(x, tokenize='trigram')")
    conn.execute("DROP TABLE _cap_tri")
    return True


def _probe_fts_secure_delete(conn) -> bool:
    conn.execute("CREATE VIRTUAL TABLE _cap_sd USING fts5(x)")
    conn.execute("INSERT INTO _cap_sd(_cap_sd, rank) VALUES('secure-delete', 1)")
    conn.execute("DROP TABLE _cap_sd")
    return True


def _probe_core_secure_delete(conn) -> bool:
    conn.execute("PRAGMA secure_delete = ON")
    return conn.execute("PRAGMA secure_delete").fetchone()[0] == 1


def _probe_foreign_keys(conn) -> bool:
    conn.execute("PRAGMA foreign_keys = ON")
    return conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def _cipher_version(conn) -> str:
    row = conn.execute("PRAGMA cipher_version").fetchone()
    return row[0] if row else ""


def assert_sqlite_capabilities(conn) -> dict:
    version = conn.execute("SELECT sqlite_version()").fetchone()[0]
    cipher_version = _cipher_version(conn)
    if not cipher_version:
        raise CapabilityError("SQLCipher is not active: PRAGMA cipher_version is empty")
    provider_row = conn.execute("PRAGMA cipher_provider").fetchone()
    try:
        caps = {
            "sqlcipher": True,
            "cipher_version": cipher_version,
            "cipher_provider": provider_row[0] if provider_row else "",
            "fts5": _probe_fts5(conn),
            "trigram": _probe_trigram(conn),
            "fts_secure_delete": _probe_fts_secure_delete(conn),
            "core_secure_delete": _probe_core_secure_delete(conn),
            "foreign_keys": _probe_foreign_keys(conn),
            "sqlite_version": version,
        }
    except Exception as exc:  # noqa: BLE001 - re-raised typed
        raise CapabilityError(f"SQLite build missing a required capability: {exc}") from exc
    for req in ("fts5", "trigram", "fts_secure_delete", "core_secure_delete", "foreign_keys"):
        if not caps[req]:
            raise CapabilityError(f"required capability absent: {req}")
    return caps
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_capabilities.py -v`
Expected: PASS (both). If `sqlcipher` is False or `cipher_version` empty, the binding built without SQLCipher — stop and fix the source build (Step 2) before continuing; the whole design depends on it.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml requirements.in requirements-lock.txt .gitignore CHANGELOG.md \
        src/whatsvault/__init__.py src/whatsvault/capabilities.py tests/__init__.py tests/test_capabilities.py
git commit -m "feat: scaffold vault core with deterministic sqlcipher install and capability gate"
```

---

### Task 2: Prefixed ULID identifiers

**Files:**
- Create: `src/whatsvault/ids.py`
- Test: `tests/test_ids.py`

**Interfaces:**
- Produces: `new_id(prefix) -> str`, `validate(prefix, value) -> str`, `PREFIXES` (frozenset), `IdError(ValueError)`.

- [ ] **Step 1: Write the failing test**

`tests/test_ids.py`:
```python
import re
import pytest
from whatsvault import ids


def test_registry_covers_every_schema_entity():
    # Every table with a ULID primary key needs a prefix.
    for p in ("acc", "cnt", "cnv", "src", "msg", "rev", "att", "evt",
              "drf", "apv", "atm", "cap", "dev", "bat", "aud"):
        assert p in ids.PREFIXES


def test_new_id_has_prefix_and_26_char_ulid():
    v = ids.new_id("msg")
    assert v.startswith("msg_")
    body = v[len("msg_"):]
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", body), body


def test_new_id_rejects_unknown_prefix():
    with pytest.raises(ids.IdError):
        ids.new_id("zzz")


def test_ids_are_unique():
    made = [ids.new_id("evt") for _ in range(50)]
    assert len(set(made)) == 50


def test_ids_are_orderable_without_error():
    made = [ids.new_id("evt") for _ in range(10)]
    ordered = sorted(made)          # lexical sort is a stable tie-breaker, not a chronology claim
    assert len(ordered) == len(made)


def test_validate_accepts_good_and_rejects_wrong_prefix():
    good = ids.new_id("cnt")
    assert ids.validate("cnt", good) == good
    with pytest.raises(ids.IdError):
        ids.validate("msg", good)


def test_validate_rejects_malformed_body():
    with pytest.raises(ids.IdError):
        ids.validate("msg", "msg_not-a-ulid")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_ids.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/whatsvault/ids.py`:
```python
"""Prefixed ULID identifiers (spec §3.2). Ordering is a stable tie-breaker
only, never message chronology."""
import re
from ulid import ULID

# One prefix per ULID-keyed table in the vault + control schemas.
PREFIXES = frozenset({
    "acc",  # accounts
    "cnt",  # contacts
    "cnv",  # conversations
    "src",  # conversation_sources
    "msg",  # messages
    "rev",  # message_revisions
    "att",  # attachments
    "evt",  # ingest_events / message_status_events
    "drf",  # drafts
    "apv",  # approvals
    "atm",  # send_attempts
    "cap",  # capability_grants
    "dev",  # approval_devices
    "bat",  # import_batches
    "aud",  # audit_log
})

_ULID_RE = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")


class IdError(ValueError):
    pass


def new_id(prefix: str) -> str:
    if prefix not in PREFIXES:
        raise IdError(f"unknown id prefix: {prefix!r}")
    return f"{prefix}_{ULID()!s}"


def validate(prefix: str, value: str) -> str:
    if prefix not in PREFIXES:
        raise IdError(f"unknown id prefix: {prefix!r}")
    marker = f"{prefix}_"
    if not value.startswith(marker):
        raise IdError(f"id {value!r} does not carry prefix {prefix!r}")
    if not _ULID_RE.fullmatch(value[len(marker):]):
        raise IdError(f"id {value!r} has malformed ULID body")
    return value
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_ids.py -v`
Expected: PASS (all 7).

- [ ] **Step 5: Commit**

```bash
git add src/whatsvault/ids.py tests/test_ids.py
git commit -m "feat: prefixed ULID identifiers covering all schema entities"
```

---

### Task 3: Time model — intervals, ordering, DST classification

**Files:**
- Create: `src/whatsvault/timemodel.py`
- Test: `tests/test_timemodel.py`

**Interfaces:**
- Produces: `Interval(ts_lower_ms, ts_upper_ms_exclusive, ts_precision)` (validated in `__post_init__`), `from_provider_seconds(int) -> Interval`, `from_local_minute(int) -> Interval`, `definitely_before(a,b) -> bool`, `temporal_overlap(a,b) -> bool`, `DstClass` enum, `classify_local(zone, local_dt) -> DstClass`.

- [ ] **Step 1: Write the failing test**

`tests/test_timemodel.py`:
```python
import datetime as dt
import pytest
from whatsvault import timemodel as tm


def test_provider_seconds_become_one_second_interval():
    iv = tm.from_provider_seconds(1603059201)
    assert (iv.ts_lower_ms, iv.ts_upper_ms_exclusive, iv.ts_precision) == (1603059201000, 1603059202000, "s")


def test_minute_interval_is_sixty_seconds_wide():
    iv = tm.from_local_minute(1603059180000)
    assert iv.ts_upper_ms_exclusive - iv.ts_lower_ms == 60000 and iv.ts_precision == "min"


def test_interval_rejects_inverted_bounds():
    with pytest.raises(ValueError):
        tm.Interval(100, 100, "s")   # upper must be > lower


def test_interval_rejects_bad_precision():
    with pytest.raises(ValueError):
        tm.Interval(0, 1000, "weeks")


def test_definitely_before_requires_no_overlap():
    a, b = tm.from_provider_seconds(100), tm.from_provider_seconds(200)
    assert tm.definitely_before(a, b) is True and tm.definitely_before(b, a) is False


def test_overlapping_intervals_are_not_definitely_ordered():
    second = tm.from_provider_seconds(1000)
    minute = tm.from_local_minute(1000000 - (1000000 % 60000))
    assert tm.temporal_overlap(second, minute) is True
    assert tm.definitely_before(second, minute) is False
    assert tm.definitely_before(minute, second) is False


def test_classify_local_detects_nonexistent_spring_forward():
    assert tm.classify_local("America/New_York", dt.datetime(2026, 3, 8, 2, 30)) == tm.DstClass.NONEXISTENT


def test_classify_local_detects_fold_fall_back():
    assert tm.classify_local("America/New_York", dt.datetime(2026, 11, 1, 1, 30)) == tm.DstClass.FOLD


def test_classify_local_ordinary_time_is_unambiguous():
    assert tm.classify_local("America/New_York", dt.datetime(2026, 6, 1, 12, 0)) == tm.DstClass.UNAMBIGUOUS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_timemodel.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/whatsvault/timemodel.py`:
```python
"""Time as uncertainty intervals (spec §3.3)."""
import datetime as dt
from dataclasses import dataclass
from enum import Enum
from zoneinfo import ZoneInfo

_PRECISIONS = {"ms", "s", "min", "day"}


@dataclass(frozen=True)
class Interval:
    ts_lower_ms: int
    ts_upper_ms_exclusive: int
    ts_precision: str

    def __post_init__(self):
        if self.ts_upper_ms_exclusive <= self.ts_lower_ms:
            raise ValueError("interval upper bound must exceed lower bound")
        if self.ts_precision not in _PRECISIONS:
            raise ValueError(f"bad precision: {self.ts_precision!r}")


def from_provider_seconds(ts_seconds: int) -> Interval:
    lower = int(ts_seconds) * 1000
    return Interval(lower, lower + 1000, "s")


def from_local_minute(epoch_minute_start_ms: int) -> Interval:
    return Interval(epoch_minute_start_ms, epoch_minute_start_ms + 60000, "min")


def definitely_before(a: Interval, b: Interval) -> bool:
    return a.ts_upper_ms_exclusive <= b.ts_lower_ms


def temporal_overlap(a: Interval, b: Interval) -> bool:
    return a.ts_lower_ms < b.ts_upper_ms_exclusive and b.ts_lower_ms < a.ts_upper_ms_exclusive


class DstClass(Enum):
    UNAMBIGUOUS = "unambiguous"
    FOLD = "fold"
    NONEXISTENT = "nonexistent"


def classify_local(zone: str, local_dt: dt.datetime) -> DstClass:
    tz = ZoneInfo(zone)
    aware = local_dt.replace(tzinfo=tz)
    normalised = aware.astimezone(dt.timezone.utc).astimezone(tz).replace(tzinfo=None)
    if normalised != local_dt:                     # whole wall-time, not just (hour, minute)
        return DstClass.NONEXISTENT
    off0 = local_dt.replace(tzinfo=tz, fold=0).utcoffset()
    off1 = local_dt.replace(tzinfo=tz, fold=1).utcoffset()
    return DstClass.FOLD if off0 != off1 else DstClass.UNAMBIGUOUS
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_timemodel.py -v`
Expected: PASS (all 9).

- [ ] **Step 5: Commit**

```bash
git add src/whatsvault/timemodel.py tests/test_timemodel.py
git commit -m "feat: validated interval time model with DST classification"
```

---

### Task 4: Keychain provision/require + versioned attachment AEAD

**Files:**
- Create: `src/whatsvault/crypto/__init__.py`, `src/whatsvault/crypto/keystore.py`, `src/whatsvault/crypto/atrest.py`
- Test: `tests/test_atrest.py`

**Interfaces:**
- Produces:
  - `keystore.KeyStore` (Protocol): `provision(name, nbytes) -> bytes` (creates; raises `KeyExists` if present), `require(name, nbytes) -> bytes` (loads; raises `KeyMissing` if absent; validates length).
  - `keystore.MemoryKeyStore`, `keystore.KeyringKeyStore` (asserts the macOS backend on construction), `keystore.KeyExists`, `keystore.KeyMissing`, `keystore.KeyStoreError`.
  - `atrest.seal_blob(key, plaintext, key_id=0, aad=b"") -> bytes` — versioned envelope `magic(4) || ver(1) || key_id(4 be) || nonce(12) || ct||tag`.
  - `atrest.open_blob(key, sealed, aad=b"") -> bytes` — raises `InvalidTag` on tamper, `ValueError` on bad envelope.
  - `atrest.ATTACHMENT_KEY_NAME`, `atrest.MAGIC`.

- [ ] **Step 1: Write the failing test**

`tests/test_atrest.py`:
```python
import pytest
from cryptography.exceptions import InvalidTag
from whatsvault.crypto import atrest
from whatsvault.crypto.keystore import MemoryKeyStore, KeyExists, KeyMissing


def test_provision_then_require():
    ks = MemoryKeyStore()
    k = ks.provision("attk", 32)
    assert len(k) == 32
    assert ks.require("attk", 32) == k


def test_provision_twice_refuses():
    ks = MemoryKeyStore()
    ks.provision("attk", 32)
    with pytest.raises(KeyExists):
        ks.provision("attk", 32)   # never silently regenerate


def test_require_missing_is_hard_failure():
    with pytest.raises(KeyMissing):
        MemoryKeyStore().require("nope", 32)


def test_require_wrong_length_rejected():
    ks = MemoryKeyStore()
    ks._d["short"] = b"\x00" * 16   # corrupt/wrong-length key
    with pytest.raises(ValueError):
        ks.require("short", 32)


def test_seal_open_roundtrip_versioned():
    key = MemoryKeyStore().provision(atrest.ATTACHMENT_KEY_NAME, 32)
    pt = b"an image's raw bytes \x00\xff\x10"
    sealed = atrest.seal_blob(key, pt, key_id=1, aad=b"att_ABC")
    assert sealed[:4] == atrest.MAGIC
    assert atrest.open_blob(key, sealed, aad=b"att_ABC") == pt


def test_tampered_ciphertext_fails():
    key = MemoryKeyStore().provision(atrest.ATTACHMENT_KEY_NAME, 32)
    sealed = bytearray(atrest.seal_blob(key, b"secret media"))
    sealed[-1] ^= 0x01
    with pytest.raises(InvalidTag):
        atrest.open_blob(key, bytes(sealed))


def test_wrong_aad_fails():
    key = MemoryKeyStore().provision(atrest.ATTACHMENT_KEY_NAME, 32)
    sealed = atrest.seal_blob(key, b"m", aad=b"att_ONE")
    with pytest.raises(InvalidTag):
        atrest.open_blob(key, sealed, aad=b"att_TWO")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_atrest.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/whatsvault/crypto/__init__.py`: `__all__ = []`

`src/whatsvault/crypto/keystore.py`:
```python
"""Key storage abstraction with PROVISION/REQUIRE separation. Keys are created
once (provision) and thereafter only loaded (require); require never
regenerates a missing key, because that would silently strand an existing
encrypted database under a new, wrong key (INV-ATREST)."""
import os
from typing import Protocol


class KeyStoreError(RuntimeError):
    pass


class KeyExists(KeyStoreError):
    pass


class KeyMissing(KeyStoreError):
    pass


class KeyStore(Protocol):
    def provision(self, name: str, nbytes: int) -> bytes: ...
    def require(self, name: str, nbytes: int) -> bytes: ...


class MemoryKeyStore:
    def __init__(self) -> None:
        self._d: dict[str, bytes] = {}

    def provision(self, name: str, nbytes: int) -> bytes:
        if name in self._d:
            raise KeyExists(name)
        self._d[name] = os.urandom(nbytes)
        return self._d[name]

    def require(self, name: str, nbytes: int) -> bytes:
        if name not in self._d:
            raise KeyMissing(name)
        key = self._d[name]
        if len(key) != nbytes:
            raise ValueError(f"key {name!r} has length {len(key)}, expected {nbytes}")
        return key


class KeyringKeyStore:
    """macOS Keychain via `keyring`. Refuses to run on a non-macOS backend so a
    fallback store (e.g. plaintext file) can never silently hold vault keys."""

    SERVICE = "whatsvault"

    def __init__(self) -> None:
        import keyring
        backend = type(keyring.get_keyring()).__module__
        if "macOS" not in backend and "keychain" not in backend.lower():
            raise KeyStoreError(f"expected macOS Keychain backend, got {backend!r}")

    def provision(self, name: str, nbytes: int) -> bytes:
        import keyring
        if keyring.get_password(self.SERVICE, name) is not None:
            raise KeyExists(name)
        key = os.urandom(nbytes)
        keyring.set_password(self.SERVICE, name, key.hex())
        return key

    def require(self, name: str, nbytes: int) -> bytes:
        import keyring
        v = keyring.get_password(self.SERVICE, name)
        if v is None:
            raise KeyMissing(name)
        key = bytes.fromhex(v)
        if len(key) != nbytes:
            raise ValueError(f"key {name!r} has length {len(key)}, expected {nbytes}")
        return key
```

`src/whatsvault/crypto/atrest.py`:
```python
"""Versioned AES-256-GCM sealing for attachment blobs at rest (INV-ATREST).
Envelope: magic(4) || version(1) || key_id(4, big-endian) || nonce(12) || ct||tag."""
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ATTACHMENT_KEY_NAME = "whatsvault.attachment.key.v1"
MAGIC = b"WVA1"
_VERSION = 1
_NONCE_LEN = 12
_HEADER_LEN = 4 + 1 + 4 + _NONCE_LEN


def seal_blob(key: bytes, plaintext: bytes, key_id: int = 0, aad: bytes = b"") -> bytes:
    nonce = os.urandom(_NONCE_LEN)
    header = MAGIC + bytes([_VERSION]) + key_id.to_bytes(4, "big") + nonce
    # Bind the header (version + key_id + nonce) as additional authenticated data.
    ct = AESGCM(key).encrypt(nonce, plaintext, header + aad)
    return header + ct


def open_blob(key: bytes, sealed: bytes, aad: bytes = b"") -> bytes:
    if len(sealed) < _HEADER_LEN or sealed[:4] != MAGIC:
        raise ValueError("not a WhatsVault attachment envelope")
    if sealed[4] != _VERSION:
        raise ValueError(f"unsupported envelope version {sealed[4]}")
    header = sealed[:_HEADER_LEN]
    nonce = sealed[9:_HEADER_LEN]
    ct = sealed[_HEADER_LEN:]
    return AESGCM(key).decrypt(nonce, ct, header + aad)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_atrest.py -v`
Expected: PASS (all 7).

- [ ] **Step 5: Commit**

```bash
git add src/whatsvault/crypto/__init__.py src/whatsvault/crypto/keystore.py src/whatsvault/crypto/atrest.py tests/test_atrest.py
git commit -m "feat: provision/require keystore and versioned attachment AEAD"
```

---

### Task 5: SQLCipher connection with eager key validation + at-rest proof

**Files:**
- Create: `src/whatsvault/db/__init__.py`, `src/whatsvault/db/connection.py`
- Test: `tests/test_connection.py`, `tests/test_at_rest_encryption.py`

**Interfaces:**
- Produces:
  - `connection.open_db(path, key) -> Connection` — applies `PRAGMA key`, `PRAGMA secure_delete=ON` (core), `PRAGMA foreign_keys=ON`, then **eagerly validates** the key with `SELECT count(*) FROM sqlite_master` before returning. No `cipher_secure_delete` (not a real pragma).
  - `connection.DB_KEY_NAMES = {"vault": "...", "control": "..."}`.
  - `connection.provision_db(kind, path, ks) -> Connection` — `ks.provision` the key, open, return (for a new DB).
  - `connection.open_existing(kind, path, ks) -> Connection` — `ks.require` the key, open (raises on wrong/missing key).

- [ ] **Step 1: Write the failing test**

`tests/test_connection.py`:
```python
import os
import pytest
import sqlcipher3
from whatsvault.db import connection as C
from whatsvault.crypto.keystore import MemoryKeyStore


def test_provision_write_then_open_existing(tmp_path):
    ks = MemoryKeyStore()
    p = str(tmp_path / "v.db")
    conn = C.provision_db("vault", p, ks)
    conn.execute("CREATE TABLE t(x TEXT)")
    conn.execute("INSERT INTO t(x) VALUES('hello')")
    conn.commit()
    conn.close()

    conn2 = C.open_existing("vault", p, ks)   # requires the same provisioned key
    assert conn2.execute("SELECT x FROM t").fetchone()[0] == "hello"
    assert conn2.execute("PRAGMA secure_delete").fetchone()[0] == 1
    conn2.close()


def test_open_db_eagerly_rejects_wrong_key(tmp_path):
    p = str(tmp_path / "v.db")
    good = os.urandom(32)
    conn = C.open_db(p, good)
    conn.execute("CREATE TABLE t(x TEXT)")
    conn.execute("INSERT INTO t(x) VALUES('secret')")
    conn.commit()
    conn.close()

    bad = os.urandom(32)
    with pytest.raises(sqlcipher3.DatabaseError):
        C.open_db(p, bad)   # must raise at open time, not defer to a later query
```

`tests/test_at_rest_encryption.py`:
```python
import os
from whatsvault.db import connection as C
from whatsvault.crypto import atrest
from whatsvault.crypto.keystore import MemoryKeyStore

SENTINEL = b"WV_PLAINTEXT_SENTINEL_9e1f7c"


def test_db_file_does_not_contain_plaintext(tmp_path):
    ks = MemoryKeyStore()
    p = str(tmp_path / "v.db")
    conn = C.provision_db("vault", p, ks)
    conn.execute("CREATE TABLE t(x TEXT)")
    conn.execute("INSERT INTO t(x) VALUES(?)", (SENTINEL.decode(),))
    conn.commit()
    conn.close()
    for suffix in ("", "-wal", "-shm"):
        fp = p + suffix
        if os.path.exists(fp):
            with open(fp, "rb") as fh:
                assert SENTINEL not in fh.read(), f"plaintext sentinel leaked into {fp}"


def test_attachment_blob_is_ciphertext_on_disk(tmp_path):
    key = MemoryKeyStore().provision(atrest.ATTACHMENT_KEY_NAME, 32)
    blob = atrest.seal_blob(key, SENTINEL + b" media payload")
    fp = tmp_path / "att.bin"
    fp.write_bytes(blob)
    assert SENTINEL not in fp.read_bytes()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_connection.py tests/test_at_rest_encryption.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/whatsvault/db/__init__.py`: `__all__ = []`

`src/whatsvault/db/connection.py`:
```python
"""Keyed SQLCipher connections. PRAGMA key is lazy — a wrong key does not fail
until a page is read — so open_db performs an eager read to validate the key
before returning (spec §2.4). Only real controls are applied: no
`cipher_secure_delete` (that pragma does not exist and silently no-ops)."""
import sqlcipher3

from whatsvault.crypto.keystore import KeyStore

DB_KEY_NAMES = {
    "vault": "whatsvault.vault.key.v1",
    "control": "whatsvault.control.key.v1",
}


def open_db(path: str, key: bytes):
    conn = sqlcipher3.connect(path)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")   # raw 32-byte key
    conn.execute("PRAGMA secure_delete = ON")          # core SQLite (verified to read back 1)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("SELECT count(*) FROM sqlite_master").fetchone()  # eager key validation
    return conn


def provision_db(kind: str, path: str, ks: KeyStore):
    if kind not in DB_KEY_NAMES:
        raise ValueError(f"unknown db kind: {kind!r}")
    key = ks.provision(DB_KEY_NAMES[kind], 32)
    return open_db(path, key)


def open_existing(kind: str, path: str, ks: KeyStore):
    if kind not in DB_KEY_NAMES:
        raise ValueError(f"unknown db kind: {kind!r}")
    key = ks.require(DB_KEY_NAMES[kind], 32)
    return open_db(path, key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_connection.py tests/test_at_rest_encryption.py -v`
Expected: PASS (all 4). The at-rest tests prove SQLCipher is genuinely encrypting (a regression guard against anyone swapping in plain SQLite).

- [ ] **Step 5: Commit**

```bash
git add src/whatsvault/db/__init__.py src/whatsvault/db/connection.py tests/test_connection.py tests/test_at_rest_encryption.py
git commit -m "feat: eager-validated SQLCipher connections with at-rest encryption proof"
```

---

### Task 6: Numbered migration runner + vault.db schema

Replaces the previous "runner" (which only handled schema 1) with a real numbered, transactional migration system that later phases extend. Vault schema carries an explicit `window_eligible` evidence flag (never `origin`) and load-bearing CHECK constraints.

**Files:**
- Create: `src/whatsvault/db/migrations/__init__.py`
- Create: `src/whatsvault/db/migrations/vault/0001_initial.sql`
- Create: `src/whatsvault/db/migrations/__init__.py` (the runner lives here; a sibling `migrations.py` is shadowed by the `migrations/` package)
- Test: `tests/test_schema_vault.py`, `tests/test_migrations.py`

**Interfaces:**
- Produces:
  - `migrations.migrate(conn, lane: str) -> int` — applies every pending numbered migration for lane `"vault"` or `"control"` inside a transaction each, bumping `PRAGMA user_version` atomically; returns the final version.
  - `migrations.user_version(conn) -> int`.
  - `migrations.MIGRATIONS: dict[str, list[tuple[int, str]]]`.
  - `migrations._read_asset(relpath) -> str` (monkeypatch seam for the failure test).

- [ ] **Step 1: Write the failing test**

`tests/test_migrations.py`:
```python
import os
import pytest
from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _conn(tmp_path, name="v.db"):
    return C.open_db(str(tmp_path / name), os.urandom(32))


def test_migrate_is_idempotent_and_sets_version(tmp_path):
    conn = _conn(tmp_path)
    v1 = M.migrate(conn, "vault")
    v2 = M.migrate(conn, "vault")   # second run is a no-op
    assert v1 == v2 == max(v for v, _ in M.MIGRATIONS["vault"])


def test_partial_failure_does_not_bump_version(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    M.migrate(conn, "vault")                       # reach version 1 cleanly
    # Append a bogus migration (2) whose SQL is invalid; version must stay 1.
    monkeypatch.setitem(M.MIGRATIONS, "vault",
                        M.MIGRATIONS["vault"] + [(2, "vault/_bogus.sql")])
    real_read = M._read_asset
    monkeypatch.setattr(M, "_read_asset",
                        lambda p: "CREATE TABLE bad(;" if p.endswith("_bogus.sql") else real_read(p))
    with pytest.raises(Exception):
        M.migrate(conn, "vault")
    assert M.user_version(conn) == 1
```

`tests/test_schema_vault.py`:
```python
import os
import pytest
import sqlcipher3
from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _vault(tmp_path):
    conn = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(conn, "vault")
    return conn


def test_ingest_events_semantic_key_unique(tmp_path):
    conn = _vault(tmp_path)
    ins = ("INSERT INTO ingest_events(id, provider, semantic_event_key, family, received_at_ms, "
           "raw_payload_sha256, raw_payload, parser_version) VALUES(?,?,?,?,?,?,?,?)")
    conn.execute(ins, ("evt_1", "meta", "KEY1", "MESSAGE_INBOUND", 1, "h", b"\x00", 1))
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute(ins, ("evt_2", "meta", "KEY1", "MESSAGE_INBOUND", 2, "h", b"\x00", 1))


def test_messages_wamid_unique_but_nulls_allowed(tmp_path):
    conn = _vault(tmp_path)
    conn.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn1')")
    conn.execute("INSERT INTO conversations(id, account_id, type) VALUES('cnv','acc','dm')")
    cols = ("id, account_id, conversation_id, direction, ts_lower_ms, ts_upper_ms_exclusive, "
            "ts_precision, type, text_original, origin, window_eligible")
    def ins(mid, wamid=None):
        conn.execute(
            f"INSERT INTO messages({cols}, wamid) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, "acc", "cnv", "in", 1, 2, "s", "text", "hi", "cloud_api", 0, wamid))
    ins("msg_a"); ins("msg_b")               # two NULL wamids OK
    ins("msg_c", "wamid.X")
    with pytest.raises(sqlcipher3.IntegrityError):
        ins("msg_d", "wamid.X")              # duplicate non-null wamid rejected


def test_interval_check_rejects_inverted_ts(tmp_path):
    conn = _vault(tmp_path)
    conn.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn')")
    conn.execute("INSERT INTO conversations(id, account_id, type) VALUES('cnv','acc','dm')")
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute(
            "INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, "
            "ts_upper_ms_exclusive, ts_precision, type, text_original, origin, window_eligible) "
            "VALUES('msg_x','acc','cnv','in',5,5,'s','text','x','cloud_api',0)")  # upper == lower


def test_status_events_have_no_mandatory_fk(tmp_path):
    conn = _vault(tmp_path)
    conn.execute("INSERT INTO message_status_events(id, wamid, status, provider_ts_ms, recipient_id) "
                 "VALUES('evt_s','wamid.Y','sent',1,'r')")
    assert conn.execute("SELECT message_internal_id FROM message_status_events").fetchone()[0] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_migrations.py tests/test_schema_vault.py -v`
Expected: FAIL with `ModuleNotFoundError` / missing `migrate`.

- [ ] **Step 3: Write minimal implementation**

`src/whatsvault/db/migrations/__init__.py`: `__all__ = []`

`src/whatsvault/db/migrations/vault/0001_initial.sql`:
```sql
CREATE TABLE accounts (
    id TEXT PRIMARY KEY, waba_id TEXT, phone_number_id TEXT NOT NULL, display_phone TEXT
);

CREATE TABLE contacts (
    id TEXT PRIMARY KEY, wa_id TEXT, wa_id_hash TEXT, display_name TEXT, push_name TEXT,
    first_seen_ms INTEGER, last_seen_ms INTEGER
);

CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    type TEXT NOT NULL CHECK (type IN ('dm','group')),
    wa_chat_id TEXT, subject TEXT, last_message_ms INTEGER
);

CREATE TABLE conversation_sources (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    source_kind TEXT NOT NULL CHECK (source_kind IN ('manual_export','meta_cloud','history_sync')),
    external_identifier TEXT,
    write_capable INTEGER NOT NULL DEFAULT 0 CHECK (write_capable IN (0,1)),
    account_id TEXT REFERENCES accounts(id),
    import_batch_id TEXT
);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    sender_contact_id TEXT REFERENCES contacts(id),
    direction TEXT NOT NULL CHECK (direction IN ('in','out')),
    ts_lower_ms INTEGER NOT NULL,
    ts_upper_ms_exclusive INTEGER NOT NULL,
    ts_precision TEXT NOT NULL CHECK (ts_precision IN ('ms','s','min','day')),
    ts_ingested_ms INTEGER,
    tz_name TEXT,
    tz_basis TEXT CHECK (tz_basis IN ('provider','explicit_import_setting','inferred','unknown')),
    type TEXT NOT NULL,
    text_original TEXT,
    reply_to_wamid TEXT,
    origin TEXT NOT NULL CHECK (origin IN ('cloud_api','business_app_echo','history_sync','manual_export')),
    -- 24h-window eligibility is an EXPLICIT normaliser decision, never derived from origin (spec I5).
    window_eligible INTEGER NOT NULL DEFAULT 0 CHECK (window_eligible IN (0,1)),
    wamid TEXT,
    import_fingerprint TEXT,
    edited_at_ms INTEGER,
    deleted_at_ms INTEGER,
    delivery_rank INTEGER NOT NULL DEFAULT 0 CHECK (delivery_rank BETWEEN 0 AND 3),
    failed_at_ms INTEGER,
    CHECK (ts_upper_ms_exclusive > ts_lower_ms)
);
CREATE UNIQUE INDEX ux_messages_wamid ON messages(account_id, wamid) WHERE wamid IS NOT NULL;
CREATE UNIQUE INDEX ux_messages_import_fp ON messages(import_fingerprint) WHERE import_fingerprint IS NOT NULL;
CREATE INDEX ix_messages_window ON messages(conversation_id, direction, window_eligible);

CREATE TABLE message_revisions (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(id),
    revision_number INTEGER NOT NULL CHECK (revision_number >= 0),
    event_id TEXT,
    text_original TEXT,
    ts_lower_ms INTEGER,
    UNIQUE(message_id, revision_number)
);

CREATE TABLE attachments (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(id),
    provider_media_id TEXT,
    provider_sha256 TEXT,
    mime TEXT,
    size INTEGER CHECK (size IS NULL OR size >= 0),
    retrieval_state TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (retrieval_state IN ('PENDING','FETCHED','TEMPORARILY_FAILED','UNAVAILABLE','BACKFILLED',
                                   'MEDIA_PLACEHOLDER','FILE_PRESENT','FILE_NOT_INCLUDED_IN_EXPORT','FILE_REFERENCE_BROKEN')),
    quarantine_state TEXT NOT NULL DEFAULT 'quarantined',
    retrieved_at_ms INTEGER,
    last_attempt_ms INTEGER,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error_code TEXT,
    storage_path TEXT
);
CREATE UNIQUE INDEX ux_attachments_media ON attachments(message_id, provider_media_id) WHERE provider_media_id IS NOT NULL;

CREATE TABLE message_status_events (
    id TEXT PRIMARY KEY,
    wamid TEXT NOT NULL,
    message_internal_id TEXT,             -- no FK; reconciled later
    status TEXT NOT NULL,
    provider_ts_ms INTEGER NOT NULL,
    recipient_id TEXT,
    raw_payload_sha256 TEXT
);

CREATE TABLE ingest_events (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    external_event_id TEXT,
    semantic_event_key TEXT NOT NULL,
    family TEXT NOT NULL,
    provider_ts_ms INTEGER,
    received_at_ms INTEGER NOT NULL,
    raw_payload_sha256 TEXT NOT NULL,
    raw_payload BLOB NOT NULL,
    parser_version INTEGER NOT NULL
);
CREATE UNIQUE INDEX ux_ingest_semantic ON ingest_events(provider, semantic_event_key);

-- Deny-by-default evidence immutability (spec §3.6). BEFORE UPDATE OF <cols> fires
-- only when one of the listed evidence columns is in the SET clause; the four
-- projection columns (delivery_rank, failed_at_ms, deleted_at_ms, edited_at_ms)
-- are intentionally NOT listed and remain updatable.
CREATE TRIGGER trg_messages_evidence_immutable
BEFORE UPDATE OF account_id, conversation_id, sender_contact_id, direction,
                 ts_lower_ms, ts_upper_ms_exclusive, ts_precision, ts_ingested_ms,
                 tz_name, tz_basis, type, text_original, reply_to_wamid, origin,
                 window_eligible, wamid, import_fingerprint
ON messages
BEGIN SELECT RAISE(ABORT, 'message evidence fields are immutable'); END;

CREATE TRIGGER trg_status_evidence_immutable
BEFORE UPDATE OF wamid, status, provider_ts_ms, recipient_id, raw_payload_sha256
ON message_status_events
BEGIN SELECT RAISE(ABORT, 'status evidence is immutable (only message_internal_id backlink may change)'); END;

CREATE TRIGGER trg_revisions_immutable
BEFORE UPDATE ON message_revisions
BEGIN SELECT RAISE(ABORT, 'message_revisions are immutable evidence'); END;

CREATE TRIGGER trg_ingest_immutable
BEFORE UPDATE ON ingest_events
BEGIN SELECT RAISE(ABORT, 'ingest_events are write-once evidence'); END;
```

`src/whatsvault/db/migrations/__init__.py` (the runner — a sibling `migrations.py` would be shadowed by this package directory):
```python
"""Numbered, transactional migration runner. Each migration runs inside a
BEGIN/COMMIT and bumps PRAGMA user_version atomically; a failure rolls back and
leaves the version unchanged."""
from importlib import resources

MIGRATIONS: dict[str, list[tuple[int, str]]] = {
    "vault": [(1, "vault/0001_initial.sql")],
    "control": [(1, "control/0001_initial.sql")],
}


def user_version(conn) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _read_asset(relpath: str) -> str:
    return resources.files("whatsvault.db.migrations").joinpath(relpath).read_text(encoding="utf-8")


def migrate(conn, lane: str) -> int:
    if lane not in MIGRATIONS:
        raise ValueError(f"unknown migration lane: {lane!r}")
    for version, relpath in MIGRATIONS[lane]:
        if version <= user_version(conn):
            continue
        sql = _read_asset(relpath)
        # BEGIN/COMMIT inside executescript gives atomicity and handles triggers/semicolons.
        script = f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;"
        try:
            conn.executescript(script)
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
    return user_version(conn)
```

Note: `migrations/vault/` and `migrations/control/` ship via the `package-data` glob set in Task 1. `resources.files("whatsvault.db.migrations")` resolves because `migrations/__init__.py` exists and the editable install (Task 1) maps the source tree.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_migrations.py tests/test_schema_vault.py -v`
Expected: PASS (all). `test_partial_failure_does_not_bump_version` confirms a failed migration leaves `user_version` at 1.

- [ ] **Step 5: Commit**

```bash
git add src/whatsvault/db/migrations.py src/whatsvault/db/migrations/ tests/test_migrations.py tests/test_schema_vault.py
git commit -m "feat: numbered transactional migrations and vault schema with window_eligible + checks"
```

---

### Task 7: Verify evidence immutability (deny-by-default)

The triggers were created in Task 6's migration; this task proves they enforce the full invariant — not just `text_original` — and that the four projection columns remain updatable.

**Files:**
- Test: `tests/test_immutability.py`

**Interfaces:** consumes Task 6 schema; adds no new production code (verification task).

- [ ] **Step 1: Write the test**

`tests/test_immutability.py`:
```python
import os
import pytest
import sqlcipher3
from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _vault(tmp_path):
    conn = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(conn, "vault")
    conn.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn')")
    conn.execute("INSERT INTO conversations(id, account_id, type) VALUES('cnv','acc','dm')")
    conn.execute(
        "INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, "
        "ts_upper_ms_exclusive, ts_precision, type, text_original, origin, window_eligible) "
        "VALUES('msg_1','acc','cnv','in',1,2,'s','text','original body','cloud_api',0)")
    conn.commit()
    return conn


@pytest.mark.parametrize("col,val", [
    ("text_original", "'tampered'"),
    ("sender_contact_id", "'cnt_evil'"),
    ("origin", "'manual_export'"),
    ("wamid", "'wamid.injected'"),
    ("window_eligible", "1"),          # forging a messaging capability must be impossible
    ("ts_lower_ms", "9999"),
])
def test_evidence_fields_are_immutable(tmp_path, col, val):
    conn = _vault(tmp_path)
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute(f"UPDATE messages SET {col}={val} WHERE id='msg_1'")


@pytest.mark.parametrize("col,val", [
    ("delivery_rank", "2"),
    ("failed_at_ms", "123"),
    ("deleted_at_ms", "456"),
    ("edited_at_ms", "789"),
])
def test_projection_fields_remain_updatable(tmp_path, col, val):
    conn = _vault(tmp_path)
    conn.execute(f"UPDATE messages SET {col}={val} WHERE id='msg_1'")   # must NOT raise
    assert conn.execute(f"SELECT {col} FROM messages WHERE id='msg_1'").fetchone()[0] == int(val)


def test_ingest_events_fully_immutable(tmp_path):
    conn = _vault(tmp_path)
    conn.execute("INSERT INTO ingest_events(id, provider, semantic_event_key, family, received_at_ms, "
                 "raw_payload_sha256, raw_payload, parser_version) VALUES('evt_1','meta','K','MESSAGE_INBOUND',1,'h',X'0102',1)")
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("UPDATE ingest_events SET raw_payload=X'9999' WHERE id='evt_1'")
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("UPDATE ingest_events SET family='OTHER' WHERE id='evt_1'")


def test_status_backlink_updatable_but_evidence_frozen(tmp_path):
    conn = _vault(tmp_path)
    conn.execute("INSERT INTO message_status_events(id, wamid, status, provider_ts_ms) VALUES('evt_s','w','sent',1)")
    conn.execute("UPDATE message_status_events SET message_internal_id='msg_1' WHERE id='evt_s'")  # allowed
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("UPDATE message_status_events SET status='read' WHERE id='evt_s'")            # frozen
```

- [ ] **Step 2: Run the test**

Run: `.venv/bin/pytest tests/test_immutability.py -v`
Expected: PASS (all). If any `test_evidence_fields_are_immutable` case does NOT raise, the Task 6 trigger's `OF` column list is incomplete — fix `0001_initial.sql` and re-run.

- [ ] **Step 3: Commit**

```bash
git add tests/test_immutability.py
git commit -m "test: prove deny-by-default evidence immutability across all evidence fields"
```

---

### Task 8: Family-specific, domain-tagged semantic dedupe keys

**Files:**
- Create: `src/whatsvault/ingest/__init__.py`, `src/whatsvault/ingest/dedupe.py`
- Test: `tests/test_dedupe.py`

**Interfaces:**
- Produces: `dedupe.message_key(provider, phone_number_id, wamid) -> str`; `dedupe.status_key(provider, phone_number_id, wamid, status, provider_ts_ms, recipient_id) -> str` (`recipient_id` may be `None`); both lowercase hex SHA-256, domain-tagged.

- [ ] **Step 1: Write the failing test**

`tests/test_dedupe.py`:
```python
from whatsvault.ingest import dedupe as D


def test_message_key_is_stable_and_hex():
    k = D.message_key("meta", "pn1", "wamid.A")
    assert k == D.message_key("meta", "pn1", "wamid.A")
    assert len(k) == 64 and all(c in "0123456789abcdef" for c in k)


def test_status_ranks_of_same_wamid_do_not_collide():
    base = ("meta", "pn1", "wamid.A")
    keys = {D.status_key(*base, s, ts, "r") for s, ts in [("sent",1),("delivered",2),("read",3)]}
    assert len(keys) == 3


def test_status_and_message_families_do_not_collide():
    assert D.message_key("meta","pn1","wamid.A") != D.status_key("meta","pn1","wamid.A","sent",1,"r")


def test_missing_recipient_is_handled():
    k = D.status_key("meta", "pn1", "wamid.A", "sent", 1, None)   # must not raise
    assert len(k) == 64
    assert k != D.status_key("meta", "pn1", "wamid.A", "sent", 1, "r")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_dedupe.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/whatsvault/ingest/__init__.py`: `__all__ = []`

`src/whatsvault/ingest/dedupe.py`:
```python
"""Family-specific, domain-tagged semantic dedupe keys (spec §3.8)."""
import hashlib

_MSG_DOMAIN = "WHATSVAULT-DEDUPE-MESSAGE-V1"
_STATUS_DOMAIN = "WHATSVAULT-DEDUPE-STATUS-V1"


def _sha(domain: str, *parts: str) -> str:
    h = hashlib.sha256()
    h.update(domain.encode("utf-8"))
    for p in parts:
        b = p.encode("utf-8")
        h.update(len(b).to_bytes(4, "big"))   # length-prefix so parts can't run together
        h.update(b)
    return h.hexdigest()


def message_key(provider: str, phone_number_id: str, wamid: str) -> str:
    return _sha(_MSG_DOMAIN, provider, phone_number_id, wamid)


def status_key(provider: str, phone_number_id: str, wamid: str,
               status: str, provider_ts_ms: int, recipient_id) -> str:
    # A missing recipient is represented distinctly from any real recipient id.
    rid = "\x00none" if recipient_id is None else recipient_id
    return _sha(_STATUS_DOMAIN, provider, phone_number_id, wamid, status, str(provider_ts_ms), rid)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_dedupe.py -v`
Expected: PASS (all 4).

- [ ] **Step 5: Commit**

```bash
git add src/whatsvault/ingest/__init__.py src/whatsvault/ingest/dedupe.py tests/test_dedupe.py
git commit -m "feat: domain-tagged family-specific dedupe keys"
```

---

### Task 9: Status-reduction lattice

**Files:**
- Create: `src/whatsvault/ingest/status.py`
- Test: `tests/test_status.py`

**Interfaces:**
- Produces: `status.RANK`; `status.reduce_status(events) -> dict` with keys `delivery_rank`, `failed_at_ms`, `deleted_at_ms`, `unknown_statuses` (sorted list of any statuses not in the known set — surfaced, not silently dropped). Documented: `failed_at_ms`/`deleted_at_ms` record the **earliest** provider timestamp.

- [ ] **Step 1: Write the failing test**

`tests/test_status.py`:
```python
from whatsvault.ingest import status as S


def test_empty_is_unknown():
    assert S.reduce_status([]) == {"delivery_rank": 0, "failed_at_ms": None,
                                   "deleted_at_ms": None, "unknown_statuses": []}


def test_rank_is_max_of_success_events():
    ev = [{"status": s, "provider_ts_ms": i} for i, s in enumerate(["sent","delivered","read"], 1)]
    assert S.reduce_status(ev)["delivery_rank"] == 3


def test_late_sent_after_read_does_not_downgrade():
    ev = [{"status": "read", "provider_ts_ms": 3}, {"status": "sent", "provider_ts_ms": 99}]
    assert S.reduce_status(ev)["delivery_rank"] == 3


def test_failed_and_deleted_are_orthogonal_and_earliest():
    ev = [{"status": "read", "provider_ts_ms": 3},
          {"status": "failed", "provider_ts_ms": 9}, {"status": "failed", "provider_ts_ms": 5},
          {"status": "deleted", "provider_ts_ms": 7}]
    out = S.reduce_status(ev)
    assert out["delivery_rank"] == 3
    assert out["failed_at_ms"] == 5           # earliest
    assert out["deleted_at_ms"] == 7


def test_unknown_statuses_are_surfaced():
    out = S.reduce_status([{"status": "warp_speed", "provider_ts_ms": 1}])
    assert out["unknown_statuses"] == ["warp_speed"]
    assert out["delivery_rank"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_status.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/whatsvault/ingest/status.py`:
```python
"""Monotonic delivery-rank lattice with orthogonal failure/deletion (spec §3.7).
Arrival order is irrelevant; rank is MAX over success events. failed_at_ms and
deleted_at_ms record the EARLIEST provider timestamp. Unknown statuses are
surfaced for diagnosis rather than silently ignored."""

RANK = {"sent": 1, "delivered": 2, "read": 3}
_KNOWN = set(RANK) | {"failed", "deleted"}


def reduce_status(events: list[dict]) -> dict:
    rank = 0
    failed_at = None
    deleted_at = None
    unknown: set[str] = set()
    for e in events:
        s = e["status"]
        ts = e["provider_ts_ms"]
        if s in RANK:
            rank = max(rank, RANK[s])
        elif s == "failed":
            failed_at = ts if failed_at is None else min(failed_at, ts)
        elif s == "deleted":
            deleted_at = ts if deleted_at is None else min(deleted_at, ts)
        else:
            unknown.add(s)
    return {"delivery_rank": rank, "failed_at_ms": failed_at,
            "deleted_at_ms": deleted_at, "unknown_statuses": sorted(unknown)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_status.py -v`
Expected: PASS (all 5).

- [ ] **Step 5: Commit**

```bash
git add src/whatsvault/ingest/status.py tests/test_status.py
git commit -m "feat: status lattice with earliest-timestamp flags and unknown-status surfacing"
```

---

### Task 10: control.db schema aligned to the signed protocol

Full `control.db` migration matching the frozen signing byte contract: BLOB nonces/hashes with length checks, all P7-bound fields, defined public-key representation, same-database FKs, state CHECKs, the `conversation_windows` projection (moved here from vault), draft-freeze + append-only triggers.

**Files:**
- Create: `src/whatsvault/db/migrations/control/0001_initial.sql`
- Test: `tests/test_schema_control.py`

**Interfaces:** consumes `migrate(conn, "control")`.

- [ ] **Step 1: Write the failing test**

`tests/test_schema_control.py`:
```python
import os
import pytest
import sqlcipher3
from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _control(tmp_path):
    conn = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(conn, "control")
    return conn


def test_nonce_and_hash_are_32_byte_blobs(tmp_path):
    conn = _control(tmp_path)
    good = ("INSERT INTO drafts(id, conversation_id, account_id, phone_number_id, kind, "
            "nonce, body_sha256, state) VALUES('drf_1','cnv','acc','pn','text',?,?,'DRAFT')")
    conn.execute(good, (b"\x11"*32, b"\x22"*32))            # exactly 32 bytes OK
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("INSERT INTO drafts(id, conversation_id, account_id, phone_number_id, kind, nonce, state) "
                     "VALUES('drf_2','cnv','acc','pn','text',?, 'DRAFT')", (b"\x11"*16,))  # 16 bytes rejected


def test_draft_state_is_constrained(tmp_path):
    conn = _control(tmp_path)
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("INSERT INTO drafts(id, conversation_id, account_id, phone_number_id, kind, state) "
                     "VALUES('drf_3','cnv','acc','pn','text','MAGICALLY_APPROVED')")


def test_nonce_single_use_and_idempotency(tmp_path):
    conn = _control(tmp_path)
    conn.execute("INSERT INTO approval_nonces(nonce, consumed_by, consumed_at_ms) VALUES(?,?,?)", (b"\x33"*32,"atm_1",1))
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("INSERT INTO approval_nonces(nonce, consumed_by, consumed_at_ms) VALUES(?,?,?)", (b"\x33"*32,"atm_2",2))
    conn.execute("INSERT INTO drafts(id, conversation_id, account_id, phone_number_id, kind, state) VALUES('drf_1','cnv','acc','pn','text','DRAFT')")
    conn.execute("INSERT INTO send_attempts(id, draft_id, idempotency_key, state) VALUES('atm_1','drf_1','IK1','SUBMITTING')")
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("INSERT INTO send_attempts(id, draft_id, idempotency_key, state) VALUES('atm_9','drf_1','IK1','SUBMITTING')")


def test_same_db_foreign_keys_enforced(tmp_path):
    conn = _control(tmp_path)
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("INSERT INTO send_attempts(id, draft_id, idempotency_key, state) VALUES('atm_x','drf_missing','IK2','SUBMITTING')")


def test_draft_freezes_once_state_leaves_draft(tmp_path):
    conn = _control(tmp_path)
    conn.execute("INSERT INTO drafts(id, conversation_id, account_id, phone_number_id, kind, nonce, body_sha256, state) "
                 "VALUES('drf_1','cnv','acc','pn','text',?,?,'DRAFT')", (b"\x11"*32, b"\x22"*32))
    conn.execute("UPDATE drafts SET state='PENDING_APPROVAL' WHERE id='drf_1'")   # state transition allowed
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("UPDATE drafts SET body_sha256=? WHERE id='drf_1'", (b"\x99"*32,))  # frozen core field


def test_audit_log_append_only(tmp_path):
    conn = _control(tmp_path)
    conn.execute("INSERT INTO audit_log(id, actor, tool, args_hash, outcome, ts_ms) VALUES('aud_1','mcp','search','h','ok',1)")
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("UPDATE audit_log SET outcome='changed' WHERE id='aud_1'")
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("DELETE FROM audit_log WHERE id='aud_1'")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_schema_control.py -v`
Expected: FAIL — no `control/0001_initial.sql`.

- [ ] **Step 3: Write minimal implementation**

`src/whatsvault/db/migrations/control/0001_initial.sql`:
```sql
CREATE TABLE approval_devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    public_key BLOB NOT NULL,                      -- SEC1/X9.63 uncompressed point (0x04 || X || Y), 65 bytes for P-256
    key_algorithm TEXT NOT NULL DEFAULT 'P-256' CHECK (key_algorithm IN ('P-256')),
    key_encoding TEXT NOT NULL DEFAULT 'sec1-uncompressed' CHECK (key_encoding IN ('sec1-uncompressed')),
    created_at_ms INTEGER,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','REVOKED'))
);

CREATE TABLE drafts (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    phone_number_id TEXT NOT NULL,
    recipient_id TEXT,
    recipient_wa_id TEXT,
    recipient_display_snapshot TEXT,
    body_bytes BLOB,
    body_sha256 BLOB CHECK (body_sha256 IS NULL OR length(body_sha256) = 32),
    kind TEXT NOT NULL CHECK (kind IN ('text','template','mark_read')),
    template_id TEXT,
    template_params_sha256 BLOB CHECK (template_params_sha256 IS NULL OR length(template_params_sha256) = 32),
    attachments_digest BLOB CHECK (attachments_digest IS NULL OR length(attachments_digest) = 32),
    reply_to_wamid TEXT,
    nonce BLOB UNIQUE CHECK (nonce IS NULL OR length(nonce) = 32),
    created_at_ms INTEGER,
    expires_at_ms INTEGER,
    created_by TEXT CHECK (created_by IS NULL OR created_by IN ('mcp','scheduler')),
    state TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (state IN ('DRAFT','PENDING_APPROVAL','APPROVAL_RECEIVED','SENDING','SUBMITTING',
                         'SUBMITTED','EXPIRED','REJECTED','CANCELLED','FAILED',
                         'INDETERMINATE','ABANDONED_INDETERMINATE'))
);

CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL REFERENCES drafts(id),
    device_id TEXT NOT NULL REFERENCES approval_devices(id),
    decision TEXT NOT NULL CHECK (decision IN ('APPROVE','REJECT')),
    signature BLOB CHECK (signature IS NULL OR length(signature) = 64),   -- raw r||s
    envelope BLOB,
    received_at_ms INTEGER,
    nonce BLOB CHECK (nonce IS NULL OR length(nonce) = 32),
    UNIQUE(draft_id, device_id, decision, nonce)
);

CREATE TABLE approval_nonces (
    nonce BLOB PRIMARY KEY CHECK (length(nonce) = 32),
    consumed_by TEXT,
    consumed_at_ms INTEGER
);

CREATE TABLE send_attempts (
    id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL REFERENCES drafts(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('SUBMITTING','SUBMITTED','FAILED','INDETERMINATE','ABANDONED_INDETERMINATE')),
    wamid TEXT,
    error_code TEXT,
    biz_opaque_callback_data TEXT,
    created_at_ms INTEGER,
    updated_at_ms INTEGER
);

CREATE TABLE capability_grants (
    capability_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES approval_devices(id),
    account_id TEXT,
    conversation_id TEXT,
    action TEXT NOT NULL,
    created_at_ms INTEGER,
    expires_at_ms INTEGER,
    max_actions INTEGER CHECK (max_actions IS NULL OR max_actions >= 0),
    used_count INTEGER NOT NULL DEFAULT 0 CHECK (used_count >= 0),
    nonce BLOB CHECK (nonce IS NULL OR length(nonce) = 32),
    signature BLOB CHECK (signature IS NULL OR length(signature) = 64),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','REVOKED'))
);

-- Send-authoritative 24h-window projection lives HERE (INV-SENDPOLICY), not in vault.db.
CREATE TABLE conversation_windows (
    conversation_id TEXT PRIMARY KEY,
    last_inbound_ms INTEGER NOT NULL DEFAULT 0 CHECK (last_inbound_ms >= 0)
);

CREATE TABLE audit_log (
    id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    tool TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    outcome TEXT NOT NULL,
    ts_ms INTEGER NOT NULL
);

CREATE TRIGGER trg_draft_freeze
BEFORE UPDATE OF body_bytes, body_sha256, recipient_wa_id, recipient_id, account_id,
                 phone_number_id, nonce, expires_at_ms, kind, template_id,
                 template_params_sha256, attachments_digest, reply_to_wamid
ON drafts
WHEN OLD.state <> 'DRAFT'
BEGIN SELECT RAISE(ABORT, 'draft core fields freeze once state leaves DRAFT'); END;

CREATE TRIGGER trg_audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_schema_control.py -v`
Expected: PASS (all 6).

- [ ] **Step 5: Commit**

```bash
git add src/whatsvault/db/migrations/control/ tests/test_schema_control.py
git commit -m "feat: control.db schema aligned to signed byte contract with freeze/append-only triggers"
```

---

### Task 11: `doctor` — rebuild-from-evidence, drift detection, integrity checks

Separates the live `advance_window` (MAX, used during ingest) from the doctor's `rebuild_window_from_evidence` (exact MAX over `window_eligible=1` evidence in **vault.db**, reported into the **control.db** projection with drift detection). `check_vault` runs real structural + SQLCipher integrity checks and does what its interface advertises.

**Files:**
- Create: `src/whatsvault/doctor.py`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Produces:
  - `doctor.advance_window(control_conn, conversation_id, incoming_provider_ms) -> int` — live path, `MAX(existing, incoming)`.
  - `doctor.rebuild_window_from_evidence(vault_conn, control_conn, conversation_id) -> dict` — computes exact `MAX(ts_lower_ms)` over `direction='in' AND window_eligible=1` in vault; returns `{"evidence_ms", "stored_ms", "drift": bool}` and rewrites the control projection to the evidence truth (repairs a forged/future value downward).
  - `doctor.check_vault(vault_conn) -> list[dict]` — findings for `message_id_prefix`, `integrity_check`, `foreign_key_check`, `cipher_integrity_check`.

- [ ] **Step 1: Write the failing test**

`tests/test_doctor.py`:
```python
import os
import pytest
from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault import doctor, ids


def _dbs(tmp_path):
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32)); M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32)); M.migrate(c, "control")
    v.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn')")
    v.execute("INSERT INTO conversations(id, account_id, type) VALUES('cnv','acc','dm')")
    c.execute("INSERT INTO conversation_windows(conversation_id, last_inbound_ms) VALUES('cnv',0)")
    return v, c


def _msg(v, mid, origin, direction, ts, eligible):
    v.execute("INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, "
              "ts_upper_ms_exclusive, ts_precision, type, text_original, origin, window_eligible) "
              f"VALUES('{mid}','acc','cnv','{direction}',{ts},{ts+1000},'s','text','x','{origin}',{eligible})")


def test_rebuild_uses_eligible_evidence_only(tmp_path):
    v, c = _dbs(tmp_path)
    _msg(v, "msg_1", "cloud_api", "in", 5000, 1)
    _msg(v, "msg_2", "cloud_api", "in", 9000, 1)          # newest eligible
    _msg(v, "msg_3", "cloud_api", "in", 999000, 0)        # inbound but NOT window-eligible -> excluded
    _msg(v, "msg_4", "history_sync", "in", 888000, 0)     # history never eligible
    _msg(v, "msg_5", "cloud_api", "out", 12000, 1)        # outbound excluded
    v.commit()
    out = doctor.rebuild_window_from_evidence(v, c, "cnv")
    assert out["evidence_ms"] == 9000
    assert c.execute("SELECT last_inbound_ms FROM conversation_windows WHERE conversation_id='cnv'").fetchone()[0] == 9000


def test_doctor_repairs_forged_future_window_downward(tmp_path):
    v, c = _dbs(tmp_path)
    c.execute("UPDATE conversation_windows SET last_inbound_ms=32503680000000 WHERE conversation_id='cnv'")  # year ~3000
    _msg(v, "msg_1", "cloud_api", "in", 9000, 1); v.commit()
    out = doctor.rebuild_window_from_evidence(v, c, "cnv")
    assert out["drift"] is True
    assert out["evidence_ms"] == 9000
    assert c.execute("SELECT last_inbound_ms FROM conversation_windows WHERE conversation_id='cnv'").fetchone()[0] == 9000  # repaired DOWN


def test_advance_window_is_monotonic_for_live_path(tmp_path):
    v, c = _dbs(tmp_path)
    c.execute("UPDATE conversation_windows SET last_inbound_ms=50000 WHERE conversation_id='cnv'")
    assert doctor.advance_window(c, "cnv", 9000) == 50000    # older incoming does not lower live window
    assert doctor.advance_window(c, "cnv", 70000) == 70000


def test_check_vault_flags_bad_id_and_runs_integrity(tmp_path):
    v, _ = _dbs(tmp_path)
    good = ids.new_id("msg")
    _msg(v, good, "cloud_api", "in", 1, 1)
    v.execute("INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, "
              "ts_upper_ms_exclusive, ts_precision, type, text_original, origin, window_eligible) "
              "VALUES('BADID','acc','cnv','in',1,2,'s','text','x','cloud_api',0)")
    v.commit()
    findings = {f["check"]: f for f in doctor.check_vault(v)}
    assert findings["message_id_prefix"]["ok"] is False       # BADID fails
    assert findings["integrity_check"]["ok"] is True
    assert findings["foreign_key_check"]["ok"] is True
    assert "cipher_integrity_check" in findings
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_doctor.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/whatsvault/doctor.py`:
```python
"""Vault integrity checks (spec §3.7 I5 + structural + SQLCipher integrity).
A doctor reconstructs evidence truth and repairs drift; it never preserves a
forged or future window value. Send-side invariants I2/I3/I4 are Phase 4."""
from whatsvault import ids


def advance_window(control_conn, conversation_id: str, incoming_provider_ms: int) -> int:
    """Live ingest path: monotonic MAX(existing, incoming). Never used by doctor."""
    row = control_conn.execute(
        "SELECT last_inbound_ms FROM conversation_windows WHERE conversation_id=?",
        (conversation_id,)).fetchone()
    existing = row[0] if row else 0
    new_val = max(existing, incoming_provider_ms)
    control_conn.execute(
        "INSERT INTO conversation_windows(conversation_id, last_inbound_ms) VALUES(?,?) "
        "ON CONFLICT(conversation_id) DO UPDATE SET last_inbound_ms=excluded.last_inbound_ms",
        (conversation_id, new_val))
    control_conn.commit()
    return new_val


def rebuild_window_from_evidence(vault_conn, control_conn, conversation_id: str) -> dict:
    """Doctor path: the window MUST equal the exact MAX over window-eligible inbound
    evidence. If the stored projection differs (e.g. a forged future value), repair it
    to the evidence truth and report drift."""
    (evidence_ms,) = vault_conn.execute(
        "SELECT COALESCE(MAX(ts_lower_ms), 0) FROM messages "
        "WHERE conversation_id=? AND direction='in' AND window_eligible=1",
        (conversation_id,)).fetchone()
    stored_row = control_conn.execute(
        "SELECT last_inbound_ms FROM conversation_windows WHERE conversation_id=?",
        (conversation_id,)).fetchone()
    stored_ms = stored_row[0] if stored_row else 0
    drift = stored_ms != evidence_ms
    if drift:
        control_conn.execute(
            "INSERT INTO conversation_windows(conversation_id, last_inbound_ms) VALUES(?,?) "
            "ON CONFLICT(conversation_id) DO UPDATE SET last_inbound_ms=excluded.last_inbound_ms",
            (conversation_id, evidence_ms))
        control_conn.commit()
    return {"evidence_ms": evidence_ms, "stored_ms": stored_ms, "drift": drift}


def check_vault(vault_conn) -> list[dict]:
    findings: list[dict] = []

    bad = 0
    for (mid,) in vault_conn.execute("SELECT id FROM messages"):
        try:
            ids.validate("msg", mid)
        except ids.IdError:
            bad += 1
    findings.append({"check": "message_id_prefix", "ok": bad == 0,
                     "detail": f"{bad} message id(s) fail prefix/ULID validation"})

    ic = vault_conn.execute("PRAGMA integrity_check").fetchone()[0]
    findings.append({"check": "integrity_check", "ok": ic == "ok", "detail": ic})

    fk = vault_conn.execute("PRAGMA foreign_key_check").fetchall()
    findings.append({"check": "foreign_key_check", "ok": len(fk) == 0,
                     "detail": f"{len(fk)} foreign-key violation(s)"})

    cic = vault_conn.execute("PRAGMA cipher_integrity_check").fetchall()
    findings.append({"check": "cipher_integrity_check", "ok": len(cic) == 0,
                     "detail": f"{len(cic)} encrypted-page HMAC failure(s)"})

    return findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_doctor.py -v`
Expected: PASS (all 4).

- [ ] **Step 5: Commit**

```bash
git add src/whatsvault/doctor.py tests/test_doctor.py
git commit -m "feat: doctor rebuilds window from evidence, repairs drift, runs integrity checks"
```

---

### Task 12: Full-suite gate, real secret scan, dependency check

**Files:**
- Create: `tests/test_no_secrets.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `tests/test_no_secrets.py` scanning tracked files for genuine secret patterns with an explicit, reviewable allowlist.

- [ ] **Step 1: Write the secret-scan test**

`tests/test_no_secrets.py`:
```python
import re
import subprocess

# Genuine secret patterns — NOT generic source identifiers.
_PEM = re.compile(rb"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")
_LONG_HEX_ASSIGN = re.compile(rb"""=\s*["'][0-9a-fA-F]{64,}["']""")   # a 32-byte+ key literal
# Files allowed to contain the PRAGMA-key FORMAT STRING (which holds no real key):
_ALLOW = {"src/whatsvault/db/connection.py"}


def _tracked_files():
    out = subprocess.check_output(["git", "ls-files"], text=True)
    return [f for f in out.splitlines() if f]


def test_no_private_keys_or_key_literals_committed():
    offenders = []
    for f in _tracked_files():
        if f.endswith((".db", ".db-wal", ".db-shm")) or f == ".env":
            offenders.append(f + " (secret-bearing file type must not be tracked)")
            continue
        try:
            data = open(f, "rb").read()
        except (IsADirectoryError, FileNotFoundError):
            continue
        if _PEM.search(data):
            offenders.append(f + " (PEM private key)")
        if _LONG_HEX_ASSIGN.search(data) and f not in _ALLOW:
            offenders.append(f + " (long hex key literal)")
    assert offenders == [], f"potential secrets committed: {offenders}"
```

- [ ] **Step 2: Run the secret-scan and the full suite fresh**

Run:
```bash
.venv/bin/pytest -v
.venv/bin/pip check
```
Expected: every test passes; `pip check` reports no broken dependencies. Read the full output; a failure here is the next root-cause task, not something to wave past.

- [ ] **Step 3: Update the changelog**

Append to `CHANGELOG.md` under Unreleased:
```markdown
- Raouf: Phase 1a vault core complete (rev 2) — deterministic SQLCipher source build + locked deps, prefixed ULIDs (full entity registry), validated interval time model, provision/require keystore + versioned attachment AEAD, eager-validated connections with at-rest encryption proof, numbered transactional migrations, vault schema with explicit window_eligible + checks, deny-by-default evidence immutability, domain-tagged dedupe, status lattice, control schema aligned to the signed byte contract, and a doctor that rebuilds the window from evidence. Real secret scan + pip check gate. All tests green.
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_no_secrets.py CHANGELOG.md
git commit -m "test: secret scan + dependency gate; close out Phase 1a (rev 2)"
```

---

## Self-Review

**1. Spec coverage (§2.4, §3):**
- §2.4 SQLCipher keys (provision/require, eager validation) → Tasks 4,5; attachment blobs at rest (versioned AEAD) → Task 4; at-rest proof → Task 5; no fake pragmas → Task 5; secret hygiene → Task 12. ✓
- §3.2 prefixed ULIDs (full entity registry incl. acc/src/rev/aud) → Task 2. ✓
- §3.3 interval time (validated) + DST → Task 3. ✓
- §3.4 vault schema + uniqueness + CHECK constraints + explicit `window_eligible` → Task 6. ✓
- §3.5 control schema + BLOB nonce/hash contract + P7 fields + same-DB FKs + state CHECKs + draft-freeze + append-only audit → Task 10. ✓
- §3.6 **deny-by-default** evidence immutability (all evidence fields, ingest/revisions/status) → Tasks 6 (triggers) + 7 (proof). ✓
- §3.7 status lattice (+ unknown-status surfacing) → Task 9; I5 window as `window_eligible`, projection in `control.db`, doctor rebuilds-from-evidence and repairs drift → Tasks 6,10,11. ✓
- §3.8 domain-tagged family-specific dedupe → Task 8. ✓
- §4.3 capability gate (fts5/trigram/fts_secure_delete/core_secure_delete/foreign_keys/cipher_version) → Task 1. ✓
- Numbered transactional migrations → Task 6 (runner) + Tasks 6/10 (lanes). ✓
- **Deferred (out of Phase 1a, named):** search index/normaliser (1c), importer (1b), sealed-envelope decrypt (3), draft/approval flow + I2/I3/I4 + canonical signing (4).

**2. Placeholder scan:** no TBD/TODO; every code step carries runnable code.

**3. Type consistency:** `open_db`/`provision_db`/`open_existing`, `migrate(conn, lane)`, `KeyStore.provision/require`, `seal_blob/open_blob` signatures consistent across Tasks 4→5→6→10→11; `reduce_status` shape stable; doctor `advance_window` vs `rebuild_window_from_evidence` distinct and matched to tests.

**Empirical grounding (receipts from the gauntlet, not assumptions):** `sqlcipher3` source build works against brew SQLCipher (`connect`/`Row`/`IntegrityError`/`DatabaseError` top-level; `cipher_version`/`cipher_provider`/`cipher_integrity_check` present); `RAISE(ABORT)`→`IntegrityError`; `PRAGMA key` is lazy so `open_db` validates eagerly; `cipher_secure_delete` is a silent no-op and is banned; core `secure_delete` reads back `1`. `sqlcipher3-binary` has no wheel for this target (macOS arm64) — wheels exist for other platforms, so the constraint is platform-specific, not absolute.
