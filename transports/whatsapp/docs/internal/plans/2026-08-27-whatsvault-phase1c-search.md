# WhatsVault Phase 1c — Search & Persian Normalisation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. `- [ ]` steps.

**Goal:** Build a disposable, rebuildable FTS index over vault evidence with English+Persian recall, an injection-safe query AST compiler, and snippets rendered from the original text — never from the normalised index.

**Architecture:** A derived `search_documents` table (droppable) feeds two FTS5 indexes: `fts_lexical` (`unicode61`) and `fts_compact` (`trigram`, fallback tier). The Persian normaliser produces both indexed columns from `messages.text_original`; the same normaliser (same version) runs on query terms. A query AST compiles to explicit FTS5 MATCH syntax — raw text never reaches MATCH. Snippets are derived on demand from `text_original`.

**Tech Stack:** Python ≥3.11, SQLCipher FTS5 (verified in Phase 1a capability gate), existing `whatsvault.db`. Builds on Phase 1a (+1b optional).

**Spec:** `docs/internal/specs/2026-08-27-whatsvault-design.md` §4.

## Global Constraints (spec §4)

- **INV-SEARCH** — search is a disposable derived index; it must never alter evidence, dedup identity, signatures, quotations, or message display. Every search structure is droppable and rebuildable from `text_original` alone.
- Derived columns live in `search_documents`, **not** on `messages`. Reindex recomputes rows where `normaliser_version != CURRENT`.
- Two indexes: `fts_lexical` (unicode61) primary; `fts_compact` (trigram, ≥3 chars) fallback recall only, never equal-ranked. **No blended BM25.** No implicit recency decay in V1.
- **Never** use FTS5 `snippet()`/`highlight()` for user-visible evidence (they return marked-up copies of the normalised column). Render from `text_original` via on-demand span mapping. `display_text = text_original` always.
- **Query is an AST, never raw MATCH.** Tokenise + re-quote into explicit FTS5 syntax; structured operators are typed params; hard caps on query bytes/token/phrase/NEAR/prefix/limit. No `raw_fts_query` tool.
- Query terms run the **identical** normaliser at the **same `normaliser_version`** as the index, or recall silently breaks.
- Secure delete on the FTS indexes: `fts secure-delete = 1` (verified present in Phase 1a) + `PRAGMA secure_delete=ON` (already set by `open_db`).
- Cross-tier dedup by `message_id`, keeping the higher (lexical) tier's rank.
- Persian pipeline (index only, never storage): NFC → Yeh `ي`→`ی` → Kaf `ك`→`ک` → hamza fold `أ إ آ ٱ`→`ا` (lossy, intentional) → strip Arabic combining marks (U+064B–065F, U+0670) + tatweel (U+0640) → Persian/Arabic-Indic digits → ASCII → Persian punctuation → ASCII → strip bidi controls → Latin case-fold.

---

### Task 1: Persian normaliser (dual output)

**Files:** Create `src/whatsvault/search/__init__.py`, `src/whatsvault/search/normalise.py`; Test `tests/test_normalise.py`.

**Interfaces:**
- `normalise.NORMALISER_VERSION: int = 1`.
- `normalise.to_search(text: str) -> str` — lexical column: ZWNJ→space, full pipeline.
- `normalise.to_compact(text: str) -> str` — compact column: all internal separators removed, full pipeline.
- `normalise.normalise_query(term: str) -> tuple[str, str]` — `(search_form, compact_form)` for a query term, identical pipeline.

- [ ] **Step 1: Failing test** — the bilingual corpus from spec §4.4:
```python
from whatsvault.search import normalise as N


def test_yeh_and_kaf_unified():
    assert N.to_search("علي") == N.to_search("علی")     # Arabic vs Persian Yeh
    assert N.to_search("كتاب") == N.to_search("کتاب")   # Arabic vs Persian Kaf


def test_zwnj_becomes_space_in_lexical():
    assert N.to_search("می‌روم") == N.to_search("می روم")


def test_separators_removed_in_compact():
    assert N.to_compact("می‌روم") == N.to_compact("میروم") == N.to_compact("می روم")


def test_digits_folded_to_ascii():
    assert N.to_search("۱۲۳") == N.to_search("١٢٣") == N.to_search("123")


def test_hamza_folding_is_lossy_by_design():
    assert N.to_search("آزاد") == N.to_search("ازاد")   # accepted false-positive


def test_latin_case_folded():
    assert N.to_search("SALAM Raouf") == N.to_search("salam raouf")


def test_query_uses_same_pipeline():
    s, c = N.normalise_query("می‌روم")
    assert s == N.to_search("می‌روم") and c == N.to_compact("می‌روم")
```

- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** `normalise.py` implementing the exact pipeline; `to_search`/`to_compact` differ only in the separator step. **Step 4: PASS.** **Step 5: Commit** `feat: Persian dual-output search normaliser`.

---

### Task 2: Search schema + index sync (vault migration 0003)

**Files:** Create `src/whatsvault/db/migrations/vault/0003_search.sql`; Modify `migrations/__init__.py`; Create `src/whatsvault/search/index.py`; Test `tests/test_search_index.py`.

**Interfaces:**
- Migration creates `search_documents(rowid INTEGER PRIMARY KEY, message_id TEXT UNIQUE, normaliser_version, text_search, text_compact)` and the two external-content FTS5 tables declared `USING fts5(text_search, content='search_documents', content_rowid='rowid', tokenize=...)` (lexical) / `content_rowid='rowid', tokenize='trigram'` (compact) + the FTS `secure-delete` config. **External-content discipline:** every FTS row's rowid MUST equal its `search_documents.rowid`; deletes use the FTS5 `'delete'` command with the matching rowid+old-values (or a `content=''` contentless variant if simpler) — a plain `DELETE` on an external-content FTS table corrupts it. `index_message` inserts into `search_documents` first, then the FTS rows with that rowid, in one transaction.
- `index.index_message(vault_conn, message_id, text_original) -> None` — writes `search_documents` + both FTS rows transactionally.
- `index.reindex_stale(vault_conn) -> int` — recompute rows where `normaliser_version != CURRENT`; returns count.
- `index.rebuild_all(vault_conn) -> int` — drop + rebuild from `messages.text_original`.

