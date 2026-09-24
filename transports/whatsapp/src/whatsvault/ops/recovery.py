"""Conservative startup recovery (5x-A). Resolves crash-stranded SUBMITTING attempts to
INDETERMINATE (never a blind resend), reports reloadable scheduler jobs and the ingest
circuit state, and surfaces accepted-but-unconsumed approval envelopes for the dispatcher
to re-drive through the sender — this function never sends anything itself."""

from ..approval import sender
from ..ingest import dlq


def run_startup(vault_conn, control_conn, now_ms) -> dict:
    rec = sender.recover_startup(control_conn, now_ms)
    jobs = control_conn.execute("SELECT COUNT(*) FROM scheduled_jobs WHERE enabled=1").fetchone()[0]
    pending = control_conn.execute(
        "SELECT COUNT(*) FROM approvals a WHERE a.nonce IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM approval_nonces n WHERE n.nonce = a.nonce)"
    ).fetchone()[0]
    return {
        "submitting_recovered": rec["recovered"],
        "scheduler_jobs": jobs,
        "circuit_state": dlq.state(vault_conn),
        "pending_approvals": pending,
    }
