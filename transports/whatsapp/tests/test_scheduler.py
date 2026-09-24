import os

import pytest

from apps.scheduler import scheduler
from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _fresh(tmp_path):
    key = os.urandom(32)
    path = str(tmp_path / "c.db")
    c = C.open_db(path, key)
    M.migrate(c, "control")
    return c, path, key


def test_persist_and_reload_survives_restart(tmp_path):
    c, path, key = _fresh(tmp_path)
    jid = scheduler.persist_job(c, {"conversation_id": "cnv", "generation_mode": "static"})
    c2 = C.open_db(path, key)  # simulate process restart: new handle, same key
    assert any(j["job_id"] == jid for j in scheduler.load_jobs(c2))


def test_ai_generation_rejected_v1(tmp_path):
    c, _, _ = _fresh(tmp_path)
    with pytest.raises(scheduler.SchedulerError):
        scheduler.persist_job(c, {"conversation_id": "cnv", "generation_mode": "ai"})


def test_fire_stale_precondition_produces_no_draft(tmp_path):
    c, _, _ = _fresh(tmp_path)
    jid = scheduler.persist_job(c, {"conversation_id": "cnv", "generation_mode": "static"})

    def _must_not_prepare():
        raise AssertionError("prepare_fn must not run when precondition is stale")

    r = scheduler.fire(c, jid, precondition_fn=lambda: False, prepare_fn=_must_not_prepare, now_ms=100)
    assert r["outcome"] == "SKIPPED_PRECONDITION" and r["draft_id"] is None
    assert c.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0] == 1  # run still recorded


def test_fire_prepares_exactly_one_draft(tmp_path):
    c, _, _ = _fresh(tmp_path)
    jid = scheduler.persist_job(c, {"conversation_id": "cnv", "generation_mode": "static"})
    r = scheduler.fire(c, jid, precondition_fn=lambda: True, prepare_fn=lambda: "drf_x", now_ms=100)
    assert r["outcome"] == "PREPARED" and r["draft_id"] == "drf_x"
