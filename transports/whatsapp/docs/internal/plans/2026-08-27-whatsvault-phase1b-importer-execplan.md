# WhatsVault Phase 1b — Export Importer (STANDALONE EXECUTION PLAN)

> **Generated just-in-time** from the design spec (§8, §3.9), the master roadmap, the **Corrections Ledger #25–#31 (INV-IMPORT cluster)**, and the **actual Phase 1a repository state** (schema/APIs read 2026-08-27, commit `ece2809`). This is the execution-safe plan referenced by the master's Execution rule; it supersedes the roadmap's Phase 1b reference draft.

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline) or subagent-driven-development. `- [ ]` steps. TDD iron law: failing test → watch it fail for the right reason → minimal code → watch it pass → commit.

**Goal:** Import WhatsApp TXT/ZIP chat exports into `vault.db` as evidence, refusing to guess anything the file cannot justify (dates, timezone, **self/direction**, message boundaries), with batch-scoped undo and forensic reparse — never touching `control.db` or a messaging window.

**Spec:** `docs/internal/specs/2026-08-27-whatsvault-design.md` §8, §3.9.

## Bound to actual Phase 1a state (verified this session)

- `messages` columns exist as: `direction NOT NULL CHECK IN ('in','out')`, `sender_contact_id TEXT REFERENCES contacts(id)` (nullable, **frozen** by `trg_messages_evidence_immutable`), `origin CHECK IN ('cloud_api','business_app_echo','history_sync','manual_export')`, `window_eligible`, `import_fingerprint` with `ux_messages_import_fp UNIQUE`, uncertainty-interval columns.
- `conversation_sources` already exists with `source_kind`, `import_batch_id`.
- Migration runner: `whatsvault.db.migrations.migrate(conn, "vault")` runs numbered assets in `MIGRATIONS["vault"]`; each in a `BEGIN…COMMIT` bumping `user_version`. Register `(2, "vault/0002_import_provenance.sql")`.
- `whatsvault.ids`: `new_id(prefix)`/`validate` gated by `PREFIXES`; **has `bat`, has NO import-participant prefix** → this plan adds `imp`.
- `whatsvault.timemodel`: `Interval`, `from_local_minute(epoch_minute_start_ms)`, `from_provider_seconds`, `classify_local(zone, local_dt) -> DstClass{UNAMBIGUOUS,FOLD,NONEXISTENT}`.
- `whatsvault.crypto.atrest`: `seal_blob(key, plaintext, key_id=0, aad=b"")` / `open_blob(key, sealed, aad=b"")`.
- Tests open a vault with `connection.open_db(path, os.urandom(32))` then `migrations.migrate(conn,"vault")`. No conftest; each test file builds its own vault.

## Ledger corrections folded in (binding)

- **#25** `self_participant` is **mandatory** (direction is NOT NULL in/out). Stored as `self_participant_label` on the batch (an operator-declared "me" label, not an FK — avoids a circular batch↔participant FK). Absent → `ImportRefused`. Never assume "You".
- **#26** Imported messages link to their provisional sender via `message_import_observations.sender_import_participant_id` (participants are never auto-linked to `contacts`; `sender_contact_id` stays NULL for imports).
- **#27** `import_batch(..., dst_resolutions=...)` — resolutions keyed by a stable **source location** (`source_ordinal`). No global fold switch; unresolved fold/nonexistent → refuse.
- **#28** Ambiguous boundaries: default rule is "a line matching the header regex starts a new message"; `dry_run` additionally surfaces header-like lines whose date is implausibly outside the transcript span as `ambiguous_boundaries` (offsets) for operator review. Named residual: plaintext exports are information-theoretically ambiguous here — surfaced, never forged.
- **#29** New provenance tables get immutability triggers with **sanctioned** mutation paths only: batches freeze identity/declared fields (soft `undone_at_ms` set once at undo); participants allow only `link_state`/`linked_contact_id` transitions; observations are write-once (undo deletes, never updates).
- **#30** Atomic source-artefact contract: seal → fsync/atomic-rename → verify hash **before** the import transaction; the batch row is written only with an already-durable `source_artifact_path`+`source_artifact_sha256`; artefact AAD binds `batch_id‖source_sha256`. Orphan artefacts (txn aborted) are harmless ciphertext, cleanable.
- **#31** Hostile-ZIP matrix expanded (zero-size, ZIP64, encrypted, backslash/Windows-absolute traversal, symlink, declared-size lie, streaming byte cap, perms, `0700` temp dir). The word "MIME-sniff" is dropped — the guard classifies by extension only and says so (no false sniffing claim).

