-- Phase 1c: disposable, rebuildable search index (spec §4, INV-SEARCH).
-- search_documents holds the derived normalised columns; the two external-content
-- FTS5 indexes reference it by rowid. NO FK to messages: search is fully decoupled
-- and rebuildable from messages.text_original alone.
CREATE TABLE search_documents (
    rowid INTEGER PRIMARY KEY,
    message_id TEXT UNIQUE NOT NULL,
    normaliser_version INTEGER NOT NULL,
    text_search TEXT,
    text_compact TEXT
);

CREATE VIRTUAL TABLE fts_lexical USING fts5(
    text_search, content='search_documents', content_rowid='rowid', tokenize='unicode61');
CREATE VIRTUAL TABLE fts_compact USING fts5(
    text_compact, content='search_documents', content_rowid='rowid', tokenize='trigram');

-- SR-1: secure-delete config set once at creation (persists in the FTS config).
INSERT INTO fts_lexical(fts_lexical, rank) VALUES('secure-delete', 1);
INSERT INTO fts_compact(fts_compact, rank) VALUES('secure-delete', 1);
