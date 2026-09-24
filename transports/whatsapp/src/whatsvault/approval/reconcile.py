"""Delivery/status reconciliation (spec §6.6, ledger #59/#60).

Deterministic when the status event carries biz_opaque_callback_data (wv1:<attempt>) or a
known wamid -> resolves the send_attempt (INDETERMINATE -> SUBMITTED). Otherwise it records
a durable POSSIBLE_MATCH in reconciliation_candidates for HUMAN resolution — never
auto-attributed (two same-minute sends to one recipient stay ambiguous)."""

from .. import ids


def on_status_event(vault_conn, control_conn, status_event, *, now_ms) -> dict:
    wamid = status_event.get("wamid")
    callback = status_event.get("biz_opaque_callback_data")
    if callback and callback.startswith("wv1:"):
        atm = callback[4:]
        cur = control_conn.execute(
            "UPDATE send_attempts SET state='SUBMITTED', wamid=?, updated_at_ms=? "
            "WHERE id=? AND state='INDETERMINATE'",
            (wamid, now_ms, atm),
        )
        control_conn.commit()
        if cur.rowcount == 1:
            return {"outcome": "RESOLVED", "attempt_id": atm}
    if wamid:
        row = control_conn.execute("SELECT id FROM send_attempts WHERE wamid=?", (wamid,)).fetchone()
        if row:
            control_conn.execute("UPDATE send_attempts SET updated_at_ms=? WHERE id=?", (now_ms, row[0]))
            control_conn.commit()
            return {"outcome": "RESOLVED", "attempt_id": row[0]}
    cid = ids.new_id("rcn")
    control_conn.execute(
        "INSERT INTO reconciliation_candidates(id, wamid, recipient_id, provider_ts_ms, status, state, "
        "created_at_ms) VALUES(?,?,?,?,?, 'POSSIBLE_MATCH', ?)",
        (
            cid,
            wamid,
            status_event.get("recipient_id"),
            status_event.get("provider_ts_ms"),
            status_event.get("status"),
            now_ms,
        ),
    )
    control_conn.commit()
    return {"outcome": "POSSIBLE_MATCH", "candidate_id": cid}


def resolve(control_conn, candidate_id, *, decision) -> dict:
    state = "RESOLVED" if decision == "resolve" else "DISMISSED"
    control_conn.execute(
        "UPDATE reconciliation_candidates SET state=?, resolution=? WHERE id=?",
        (state, decision, candidate_id),
    )
    control_conn.commit()
    return {"candidate_id": candidate_id, "state": state}