---

### Task 1: `imp` id prefix + import-provenance schema (vault migration 0002)

**Files:** Modify `src/whatsvault/ids.py` (add `imp`); Create `src/whatsvault/db/migrations/vault/0002_import_provenance.sql`; Modify `src/whatsvault/db/migrations/__init__.py`; Test `tests/test_schema_import.py`.

**Interfaces produced:** `migrate(conn,"vault")` reaches version 2, creating `import_batches`, `import_participants`, `message_import_observations` + their immutability triggers; `ids.new_id("imp")` valid.

- [ ] **Step 1 — failing test** `tests/test_schema_import.py`:
```python
import os
import pytest
import sqlcipher3
from whatsvault import ids
from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _vault(tmp_path):
    conn = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(conn, "vault")
    return conn


def test_reaches_version_2(tmp_path):
    assert M.user_version(_vault(tmp_path)) >= 2


def test_imp_prefix_registered():
    assert ids.new_id("imp").startswith("imp_")


def _seed_batch(conn, bat="bat_1"):
    conn.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn')")
    conn.execute("INSERT INTO conversations(id, account_id, type) VALUES('cnv','acc','dm')")
    conn.execute(
        "INSERT INTO import_batches(id, source_kind, source_sha256, declared_date_format, "
        "declared_timezone, self_participant_label) VALUES(?, 'manual_export','sha','DMY','UTC','You')",
        (bat,),
    )


def _msg(conn, mid, body):
    conn.execute(
        "INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, "
        "ts_upper_ms_exclusive, ts_precision, type, text_original, origin, window_eligible) "
        "VALUES(?, 'acc','cnv','in',1,60001,'min','text',?,'manual_export',0)",
        (mid, body),
    )


def test_observation_unique_per_batch_ordinal(tmp_path):
    conn = _vault(tmp_path); _seed_batch(conn); _msg(conn, "msg_1", "hi"); _msg(conn, "msg_2", "yo")
    ins = ("INSERT INTO message_import_observations(batch_id, message_id, source_ordinal, "
           "source_start_offset, source_end_offset, source_fingerprint) VALUES('bat_1',?,?,0,10,'fp')")
    conn.execute(ins, ("msg_1", 1))
    with pytest.raises(sqlcipher3.IntegrityError):   # DIFFERENT message, SAME ordinal -> UNIQUE(batch,ordinal)
        conn.execute(ins, ("msg_2", 1))


def test_participant_link_state_constrained(tmp_path):
    conn = _vault(tmp_path); _seed_batch(conn)
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("INSERT INTO import_participants(id, import_batch_id, raw_display_name, link_state) "
                     "VALUES('imp_1','bat_1','Mona','WHATEVER')")


def test_batch_declared_fields_immutable(tmp_path):
    conn = _vault(tmp_path); _seed_batch(conn)
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("UPDATE import_batches SET declared_date_format='MDY' WHERE id='bat_1'")


def test_observation_is_write_once(tmp_path):
    conn = _vault(tmp_path); _seed_batch(conn); _msg(conn, "msg_1", "hi")
    conn.execute("INSERT INTO message_import_observations(batch_id, message_id, source_ordinal) "
                 "VALUES('bat_1','msg_1',0)")
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("UPDATE message_import_observations SET source_ordinal=5 "
                     "WHERE batch_id='bat_1' AND message_id='msg_1'")
    conn.execute("DELETE FROM message_import_observations WHERE batch_id='bat_1'")  # undo path allowed


def test_participant_link_state_may_transition(tmp_path):
    conn = _vault(tmp_path); _seed_batch(conn)
    conn.execute("INSERT INTO contacts(id, display_name) VALUES('cnt_x','Mona')")
    conn.execute("INSERT INTO import_participants(id, import_batch_id, raw_display_name) "
                 "VALUES('imp_1','bat_1','Mona')")
    conn.execute("UPDATE import_participants SET link_state='LINKED_EXPLICIT', linked_contact_id='cnt_x' "
                 "WHERE id='imp_1'")  # sanctioned
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("UPDATE import_participants SET raw_display_name='Forged' WHERE id='imp_1'")
```

