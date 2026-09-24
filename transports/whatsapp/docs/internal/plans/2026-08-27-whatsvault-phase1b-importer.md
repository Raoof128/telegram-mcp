# WhatsVault Phase 1b — Export Importer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]` checkboxes.

**Goal:** Import WhatsApp TXT/ZIP chat exports into `vault.db` as evidence, refusing to guess anything the file cannot justify (dates, timezone, identity, message boundaries), with batch-scoped undo and forensic reparse.

**Architecture:** A pure parser (`whatsapp_export`) turns raw export text into structured, provisional records; a writer commits them into `vault.db` via a new migration (`0002`) adding import-provenance tables. Batches *observe* evidence (many-to-many), so undo removes observations, not evidence still supported by another source. The importer writes only `vault.db` — never `control.db`, never a messaging window.

**Tech Stack:** Python ≥3.11, stdlib `zipfile`/`re`/`zoneinfo`, existing `whatsvault.{ids,timemodel,db}`. Builds on Phase 1a.

**Spec:** `docs/internal/specs/2026-08-27-whatsvault-design.md` §8, §3.9.

## Global Constraints (verbatim / distilled from spec §8)

- **Refuse, don't guess.** Import requires explicit `date_format` (`DMY`/`MDY`/`YMD`) and `tz_name`; refuse otherwise. Detection is **whole-file validation** (parse the entire transcript with each candidate family, reject families producing invalid dates); 0 candidates → `UNSUPPORTED_FORMAT`, 1 → suggest, >1 → `AMBIGUOUS`. Suggestion never auto-selects.
- **Imports are never window-eligible.** Every imported message row is written with `window_eligible = 0` and `origin = 'manual_export'`. An import can never mutate `control.db` or reopen the 24h window (INV-IMPORT).
- **Provisional identity.** Imported senders land in `import_participants` (`link_state = 'UNLINKED'`), never auto-linked to a real contact. Linking is an explicit, logged, reversible operator action. No fuzzy matching.
- **Evidence identity uses ORIGINAL content**, never search-normalised text (§3.9). `content_fingerprint = SHA256(canonical parsed original content)`; `occurrence_index` is the ordinal within the equivalence bucket.
- **Many-to-many provenance.** `message_import_observations(batch_id, message_id, ...)`; undo deletes a batch's observations and deletes a canonical message only if no import observation and no provider provenance remain.
- **Conservative parsing.** Ambiguous message boundaries → `AMBIGUOUS_MESSAGE_BOUNDARY` (preserve offsets, flag in dry-run, never fabricate a participant message). System lines classified only by known locale rules → `SYSTEM_EVENT`/`SYSTEM_EVENT_UNKNOWN`, never participant attribution. Media placeholders → the four states (`MEDIA_PLACEHOLDER`/`FILE_PRESENT`/`FILE_NOT_INCLUDED_IN_EXPORT`/`FILE_REFERENCE_BROKEN`).
- **Encoding.** Strict `UTF-8` / `UTF-8+BOM`; never `errors="replace"` (`�` silently changes evidence). Decode failure → `ENCODING_UNSUPPORTED`.
- **DST.** Fold/nonexistent local instants (via `timemodel.classify_local`) require explicit operator resolution; never silently `fold=0`.
- **Hostile ZIP.** Reject path traversal, symlinks; cap expanded size, file count, compression ratio, per-file size; sanitise filenames; MIME-sniff; never execute; multiple transcript `.txt` → refuse and ask which.
- **Source artefact retained** encrypted for forensic reparse; undo never deletes it unless the operator explicitly asks.

---

### Task 1: Import-provenance schema (vault migration 0002)

**Files:**
- Create: `src/whatsvault/db/migrations/vault/0002_import_provenance.sql`
- Modify: `src/whatsvault/db/migrations/__init__.py` (register `(2, "vault/0002_import_provenance.sql")`)
- Test: `tests/test_schema_import.py`

**Interfaces:** `migrate(conn, "vault")` now reaches version 2, creating `import_batches`, `import_participants`, `message_import_observations`.

