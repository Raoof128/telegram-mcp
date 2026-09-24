"""Persistent, prepare-only scheduler (spec §5.6/§11, ledger #45/#46/#47).

Jobs are persisted so a process restart never drops schedules (#45). fire() re-validates
a precondition before surfacing a draft and only ever PREPARES a PENDING_APPROVAL draft —
never approves or sends. V1 forbids autonomous LLM generation: generation_mode is
static/template only (#47), enforced here and by a schema CHECK. The live APScheduler
loop (pinned, #46) is a thin gated wrapper around fire()."""

import json
import sys

from whatsvault import ids

_ALLOWED_MODES = ("static", "template")


class SchedulerError(Exception):
    pass


def persist_job(control_conn, job) -> str:
    mode = job.get("generation_mode", "static")
    if mode not in _ALLOWED_MODES:
        raise SchedulerError(f"generation_mode {mode!r} not allowed in V1 (no autonomous LLM)")
    job_id = job.get("job_id") or ids.new_id("job")
    control_conn.execute(
        "INSERT INTO scheduled_jobs(job_id, conversation_id, account_id, timezone, schedule, "
        "generation_mode, conditions, enabled, max_lateness_ms, next_run_ms, created_at_ms) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            job_id,
            job["conversation_id"],
            job.get("account_id"),
            job.get("timezone"),
            job.get("schedule"),
            mode,
            json.dumps(job.get("conditions")),
            1 if job.get("enabled", True) else 0,
            job.get("max_lateness_ms"),
            job.get("next_run_ms"),
            job.get("created_at_ms"),
        ),
    )
    control_conn.commit()
    return job_id


def load_jobs(control_conn) -> list:
    return [
        dict(r)
        for r in control_conn.execute(
            "SELECT job_id, conversation_id, generation_mode, enabled, schedule, next_run_ms "
            "FROM scheduled_jobs WHERE enabled=1"
        ).fetchall()
    ]


def fire(control_conn, job_id, *, precondition_fn, prepare_fn, now_ms) -> dict:
    run_id = ids.new_id("run")
    if precondition_fn():
        draft_id = prepare_fn()
        outcome = "PREPARED"
    else:
        draft_id = None
        outcome = "SKIPPED_PRECONDITION"
    control_conn.execute(
        "INSERT INTO job_runs(id, job_id, fired_at_ms, outcome, draft_id) VALUES(?,?,?,?,?)",
        (run_id, job_id, now_ms, outcome, draft_id),
    )
    control_conn.execute("UPDATE scheduled_jobs SET last_run_ms=? WHERE job_id=?", (now_ms, job_id))
    control_conn.commit()
    return {"run_id": run_id, "outcome": outcome, "draft_id": draft_id}


def build_live_scheduler():  # pragma: no cover - live loop, not run in CI
    from apscheduler.schedulers.background import BackgroundScheduler

    return BackgroundScheduler()


# --- daemon entrypoint ---------------------------------------------------------
BLOCKED_ON = "job_payload_schema"
DETAIL = (
    "scheduled_jobs carries no body / recipient_wa_id / phone_number_id column, "
    "so drafts.prepare() cannot be called from a job row; fire() is complete and "
    "prepare-only, but there is nothing for it to prepare"
)


def run(vault_conn, control_conn, now_ms) -> dict:
    """Start the scheduler. Jobs load and are counted, but no job can be fired
    until the payload columns exist, so this reports and stops rather than
    exiting instantly and being restarted forever by launchd."""
    from whatsvault.ops import structlog

    return structlog.event(
        {
            "service": "scheduler",
            "status": "not_started",
            "blocked_on": BLOCKED_ON,
            "detail": DETAIL,
            "jobs_loaded": len(load_jobs(control_conn)),
        }
    )


def main():  # pragma: no cover - process entrypoint
    import json
    import time

    from whatsvault.ops import daemon

    vault_conn, control_conn, blocked = daemon.open_databases("scheduler")
    if blocked is not None:
        print(json.dumps(blocked))
        return 0
    print(json.dumps(run(vault_conn, control_conn, int(time.time() * 1000))))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
