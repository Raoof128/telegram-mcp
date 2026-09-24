# WhatsVault Phase 1c — Search & Persian Normalisation (STANDALONE EXECUTION PLAN)

> **Generated just-in-time** from the design spec §4/§3.9, the master roadmap, the **Corrections Ledger #32–#35 (INV-SEARCH cluster)**, and the **actual repository state** (schema/APIs + empirical FTS5 probes run 2026-08-27, after Phase 1b commit `5e5600a`). Supersedes the roadmap's 1c reference draft.

> **For agentic workers:** superpowers:executing-plans. `- [ ]` steps. TDD iron law.

**Goal:** A disposable, rebuildable FTS index over vault evidence with English+Persian recall, an injection-safe query AST, uncertainty-interval-correct time filters, and snippets rendered from the ORIGINAL text via per-codepoint span mapping — never from the normalised index.

**Spec:** §4, §3.9. **INV-SEARCH** — search is a disposable derived index; it never alters evidence, dedup identity, signatures, quotations, or display; every structure is droppable and rebuildable from `text_original`.

## Bound to actual repo state (verified this session)

- Empirical FTS5 probe PASSED: external-content FTS5 (`content='search_documents', content_rowid='rowid'`) with `unicode61` (lexical) and `trigram` (compact); the FTS5 `'delete'` command removes external-content rows given `(rowid, old values)`; `secure-delete` config + `integrity-check` accepted; lexical finds spaced `می روم`, trigram finds joined `میروم`. (sqlcipher3 0.6.2 / SQLCipher 4.12.0.)
- `capabilities.assert_sqlite_capabilities` already requires `fts5`, `trigram`, `fts_secure_delete`, `core_secure_delete`, `foreign_keys`.
- `connection.open_db` sets `row_factory = sqlcipher3.Row` (index rows by position or column name) and `PRAGMA secure_delete=ON`.
- Migration lane `vault` is at version 2 (0001 initial, 0002 import). This phase registers `(3, "vault/0003_search.sql")`.
- `messages.text_original` is the only source; `messages` uncertainty columns are `ts_lower_ms`, `ts_upper_ms_exclusive` (verified in 1a schema).
- Normaliser prototype verified empirically: per-codepoint core yields every §4.4 equality (Yeh/Kaf/hamza/digits/ZWNJ/case) AND maps a normalised `کتاب` back to original `کتـاب` including an internal tatweel (#34 length-changing case).

## Ledger corrections folded in (binding)

- **#32** Two MATCH forms: `compile_lexical(q)` (spaces preserved, unicode61) and `compile_compact(q)` (separators removed, trigram). The normaliser returns both forms; compact keeps the ≥3-char rule and simple term/phrase semantics only.
- **#33** Time filters use uncertainty-interval **overlap**, not a lower-bound: `ts_upper_ms_exclusive > from_ms AND ts_lower_ms < to_ms`.
- **#34** Snippets map **per-codepoint** back to ORIGINAL offsets (expanding the index-map design); `display_text == text_original` always; internal stripped chars (tatweel/combining/ZWNJ) fall inside the span.
- **#35** `index.index_message` + `index.reindex_stale` exist here; **live-ingest wiring is Phase 3c** (post-commit index / reindex sweep, ACK never waits on indexing). This phase provides and tests the rebuildable index machinery.

## Global constraints (§4)
- Derived columns live in `search_documents`, never on `messages`. Reindex recomputes rows where `normaliser_version != NORMALISER_VERSION`.
- Two indexes: `fts_lexical` (unicode61) primary; `fts_compact` (trigram, ≥3 chars) fallback recall only, never equal-ranked. **No blended BM25.** No recency decay in V1.
- **Never** use FTS5 `snippet()`/`highlight()` for user-visible evidence. Render from `text_original` via span mapping.
- Query is an AST, never raw MATCH: tokenise → normalise → re-quote into explicit FTS5 syntax; hard caps raise `QueryTooComplex`. No `raw_fts_query`.
- External-content discipline: every FTS row's rowid == its `search_documents.rowid`; deletes use the FTS5 `'delete'` command with matching rowid + old values (a plain DELETE on an external-content FTS corrupts it). `index_message` inserts `search_documents` first, then both FTS rows with that rowid, in one transaction.

---

### Task 1: Persian dual-output normaliser (+ mapping mode)

**Files:** Create `src/whatsvault/search/__init__.py`, `src/whatsvault/search/normalise.py`; Test `tests/test_normalise.py`.

**Interfaces produced:**
- `normalise.NORMALISER_VERSION: int = 1`
- `normalise.to_search(text) -> str` (ZWNJ/space → single space)
- `normalise.to_compact(text) -> str` (all separators removed)
- `normalise.normalise_query(term) -> tuple[str, str]` → `(search_form, compact_form)`
- `normalise.normalise_mapped(text, *, joined: bool) -> tuple[str, list[int]]` — normalised string + per-normalised-char origin index into the ORIGINAL string (for #34).

- [ ] **Step 1 — failing test** (the §4.4 corpus + a mapping check). **Step 2 — FAIL. Step 3 — implement** the per-codepoint core (verified this session). **Step 4 — PASS. Step 5 — commit** `feat(1c): Persian dual-output normaliser (+ mapping mode)`.

---

### Task 2: Search schema + index sync (vault migration 0003)

**Files:** Create `src/whatsvault/db/migrations/vault/0003_search.sql`; Modify `migrations/__init__.py`; Create `src/whatsvault/search/index.py`; Test `tests/test_search_index.py`.

**Migration:** `search_documents(rowid INTEGER PRIMARY KEY, message_id TEXT UNIQUE, normaliser_version INTEGER, text_search TEXT, text_compact TEXT)` + `fts_lexical` (unicode61, external-content) + `fts_compact` (trigram, external-content) + secure-delete config applied at index time.

**Interfaces produced:**
- `index.index_message(vault_conn, message_id, text_original) -> None` — normalise, upsert `search_documents`, sync both FTS rows by rowid (delete-old-then-insert for re-index), one transaction.
- `index.reindex_stale(vault_conn) -> int` — recompute rows where `normaliser_version != NORMALISER_VERSION`; returns count.
- `index.rebuild_all(vault_conn) -> int` — drop + rebuild from `messages.text_original`.

- [ ] **Step 1 — failing test**: index `می‌روم` + `hello world`; lexical finds `می روم`; compact finds `میروم`; bumping a stored `normaliser_version` then `reindex_stale` recomputes only the stale row; `rebuild_all` reproduces results after `DELETE FROM search_documents`. **Steps 2–4.** **Step 5 — commit** `feat(1c): droppable search_documents + dual FTS with secure-delete`.

---

### Task 3: Query AST + injection-safe compiler + tiered run (#32, #33)

**Files:** Create `src/whatsvault/search/query.py`; Test `tests/test_search_query.py`.

**Interfaces produced:**
- `query.SearchQuery` dataclass: `terms, phrase, prefix, near, conversations, contacts, direction, from_ms, to_ms, origins, limit`.
- `query.compile_lexical(q) -> str` / `query.compile_compact(q) -> str` — the ONLY producers of MATCH syntax; each term normalised (via `normalise_query`) then re-quoted (embedded `"` doubled); operators emitted structurally; caps `MAX_TERMS/MAX_QUERY_BYTES/MAX_NEAR/MAX_LIMIT` raise `QueryTooComplex`; compact drops terms <3 chars.
- `query.run(vault_conn, q) -> list[dict]` — lexical tier first, trigram fallback; cross-tier dedup by `message_id` keeping the lexical rank; filters as SQL predicates (never MATCH); **time filter is interval overlap (#33)**: `ts_upper_ms_exclusive > from_ms AND ts_lower_ms < to_ms`.

- [ ] **Step 1 — failing test**: a term containing FTS operators (`foo OR bar`, `col:val`, `x*`, a stray `"`) is treated as literal quoted text; exceeding `MAX_TERMS` raises `QueryTooComplex`; interval-overlap correctly includes a minute-precision message straddling a boundary that a lower-bound filter would drop; a message matching both tiers appears once with lexical rank. **Steps 2–4.** **Step 5 — commit** `feat(1c): injection-safe query AST, dual-tier compiler, interval-overlap filters`.

---

### Task 4: Snippets from original text via per-codepoint mapping (#34)

**Files:** Create `src/whatsvault/search/snippet.py`; Test `tests/test_snippet.py`.

**Interfaces produced:**
- `snippet.render(text_original, query_terms, *, window=40) -> dict` → `{"display_text": text_original, "spans": [(start,end), ...]}`, spans indexing into ORIGINAL, via `normalise.normalise_mapped`.

- [ ] **Step 1 — failing test**: (a) `کتاب` in original `این كتاب است` (Arabic Kaf) → `display_text == text_original` + a span over original `كتاب`; (b) a length-changing tatweel case (`کتـاب`) still maps the term to the correct ORIGINAL offsets. **Steps 2–4.** **Step 5 — commit** `feat(1c): original-text snippet rendering via per-codepoint span mapping`.

---

### Task 5: doctor search checks + full-suite gate

**Files:** Modify `src/whatsvault/doctor.py`; Test extend `tests/test_doctor.py`; CHANGELOG.

**Interfaces produced:** `doctor.check_search(vault_conn) -> list[dict]` — FTS `integrity-check`, `search_documents` ↔ `messages` orphan/parity, `normaliser_version` staleness.

- [ ] **Step 1 — failing test**: parity OK after indexing; an orphan `search_documents` row (or missing index row) flagged; a stale `normaliser_version` row flagged. **Steps 2–4** (full suite + `pip check` green). **Step 5 — commit** `feat(1c): doctor search integrity checks; close out Phase 1c`.

## Self-review (consistency + ambiguity gate — required before execution)

- **SR-1 (Task 2):** `secure-delete` FTS config is issued inside migration 0003 (once at creation), not per-index.
- **SR-2 (Task 2):** `index_message` is an upsert — on re-index it issues the FTS5 `'delete'` with the OLD normalised values before inserting the new rows (external-content discipline); `reindex_stale`/`rebuild_all` call a no-commit `_index_one` helper and commit once.
- **SR-3 (Task 3):** `run()` joins `fts → search_documents → messages` to return `text_original` (search_documents stores only normalised columns) for the snippet layer.
- **SR-4 (Task 4):** `snippet.render` matches in the search (spaced) form for both the original and the query terms — covers Kaf/tatweel and stays consistent with the primary lexical tier.
- **Consistency checks passed:** `rowid INTEGER PRIMARY KEY` column literally named `rowid` works as `content_rowid` (probed); interval-overlap filter handles one-sided ranges; compact tier skipped when all terms <3 chars; `normaliser_version` per-row drives staleness.

**Gate: PASS** — plan is execution-safe. Begin Task 1.
