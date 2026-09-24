"""Vault integrity checks (spec §3.7 I5 + structural + SQLCipher integrity).
A doctor reconstructs evidence truth and repairs drift; it never preserves a
forged or future window value. Send-side invariants I2/I3/I4 are Phase 4."""

from whatsvault import ids
from whatsvault.search import normalise as _N


def advance_window(control_conn, conversation_id: str, incoming_provider_ms: int) -> int:
    """Live ingest path: monotonic MAX(existing, incoming). Never used by doctor."""
    row = control_conn.execute(
        "SELECT last_inbound_ms FROM conversation_windows WHERE conversation_id=?", (conversation_id,)
    ).fetchone()
    existing = row[0] if row else 0
    new_val = max(existing, incoming_provider_ms)
    control_conn.execute(
        "INSERT INTO conversation_windows(conversation_id, last_inbound_ms) VALUES(?,?) "
        "ON CONFLICT(conversation_id) DO UPDATE SET last_inbound_ms=excluded.last_inbound_ms",
        (conversation_id, new_val),
    )
    control_conn.commit()
    return new_val


def rebuild_window_from_evidence(vault_conn, control_conn, conversation_id: str) -> dict:
    """Doctor path: the window MUST equal the exact MAX over window-eligible inbound
    evidence. If the stored projection differs (e.g. a forged future value), repair it
    to the evidence truth and report drift."""
    (evidence_ms,) = vault_conn.execute(
        "SELECT COALESCE(MAX(ts_lower_ms), 0) FROM messages "
        "WHERE conversation_id=? AND direction='in' AND window_eligible=1",
        (conversation_id,),
    ).fetchone()
    stored_row = control_conn.execute(
        "SELECT last_inbound_ms FROM conversation_windows WHERE conversation_id=?", (conversation_id,)
    ).fetchone()
    stored_ms = stored_row[0] if stored_row else 0
    drift = stored_ms != evidence_ms
    if drift:
        control_conn.execute(
            "INSERT INTO conversation_windows(conversation_id, last_inbound_ms) VALUES(?,?) "
            "ON CONFLICT(conversation_id) DO UPDATE SET last_inbound_ms=excluded.last_inbound_ms",
            (conversation_id, evidence_ms),
        )
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
    findings.append(
        {
            "check": "message_id_prefix",
            "ok": bad == 0,
            "detail": f"{bad} message id(s) fail prefix/ULID validation",
        }
    )

    ic = vault_conn.execute("PRAGMA integrity_check").fetchone()[0]
    findings.append({"check": "integrity_check", "ok": ic == "ok", "detail": ic})

    fk = vault_conn.execute("PRAGMA foreign_key_check").fetchall()
    findings.append(
        {"check": "foreign_key_check", "ok": len(fk) == 0, "detail": f"{len(fk)} foreign-key violation(s)"}
    )

    cic = vault_conn.execute("PRAGMA cipher_integrity_check").fetchall()
    findings.append(
        {
            "check": "cipher_integrity_check",
            "ok": len(cic) == 0,
            "detail": f"{len(cic)} encrypted-page HMAC failure(s)",
        }
    )

    return findings


def check_search(vault_conn) -> list[dict]:
    findings: list[dict] = []
    for fts in ("fts_lexical", "fts_compact"):
        try:
            vault_conn.execute(f"INSERT INTO {fts}({fts}) VALUES('integrity-check')")
            findings.append({"check": f"{fts}_integrity", "ok": True, "detail": "ok"})
        except Exception as exc:
            findings.append({"check": f"{fts}_integrity", "ok": False, "detail": str(exc)})

    orphans = vault_conn.execute(
        "SELECT COUNT(*) FROM search_documents sd LEFT JOIN messages m ON m.id=sd.message_id "
        "WHERE m.id IS NULL"
    ).fetchone()[0]
    findings.append(
        {
            "check": "search_orphans",
            "ok": orphans == 0,
            "detail": f"{orphans} search_documents row(s) with no message",
        }
    )

    missing = vault_conn.execute(
        "SELECT COUNT(*) FROM messages m LEFT JOIN search_documents sd ON sd.message_id=m.id "
        "WHERE m.text_original IS NOT NULL AND sd.message_id IS NULL"
    ).fetchone()[0]
    findings.append(
        {
            "check": "search_missing",
            "ok": missing == 0,
            "detail": f"{missing} message(s) with text not indexed",
        }
    )

    stale = vault_conn.execute(
        "SELECT COUNT(*) FROM search_documents WHERE normaliser_version != ?", (_N.NORMALISER_VERSION,)
    ).fetchone()[0]
    findings.append(
        {
            "check": "search_normaliser_stale",
            "ok": stale == 0,
            "detail": f"{stale} row(s) at a stale normaliser_version",
        }
    )
    return findings