- [ ] **Step 1: Write the failing test** — `tests/test_schema_import.py`:
```python
import os
import pytest
import sqlcipher3
from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _vault(tmp_path):
    conn = C.open_db(str(tmp_path / "v.db"), os.urandom(32)); M.migrate(conn, "vault")
    return conn


def test_reaches_version_2(tmp_path):
    conn = _vault(tmp_path)
    assert M.user_version(conn) >= 2


def test_observation_unique_per_batch_ordinal(tmp_path):
    conn = _vault(tmp_path)
    conn.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn')")
    conn.execute("INSERT INTO conversations(id, account_id, type) VALUES('cnv','acc','dm')")
    conn.execute("INSERT INTO import_batches(id, source_kind, source_sha256, declared_date_format, declared_timezone) "
                 "VALUES('bat_1','manual_export','sha','DMY','Australia/Sydney')")
    conn.execute("INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, ts_upper_ms_exclusive, "
                 "ts_precision, type, text_original, origin, window_eligible) "
                 "VALUES('msg_1','acc','cnv','in',1,60001,'min','text','hi','manual_export',0)")
    conn.execute("INSERT INTO messages(id, account_id, conversation_id, direction, ts_lower_ms, ts_upper_ms_exclusive, "
                 "ts_precision, type, text_original, origin, window_eligible) "
                 "VALUES('msg_2','acc','cnv','in',1,60001,'min','text','yo','manual_export',0)")
    ins = ("INSERT INTO message_import_observations(batch_id, message_id, source_ordinal, source_start_offset, "
           "source_end_offset, source_fingerprint) VALUES('bat_1',?,?,0,10,'fp')")
    conn.execute(ins, ("msg_1", 1))
    # DIFFERENT message, SAME ordinal -> must hit UNIQUE(batch_id, source_ordinal), not the PK
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute(ins, ("msg_2", 1))


def test_participant_link_state_constrained(tmp_path):
    conn = _vault(tmp_path)
    conn.execute("INSERT INTO import_batches(id, source_kind, source_sha256, declared_date_format, declared_timezone) "
                 "VALUES('bat_1','manual_export','sha','DMY','UTC')")
    with pytest.raises(sqlcipher3.IntegrityError):
        conn.execute("INSERT INTO import_participants(id, import_batch_id, raw_display_name, link_state) "
                     "VALUES('src_1','bat_1','Mona','WHATEVER')")
```

- [ ] **Step 2: Run — expect FAIL** (`user_version` still 1). `.venv/bin/pytest tests/test_schema_import.py -q`

- [ ] **Step 3: Write** `0002_import_provenance.sql`:
```sql
CREATE TABLE import_batches (
    id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('manual_export')),
    source_sha256 TEXT NOT NULL,
    source_artifact_path TEXT,
    original_filename TEXT,
    declared_date_format TEXT NOT NULL CHECK (declared_date_format IN ('DMY','MDY','YMD')),
    declared_timezone TEXT NOT NULL,
    parser_family TEXT,
    parser_version INTEGER,
    fingerprint_version INTEGER NOT NULL DEFAULT 1,
    imported_at_ms INTEGER,
    message_count INTEGER,
    system_event_count INTEGER
);

CREATE TABLE import_participants (
    id TEXT PRIMARY KEY,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(id),
    source_conversation_id TEXT,
    raw_display_name TEXT NOT NULL,
    normalised_display_name TEXT,
    role TEXT,
    linked_contact_id TEXT REFERENCES contacts(id),
    link_state TEXT NOT NULL DEFAULT 'UNLINKED' CHECK (link_state IN ('UNLINKED','LINKED_EXPLICIT','LINK_REVOKED'))
);

CREATE TABLE message_import_observations (
    batch_id TEXT NOT NULL REFERENCES import_batches(id),
    message_id TEXT NOT NULL REFERENCES messages(id),
    source_ordinal INTEGER NOT NULL,
    source_start_offset INTEGER,
    source_end_offset INTEGER,
    source_fingerprint TEXT,
    fingerprint_version INTEGER NOT NULL DEFAULT 1,
    parser_version INTEGER,
    PRIMARY KEY (batch_id, message_id),
    UNIQUE (batch_id, source_ordinal)
);
```
Register in `migrations/__init__.py`: `"vault": [(1, "vault/0001_initial.sql"), (2, "vault/0002_import_provenance.sql")]`.

- [ ] **Step 4: Run — expect PASS.** **Step 5: Commit** `feat: import-provenance schema (batches, participants, observations)`.

---

### Task 2: Export line grammar + whole-file date-format validation

The parser core: given raw text + a declared `date_format`, build a locale-parameterised header regex and validate that the **entire** transcript parses with that family (all dates valid). Detection *assists* (suggests) but the caller must supply the format.

**Files:**
- Create: `src/whatsvault/importers/__init__.py`, `src/whatsvault/importers/grammar.py`
- Test: `tests/test_import_grammar.py`

**Interfaces:**
- `grammar.HeaderMatch` dataclass: `(date_str, time_str, sender, body_start_index)`.
- `grammar.build_header_regex(date_format: str) -> re.Pattern` — compiles a header matcher for the family.
- `grammar.validate_family(text: str, date_format: str, tz_name: str) -> dict` — returns `{"ok": bool, "header_count": int, "first_bad_line": int | None}`; `ok=False` if any header line yields an out-of-range date under this family.
- `grammar.suggest_families(text: str) -> list[str]` — families that fully validate; caller decides.

