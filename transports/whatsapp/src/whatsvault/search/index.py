"""Search index sync (spec §4, INV-SEARCH). External-content FTS5 discipline:
every FTS row's rowid == its search_documents.rowid; re-index issues the FTS5
'delete' command with the OLD normalised values before inserting the new rows
(a plain DELETE on external-content FTS corrupts it). Everything here is derived
and rebuildable from messages.text_original."""

from . import normalise as N


def stage_message(conn, message_id: str, text_original) -> None:
    """Write the index rows for one message WITHOUT committing.

    The caller owns the transaction. Import uses this so indexing lands in the
    same transaction as the message insert; index_message() is the committing
    convenience wrapper for callers that own no transaction.
    """
    ts = N.to_search(text_original or "")
    tc = N.to_compact(text_original or "")
    row = conn.execute(
        "SELECT rowid, text_search, text_compact FROM search_documents WHERE message_id=?", (message_id,)
    ).fetchone()
    if row:
        rid, old_s, old_c = row[0], row[1], row[2]
        conn.execute(
            "INSERT INTO fts_lexical(fts_lexical, rowid, text_search) VALUES('delete', ?, ?)", (rid, old_s)
        )
        conn.execute(
            "INSERT INTO fts_compact(fts_compact, rowid, text_compact) VALUES('delete', ?, ?)", (rid, old_c)
        )
        conn.execute(
            "UPDATE search_documents SET normaliser_version=?, text_search=?, text_compact=? WHERE rowid=?",
            (N.NORMALISER_VERSION, ts, tc, rid),
        )
    else:
        cur = conn.execute(
            "INSERT INTO search_documents(message_id, normaliser_version, text_search, text_compact) "
            "VALUES(?,?,?,?)",
            (message_id, N.NORMALISER_VERSION, ts, tc),
        )
        rid = cur.lastrowid
    conn.execute("INSERT INTO fts_lexical(rowid, text_search) VALUES(?,?)", (rid, ts))
    conn.execute("INSERT INTO fts_compact(rowid, text_compact) VALUES(?,?)", (rid, tc))


def index_message(conn, message_id: str, text_original) -> None:
    stage_message(conn, message_id, text_original)
    conn.commit()


def reindex_stale(conn) -> int:
    rows = conn.execute(
        "SELECT message_id FROM search_documents WHERE normaliser_version != ?", (N.NORMALISER_VERSION,)
    ).fetchall()
    for r in rows:
        mid = r[0]
        text = conn.execute("SELECT text_original FROM messages WHERE id=?", (mid,)).fetchone()
        stage_message(conn, mid, text[0] if text else "")
    conn.commit()
    return len(rows)


def rebuild_all(conn) -> int:
    conn.execute("DELETE FROM search_documents")
    conn.execute("INSERT INTO fts_lexical(fts_lexical) VALUES('delete-all')")
    conn.execute("INSERT INTO fts_compact(fts_compact) VALUES('delete-all')")
    msgs = conn.execute("SELECT id, text_original FROM messages WHERE text_original IS NOT NULL").fetchall()
    for m in msgs:
        stage_message(conn, m[0], m[1])
    conn.commit()
    return len(msgs)