- [ ] **Step 2 — run, expect FAIL** (`user_version` 1; `imp` unknown): `.venv/bin/pytest tests/test_schema_import.py -q`
- [ ] **Step 3 — implement.** Add `"imp",  # import_participants` to `ids.PREFIXES`. Write `0002_import_provenance.sql`:
```sql
CREATE TABLE import_batches (
    id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('manual_export')),
    source_sha256 TEXT NOT NULL,
    source_artifact_path TEXT,
    source_artifact_sha256 TEXT,
    original_filename TEXT,
    declared_date_format TEXT NOT NULL CHECK (declared_date_format IN ('DMY','MDY','YMD')),
    declared_timezone TEXT NOT NULL,
    self_participant_label TEXT,
    parser_family TEXT,
    parser_version INTEGER,
    fingerprint_version INTEGER NOT NULL DEFAULT 1,
    imported_at_ms INTEGER,
    message_count INTEGER,
    system_event_count INTEGER,
    undone_at_ms INTEGER
);

CREATE TABLE import_participants (
    id TEXT PRIMARY KEY,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_conversation_id TEXT,
    raw_display_name TEXT NOT NULL,
    normalised_display_name TEXT,
    role TEXT,
    linked_contact_id TEXT REFERENCES contacts(id),
    link_state TEXT NOT NULL DEFAULT 'UNLINKED'
        CHECK (link_state IN ('UNLINKED','LINKED_EXPLICIT','LINK_REVOKED'))
);

CREATE TABLE message_import_observations (
    batch_id TEXT NOT NULL REFERENCES import_batches(id),
    message_id TEXT NOT NULL REFERENCES messages(id),
    sender_import_participant_id TEXT REFERENCES import_participants(id),
    source_ordinal INTEGER NOT NULL,
    source_start_offset INTEGER,
    source_end_offset INTEGER,
    source_fingerprint TEXT,
    fingerprint_version INTEGER NOT NULL DEFAULT 1,
    parser_version INTEGER,
    PRIMARY KEY (batch_id, message_id),
    UNIQUE (batch_id, source_ordinal)
);
CREATE INDEX ix_observations_message ON message_import_observations(message_id);

-- #29 immutability with sanctioned mutation paths
CREATE TRIGGER trg_import_batches_immutable
BEFORE UPDATE OF id, source_kind, source_sha256, source_artifact_path, source_artifact_sha256,
                 original_filename, declared_date_format, declared_timezone, self_participant_label
ON import_batches
BEGIN SELECT RAISE(ABORT, 'import_batches identity/declared/artefact fields are immutable'); END;

CREATE TRIGGER trg_import_participants_identity_immutable
BEFORE UPDATE OF id, import_batch_id, raw_display_name, normalised_display_name
ON import_participants
BEGIN SELECT RAISE(ABORT, 'import_participant provisional identity is immutable (only link_state/linked_contact_id change)'); END;

CREATE TRIGGER trg_import_observations_immutable
BEFORE UPDATE ON message_import_observations
BEGIN SELECT RAISE(ABORT, 'import observations are write-once (undo deletes, never updates)'); END;
```
Register `(2, "vault/0002_import_provenance.sql")` in `MIGRATIONS["vault"]`.

- [ ] **Step 4 — run, expect PASS.** **Step 5 — commit** `feat: import-provenance schema + imp id prefix (ledger #25,#26,#29)`.

---

### Task 2: Locale-parameterised header grammar + whole-file date validation

**Files:** Create `src/whatsvault/importers/__init__.py`, `src/whatsvault/importers/grammar.py`; Test `tests/test_import_grammar.py`.

**Interfaces produced:**
- `grammar.build_header_regex(date_format: str) -> re.Pattern` — named groups `date`, `time`, `sender`; tolerant of `[ap]m`/24h, `-`/`/`/`.` separators, 2/4-digit years, the ` - ` and `] ` bracket forms.
- `grammar.validate_family(text, date_format, tz_name) -> dict` → `{"ok": bool, "header_count": int, "first_bad_line": int|None}`; `ok=False` if any header's date is out of range under the family (day>12 disproves a family that reads it as a month).
- `grammar.suggest_families(text) -> list[str]` — families whose `validate_family().ok`.

- [ ] **Step 1 — failing test** (DMY vs MDY disambiguation + extraction), per the design corpus (day>12 rejects MDY; all-days-≤12 → ambiguous {DMY,MDY}; sender/body extraction).
- [ ] **Step 2 — FAIL. Step 3 — implement** `grammar.py`. **Step 4 — PASS. Step 5 — commit** `feat: export header grammar + whole-file date validation`.

---

### Task 3: Multiline assembly + system/media classification + boundary surfacing (#28)

**Files:** Create `src/whatsvault/importers/parse.py`; Test `tests/test_import_parse.py`.