- [ ] **Step 1: Write the failing test** — covering DMY vs MDY disambiguation and refusal:
```python
import pytest
from whatsvault.importers import grammar as G

DMY = "13/04/2026, 5:32 pm - Mona: hi there\n14/04/2026, 6:01 pm - You: hello\n"
AMBIG = "03/04/2026, 5:32 pm - Mona: hi\n05/04/2026, 6:01 pm - You: yo\n"  # all days<=12 -> both DMY/MDY valid


def test_dmy_validates_and_mdy_rejects_day_over_12():
    assert G.validate_family(DMY, "DMY", "UTC")["ok"] is True
    assert G.validate_family(DMY, "MDY", "UTC")["ok"] is False   # 13 is not a month


def test_suggest_returns_single_when_unambiguous():
    assert G.suggest_families(DMY) == ["DMY"]


def test_suggest_returns_multiple_when_ambiguous():
    assert set(G.suggest_families(AMBIG)) == {"DMY", "MDY"}


def test_header_match_extracts_sender_and_body():
    pat = G.build_header_regex("DMY")
    m = pat.match("13/04/2026, 5:32 pm - Mona: hi there")
    assert m and m.group("sender") == "Mona"
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Write** `grammar.py` (implement `build_header_regex` for the three families with tolerant separators/AM-PM/year-width; `validate_family` parses each header's date under the family and range-checks month/day; `suggest_families` returns the families whose `validate_family().ok`). Key rule: a day value > 12 disproves the family that would read it as a month.

- [ ] **Step 4: Run — expect PASS.** **Step 5: Commit** `feat: locale-parameterised export header grammar with whole-file validation`.

---

### Task 3: Multiline assembly, system-line & media classification

**Files:** Create `src/whatsvault/importers/parse.py`; Test `tests/test_import_parse.py`.

**Interfaces:**
- `parse.ParsedLine` variants via a tagged dict: `{"kind": "message"|"system"|"system_unknown"|"ambiguous_boundary", ...}`.
- `parse.parse_transcript(text, date_format, tz_name) -> list[dict]` — assembles multiline bodies (a line is a continuation unless it matches the header regex), classifies system lines by a known-rules table, detects media placeholders, and flags ambiguous boundaries with `source_start_offset`/`source_end_offset`.

- [ ] **Step 1: Failing test** — multiline body, `<Media omitted>` → `MEDIA_PLACEHOLDER`, a system line ("Messages and calls are end-to-end encrypted") → `system`, and a body line that *looks* like a header inside a message → `ambiguous_boundary` (never a new participant message).
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Write** `parse.py` (stateful line walk using `grammar.build_header_regex`; a `KNOWN_SYSTEM_PATTERNS` table keyed by locale; media markers table). System classification never uses English-only heuristics as the sole rule; undecidable senderless lines → `system_unknown`.
- [ ] **Step 4: Run — expect PASS.** **Step 5: Commit** `feat: multiline assembly with conservative system/media/boundary classification`.

---

### Task 4: Evidence fingerprints (content + import)

**Files:** Create `src/whatsvault/importers/fingerprint.py`; Test `tests/test_import_fingerprint.py`.

**Interfaces:**
- `fingerprint.content_fingerprint(message_type, original_text) -> str` — SHA-256 over canonical **original** content (exact Unicode, UTF-8, no normalisation).
- `fingerprint.import_fingerprint(fingerprint_version, conversation_key, ts_bucket, sender_key, message_type, content_fp, occurrence_index) -> str`.

- [ ] **Step 1: Failing test** — identical "ok" twice in the same minute produce *different* import fingerprints (via `occurrence_index` 0 and 1); `text_normalised`-style folding (e.g. Yeh variants) must NOT change `content_fingerprint` (feed both forms, assert different fingerprints — evidence identity preserves the distinction).
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Write** `fingerprint.py` (length-prefixed SHA-256, no normalisation).
- [ ] **Step 4: Run — expect PASS.** **Step 5: Commit** `feat: evidence-based content and import fingerprints`.

---

### Task 5: Importer write path + dry-run (refuse-don't-guess)

**Files:** Create `src/whatsvault/importers/whatsapp_export.py`; Test `tests/test_import_writer.py`.

**Interfaces:**
- `whatsapp_export.dry_run(text, date_format, tz_name) -> dict` — returns `{"would_add": int, "ambiguous_dates": [...], "dst_cases": [...], "unlinked_participants": [...], "system_lines": int, "ambiguous_boundaries": [...]}`. Never writes.
- `whatsapp_export.import_batch(vault_conn, text, *, source_sha256, date_format, tz_name, conversation_id, account_id) -> dict` — refuses (`raise ImportRefused`) if `date_format`/`tz_name` missing, if `suggest_families` disagrees with the declared family, or if a DST fold/nonexistent case is present and unresolved. On success: writes `import_batches`, `import_participants` (UNLINKED), `messages` (`origin='manual_export'`, `window_eligible=0`), and `message_import_observations`.

- [ ] **Step 1: Failing test** — (a) missing `tz_name` → `ImportRefused`; (b) declared `MDY` on a DMY-only file → `ImportRefused`; (c) a clean DMY import writes N messages all with `window_eligible=0` and `origin='manual_export'`, creates UNLINKED participants, and one observation per message; (d) re-importing the same text adds 0 new messages (fingerprint dedupe) but records a second batch's observations on the shared messages.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Write** `whatsapp_export.py` composing grammar+parse+fingerprint; enforce the refusal gates first; write inside one transaction; dedupe messages by `import_fingerprint` (existing → attach a new observation, do not duplicate).
- [ ] **Step 4: Run — expect PASS.** **Step 5: Commit** `feat: export importer with refuse-don't-guess gates and idempotent write`.