def check_ingest(vault_conn) -> list[dict]:
    findings: list[dict] = []
    depth = vault_conn.execute("SELECT COUNT(*) FROM ingest_dlq").fetchone()[0]
    findings.append({"check": "dlq_depth", "ok": depth == 0, "detail": f"{depth} DLQ row(s)"})
    oldest = vault_conn.execute("SELECT MIN(first_seen_ms) FROM ingest_dlq").fetchone()[0]
    findings.append({"check": "dlq_oldest_first_seen_ms", "ok": oldest is None, "detail": str(oldest)})
    row = vault_conn.execute("SELECT circuit_state, reason FROM ingest_state WHERE id=1").fetchone()
    findings.append(
        {
            "check": "circuit_breaker",
            "ok": row[0] == "CLOSED",
            "detail": row[0] + (f": {row[1]}" if row[1] else ""),
        }
    )
    return findings


def check_mcp(vault_conn, control_conn, ks=None) -> list[dict]:
    """MCP daemon readiness (#18/#19/#21/#23).

    `ks` is optional so this stays runnable in CI, where there is no Keychain;
    pass a keystore in production to also verify the daemon's keys exist. Without
    them the launchd unit exits 1 and KeepAlive restarts it, so an unprovisioned
    daemon looks like a restart loop rather than a stated precondition.
    """
    findings: list[dict] = []

    cols = [r[1] for r in vault_conn.execute("PRAGMA table_info(conversations)")]
    findings.append(
        {
            "check": "mcp_visibility_column",
            "ok": "mcp_visibility" in cols,
            "detail": "vault.conversations.mcp_visibility (LOCAL_ONLY fence)",
        }
    )

    present = control_conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_log'"
    ).fetchone()
    findings.append({"check": "audit_log_present", "ok": present is not None, "detail": "control.audit_log"})

    triggers = {
        r[0]
        for r in control_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='audit_log'"
        )
    }
    needed = {"trg_audit_no_update", "trg_audit_no_delete"}
    findings.append(
        {
            "check": "audit_log_append_only",
            "ok": needed <= triggers,
            "detail": f"missing={sorted(needed - triggers)}",
        }
    )

    if "mcp_visibility" in cols:
        n = vault_conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE mcp_visibility='LOCAL_ONLY'"
        ).fetchone()[0]
        # Informational: a fenced conversation is a deliberate choice, never a fault.
        findings.append(
            {
                "check": "mcp_local_only_conversations",
                "ok": True,
                "detail": f"{n} conversation(s) fenced from MCP",
            }
        )

    # A database behind the shipped schema is silent until something touches the
    # missing table, which is how the OAuth tables were found absent on a live
    # vault. Say it out loud instead.
    from .db import migrations as _migrations

    for lane, conn in (("vault", vault_conn), ("control", control_conn)):
        latest = max(n for n, _ in _migrations.MIGRATIONS[lane])
        have = _migrations.user_version(conn)
        if have < latest:
            findings.append(
                {
                    "check": "schema_current",
                    "ok": False,
                    "detail": f"{lane}.db is at schema {have}, shipped is {latest}; "
                    "run `whatsvault init` to migrate",
                }
            )
            break
    else:
        findings.append({"check": "schema_current", "ok": True, "detail": "vault and control current"})

    if ks is not None:
        from .mcp import audit as _audit
        from .mcp import auth as _auth

        for check, name in (
            ("mcp_token_provisioned", _auth.TOKEN_KEY_NAME),
            ("mcp_audit_key_provisioned", _audit.AUDIT_KEY_NAME),
        ):
            try:
                ks.require(name, 32)
                ok, detail = True, name
            except Exception as exc:
                ok, detail = False, f"{name}: {type(exc).__name__} (run `whatsvault mcp-provision`)"
            findings.append({"check": check, "ok": ok, "detail": detail})
    return findings