- [ ] **Step 1: Failing test** — index two messages (`می‌روم`, `hello world`); a lexical search finds `می روم`; a compact/trigram search finds `میروم`; reindexing after bumping the stored `normaliser_version` recomputes only the stale row; `rebuild_all` reproduces the same result after `DELETE FROM search_documents`.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** the migration (register `(3, ...)`) and `index.py` (FTS sync via triggers or explicit calls; apply `INSERT INTO fts_lexical(fts_lexical,rank) VALUES('secure-delete',1)`). **Step 4: PASS.** **Step 5: Commit** `feat: droppable search_documents + dual FTS indexes with secure-delete`.

---

### Task 3: Query AST + injection-safe compiler

**Files:** Create `src/whatsvault/search/query.py`; Test `tests/test_search_query.py`.

**Interfaces:**
- `query.SearchQuery` dataclass: `terms: list[str]`, `phrase: str | None`, `prefix: str | None`, `near: tuple[list[str], int] | None`, plus filters `conversations`, `contacts`, `direction`, `from_ms`, `to_ms`, `origins`, `limit`.
- `query.compile_match(q: SearchQuery) -> str` — the ONLY producer of FTS5 MATCH syntax; each term normalised then wrapped in double-quotes; operators emitted structurally; enforces caps (`MAX_TERMS`, `MAX_QUERY_BYTES`, `MAX_NEAR`, `MAX_LIMIT`) raising `QueryTooComplex`.
- `query.run(vault_conn, q) -> list[dict]` — tiered execution (lexical first, trigram fallback), cross-tier dedup by `message_id`, filters as SQL predicates (never MATCH terms).

- [ ] **Step 1: Failing test** — a term containing FTS operators (`foo OR bar`, `col:val`, `x*`, a stray `"`) is treated as **literal text** (quoted), not as query syntax; exceeding `MAX_TERMS` raises `QueryTooComplex`; a `NEAR` query compiles to explicit `NEAR(...)`; filters (date range, direction) are applied as SQL, and a message matching both tiers appears once with the lexical rank.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** `query.py` (normalise each term via `normalise.normalise_query`, escape embedded `"` by doubling, wrap in quotes; build the MATCH string from structured parts only). **Step 4: PASS.** **Step 5: Commit** `feat: injection-safe search query AST and tiered compiler`.

---

### Task 4: Snippets from original text (span mapping)

**Files:** Create `src/whatsvault/search/snippet.py`; Test `tests/test_snippet.py`.

**Interfaces:**
- `snippet.render(text_original: str, query_terms: list[str], *, window: int = 40) -> dict` — returns `{"display_text": text_original, "spans": [(start,end), ...]}` where spans index into **`text_original`**, computed by running the normaliser in mapping mode over the original and locating normalised query terms, then projecting back to original character offsets.

- [ ] **Step 1: Failing test** — (a) searching `کتاب` in an original body `این كتاب است` (Arabic Kaf in the original) returns `display_text` byte-identical to the original and a span covering the original `كتاب`; (b) a **length-changing** case — a body containing an Arabic diacritic/tatweel that the normaliser strips (so normalised length < original length) — still maps the matched term back to the correct ORIGINAL offsets (proves the mapping is an explicit index map, not a 1:1 assumption). Assert `display_text == text_original` exactly in both.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** `snippet.py` (a mapping-mode normaliser that emits `(original_index → normalised_index)` at token boundaries; locate term in normalised, project span back). **Step 4: PASS.** **Step 5: Commit** `feat: original-text snippet rendering via span mapping`.

---

### Task 5: `doctor` search checks + full-suite gate

**Files:** Modify `src/whatsvault/doctor.py`; Test extend `tests/test_doctor.py`; CHANGELOG.

**Interfaces:** add `doctor.check_search(vault_conn) -> list[dict]` — findings for FTS `integrity-check`, `search_documents` ↔ `messages` orphan/count parity, and `normaliser_version` staleness.

- [ ] **Step 1: Failing test** — after indexing, `check_search` reports parity OK; deleting a `messages` row without its `search_documents` row is flagged as an orphan; a stale `normaliser_version` row is flagged.
- [ ] **Step 2: Run — expect FAIL.** **Step 3: Write** `check_search`. **Step 4:** `.venv/bin/pytest -q` full suite green. **Step 5: Commit** `feat: doctor search integrity checks; close out Phase 1c`.

## Self-Review

- Spec §4 coverage: dual-output normaliser → Task 1; droppable `search_documents` + dual FTS + secure-delete → Task 2; AST/injection-safe query + tiered non-blended ranking + query-side normalisation → Task 3; snippet-from-original → Task 4; doctor parity/staleness → Task 5. INV-SEARCH upheld (Task 4 asserts `display_text == text_original`; all structures rebuildable via `rebuild_all`).
- Adversarial gate (spec §9 Phase 2 partial): FTS-operator injection via query text neutralised (Task 3); evidence never displayed from the normalised column (Task 4).
- Deferred (named, not dropped): semantic embeddings and Finglish (spec §4.4) — slots reserved, not V1.
- Fidelity note: Tasks 2–5 give exact interfaces + test intents with the algorithm described; core-module code is written to satisfy the named tests. Task 1 (the normaliser, the load-bearing piece) carries full test detail.