---

### Task 6: Batch undo + forensic reparse

**Files:** Modify `src/whatsvault/importers/whatsapp_export.py`; Test `tests/test_import_undo.py`.

**Interfaces:**
- `whatsapp_export.undo_batch(vault_conn, batch_id) -> dict` — deletes the batch's observations; deletes a canonical message only if it now has **zero** import observations and no provider provenance (`wamid IS NULL`); returns counts. Never deletes the source artefact.
- `whatsapp_export.store_source_artifact(vault_conn, batch_id, raw_bytes, key) -> str` and `reparse(vault_conn, batch_id, key) -> dict` — decrypt the retained source and produce a fresh dry-run.

- [ ] **Step 1: Failing test** — two overlapping batches (A then B) observe the same message; `undo_batch(A)` must **not** delete the shared message (B still observes it); `undo_batch(B)` then deletes it (no observers, no wamid). Reparse of a stored artefact returns the same `would_add` as the original dry-run.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Write** the undo/reparse logic; source artefact stored via `crypto.atrest.seal_blob`.
- [ ] **Step 4: Run — expect PASS.** **Step 5: Commit** `feat: batch-scoped undo (observation-aware) and forensic reparse`.

---

### Task 7: Hostile ZIP handling

**Files:** Create `src/whatsvault/importers/zip_guard.py`; Test `tests/test_import_zip.py`.

**Interfaces:**
- `zip_guard.safe_extract(zip_path, dest_dir, *, max_files, max_total_bytes, max_ratio, max_file_bytes) -> dict` — rejects path traversal, absolute paths, symlinks, over-count, over-size, over-ratio (zip bomb); returns the sanitised file list.
- `zip_guard.find_transcript(file_list) -> str` — returns the single `.txt`; raises `AmbiguousTranscript` if multiple (never "largest wins").

- [ ] **Step 1: Failing test** — a crafted zip with a `../escape.txt` entry raises; a zip whose declared uncompressed size exceeds `max_ratio` raises; a zip with two `.txt` files raises `AmbiguousTranscript`; a normal one-transcript zip extracts and `find_transcript` returns it.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Write** `zip_guard.py` using `zipfile` with per-entry checks (resolve real path stays under dest, `is_symlink` via external_attr, `file_size`/compression-ratio caps).
- [ ] **Step 4: Run — expect PASS.** **Step 5: Commit** `feat: hostile-ZIP guard with traversal/bomb/ambiguity protection`.

---

### Task 8: Full-suite gate + changelog

- [ ] **Step 1:** `.venv/bin/pytest -q` (all Phase 1a + 1b green). **Step 2:** append a `Raouf:` CHANGELOG line. **Step 3:** commit `test: close out Phase 1b importer`.

## Self-Review

- Spec §8 coverage: date/DST refusal → Tasks 2,5; provisional identity → Tasks 1,5; many-to-many observations + undo → Tasks 1,6; conservative parsing (multiline/system/media/boundary) → Task 3; evidence fingerprints → Task 4; hostile ZIP → Task 7; source artefact reparse → Task 6; never-touches-control/window → Task 5 (asserted `window_eligible=0`, `origin='manual_export'`).
- Adversarial gate (spec §9 Phase 1): parser bombs, malformed/hostile ZIPs, duplicate imports, date/DST refusal — all covered by Tasks 2,5,6,7 tests.
- Placeholder scan: core modules carry real code; Tasks 3/5/6/7 describe the algorithm precisely with the exact interfaces and test intents rather than restating every line — the executor writes the module to satisfy the named tests. (If stricter code-in-every-step is desired, expand those tasks before executing.)