**Interfaces produced:**
- `parse.parse_transcript(text, date_format, tz_name) -> list[dict]` — tagged dicts `{"kind": "message"|"system"|"system_unknown"|"ambiguous_boundary", "sender", "body", "source_ordinal", "source_start_offset", "source_end_offset", "local_minute_epoch_ms", "dst_class", "media_state"?}`. A line is a continuation unless it matches the header regex; system lines classified by a locale-keyed `KNOWN_SYSTEM_PATTERNS` table (never participant-attributed); media placeholders → the four `attachments.retrieval_state` values; header-like lines with an implausible date span → `ambiguous_boundary` with offsets (#28), never a fabricated message.

- [ ] **Step 1 — failing test** — multiline body assembles; `<Media omitted>` → `MEDIA_PLACEHOLDER`; an E2E-encryption system line → `system`; a header-like line with an out-of-span date → `ambiguous_boundary` (never a new participant message). **Step 2 — FAIL. Step 3 — implement. Step 4 — PASS. Step 5 — commit** `feat: multiline assembly + conservative system/media/boundary classification (#28)`.

---

### Task 4: Evidence fingerprints (original content, no normalisation)

**Files:** Create `src/whatsvault/importers/fingerprint.py`; Test `tests/test_import_fingerprint.py`.

**Interfaces produced:**
- `fingerprint.content_fingerprint(message_type, original_text) -> str` — SHA-256 over length-prefixed **original** bytes (exact Unicode, no NFC/Yeh folding).
- `fingerprint.import_fingerprint(fingerprint_version, conversation_key, ts_bucket, sender_key, message_type, content_fp, occurrence_index) -> str`.

- [ ] **Step 1 — failing test** — identical "ok" twice in one minute → different import fingerprints via `occurrence_index` 0/1; Yeh-variant bodies → **different** `content_fingerprint` (evidence preserves the distinction). **Step 2 — FAIL. Step 3 — implement. Step 4 — PASS. Step 5 — commit** `feat: evidence-based content/import fingerprints`.

---

### Task 5: Writer + dry-run with refuse-don't-guess gates (#25, #27, #30)

**Files:** Create `src/whatsvault/importers/whatsapp_export.py`; Test `tests/test_import_writer.py`.

**Interfaces produced:**
- `whatsapp_export.dry_run(text, date_format, tz_name, *, self_participant_label) -> dict` — `{"would_add", "ambiguous_dates", "dst_cases":[{source_ordinal,dst_class}], "unlinked_participants", "system_lines", "ambiguous_boundaries", "self_resolved": bool}`. Never writes.
- `whatsapp_export.import_batch(vault_conn, text, *, source_sha256, date_format, tz_name, conversation_id, account_id, self_participant_label, dst_resolutions=None, source_artifact=None) -> dict` — refuses (`raise ImportRefused`) if `date_format`/`tz_name`/`self_participant_label` missing, if `suggest_families` disagrees with the declared family, or if any `dst_cases` lack a matching `dst_resolutions[source_ordinal]`. On success (single transaction): batch (with already-durable artefact fields if `source_artifact` given, #30), participants (`imp_`, UNLINKED), messages (`origin='manual_export'`, `window_eligible=0`, **direction** = 'out' iff sender matches `self_participant_label` else 'in', #25), observations (with `sender_import_participant_id`, #26); dedupe messages by `import_fingerprint`.
- `ImportRefused(Exception)` with a machine code (`MISSING_SELF`, `MISSING_TZ`, `FORMAT_MISMATCH`, `DST_UNRESOLVED`).

- [ ] **Step 1 — failing test** — (a) missing `self_participant_label` → `ImportRefused(MISSING_SELF)`; (b) missing `tz_name` → refused; (c) declared `MDY` on DMY-only file → refused; (d) unresolved DST fold → `ImportRefused(DST_UNRESOLVED)`, and the same import with a matching `dst_resolutions` succeeds; (e) a clean DMY import writes N messages all `window_eligible=0`/`origin='manual_export'`, direction set from `self_participant_label`, one observation per message with `sender_import_participant_id` set; (f) re-import same text adds 0 messages (fingerprint dedupe) but records the second batch's observations. **Step 2 — FAIL. Step 3 — implement. Step 4 — PASS. Step 5 — commit** `feat: importer with refuse-don't-guess gates, self/direction, DST resolutions (#25,#27)`.

---

### Task 6: Source-artefact atomic store + batch undo + reparse (#29, #30)

**Files:** Modify `src/whatsvault/importers/whatsapp_export.py`; Test `tests/test_import_undo.py`.

**Interfaces produced:**
- `whatsapp_export.store_source_artifact(dest_dir, batch_id, raw_bytes, key) -> tuple[str, str]` — seal (`atrest.seal_blob`, `aad=batch_id.encode()+b"|"+source_sha256`), write to a temp file, `os.fsync`, atomic `os.replace` to the final path, re-open and verify the sealed hash; return `(path, sealed_sha256)`. Raises before any DB write if the artefact didn't land.
- `whatsapp_export.undo_batch(vault_conn, batch_id) -> dict` — deletes the batch's observations; deletes a canonical message only if it now has **zero** observations and `wamid IS NULL`; sets `import_batches.undone_at_ms`; never deletes the artefact. Returns counts.
- `whatsapp_export.reparse(vault_conn, batch_id, key, dest_dir) -> dict` — `open_blob` the stored artefact, re-run `dry_run`, return it.

- [ ] **Step 1 — failing test** — store→verify roundtrip; two overlapping batches observe one message: `undo_batch(A)` keeps the shared message (B observes it), `undo_batch(B)` then deletes it (no observers, no wamid); `undo` sets `undone_at_ms`; `reparse` reproduces the original `would_add`. **Step 2 — FAIL. Step 3 — implement. Step 4 — PASS. Step 5 — commit** `feat: atomic source-artefact store + observation-aware undo + reparse (#29,#30)`.

---

### Task 7: Hostile-ZIP guard (#31)

**Files:** Create `src/whatsvault/importers/zip_guard.py`; Test `tests/test_import_zip.py`.

**Interfaces produced:**
- `zip_guard.safe_extract(zip_path, dest_dir, *, max_files, max_total_bytes, max_ratio, max_file_bytes) -> list[str]` — rejects path traversal (`../`, backslash, absolute POSIX/Windows), symlinks (external_attr), zero-entry, over-count, over-ratio (bomb), per-file over-size; enforces a **streaming** expanded-byte cap (reads decompressed bytes, not the declared size); creates `dest_dir` mode `0700`; classifies files by **extension only** (documented — no MIME sniffing). Raises `HostileZip(code)`.
- `zip_guard.find_transcript(names) -> str` — the single `.txt`; raises `AmbiguousTranscript` if 0 or >1 (never "largest wins").

- [ ] **Step 1 — failing test** — `../escape.txt`, a backslash/Windows-absolute entry, a symlink entry, a declared-size-lie whose streamed bytes exceed `max_total_bytes`, and a two-`.txt` zip each raise; a normal one-transcript zip extracts and `find_transcript` returns it; `dest_dir` is `0700`. **Step 2 — FAIL. Step 3 — implement. Step 4 — PASS. Step 5 — commit** `feat: hostile-ZIP guard (traversal/bomb/symlink/streaming-cap; extension-only) (#31)`.

---

### Task 8: Full-suite gate + CHANGELOG

- [ ] `.venv/bin/pytest -q` (Phase 1a + 1b green). Append a `Raouf:` CHANGELOG line. Commit `test: close out Phase 1b importer (ledger #25-#31)`.

## Self-review (consistency + ambiguity gate — required before execution)

Run before Task 1. Findings recorded inline below.

- **SR-1 (adopted into Task 5):** the writer also inserts a `conversation_sources(id=src_…, conversation_id, source_kind='manual_export', import_batch_id=bat, write_capable=0)` row — imports are a source of the conversation (spec §8), never write-capable.
- **SR-2 (adopted into Tasks 3/5):** `parse_transcript` emits naive local wall-clock components + `dst_class`; the **writer** computes the UTC epoch only after applying `dst_resolutions` (fold selection). No epoch is assigned to an unresolved FOLD/NONEXISTENT instant.
- **SR-3 (noted residual):** a message deduped across batches keeps the direction assigned by the first batch's `self_participant_label`; a later batch with a different self designation does not re-flip it (fingerprint binds sender, not direction). Acceptable for V1; documented.
- **Consistency checks passed:** no circular batch↔participant FK (self is a label, not an FK); immutability triggers leave the sanctioned paths open (batch `undone_at_ms`; participant `link_state`/`linked_contact_id`; observation DELETE); all `messages` NOT-NULL columns covered by the writer; `RAISE(ABORT)`→`sqlcipher3.IntegrityError` (Phase 1a fact).

**Gate: PASS** — plan is execution-safe. Begin Task 1.
