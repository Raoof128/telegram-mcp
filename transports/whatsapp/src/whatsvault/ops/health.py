"""Aggregated health/status (5x-A). Rolls up the doctor checks + ingest circuit state
into one status object for the CLI `health` verb and launchd health probes."""

from .. import doctor


def status(vault_conn, control_conn) -> dict:
    checks = (
        doctor.check_vault(vault_conn) + doctor.check_search(vault_conn) + doctor.check_ingest(vault_conn)
    )
    circuit = vault_conn.execute("SELECT circuit_state FROM ingest_state WHERE id=1").fetchone()[0]
    dlq_depth = vault_conn.execute("SELECT COUNT(*) FROM ingest_dlq").fetchone()[0]
    return {
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "summary": {"dlq_depth": dlq_depth, "circuit_state": circuit},
    }
