import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from whatsvault.approval import devices as D
from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.ops import recovery


def _sec1(p):
    return p.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def test_run_startup_is_conservative(tmp_path):
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(c, "control")
    c.execute(
        "INSERT INTO drafts(id, conversation_id, account_id, phone_number_id, kind, state) "
        "VALUES('drf_1','cnv','acc','PN1','text','SUBMITTING')"
    )
    c.execute(
        "INSERT INTO send_attempts(id, draft_id, idempotency_key, state, created_at_ms, updated_at_ms) "
        "VALUES('atm_1','drf_1','i','SUBMITTING',1,1)"
    )
    c.execute(
        "INSERT INTO scheduled_jobs(job_id, conversation_id, generation_mode, enabled) "
        "VALUES('job_1','cnv','static',1)"
    )
    did = D.enroll(
        c,
        "iphone",
        signing_pub=_sec1(ec.generate_private_key(ec.SECP256R1())),
        agreement_pub=_sec1(ec.generate_private_key(ec.SECP256R1())),
    )
    c.execute(
        "INSERT INTO approvals(approval_id, draft_id, device_id, decision, nonce) "
        "VALUES('apv_1','drf_1',?, 'APPROVE', ?)",
        (did, os.urandom(32)),
    )
    c.commit()
    r = recovery.run_startup(v, c, 999)
    assert r["submitting_recovered"] == 1
    assert c.execute("SELECT state FROM send_attempts WHERE id='atm_1'").fetchone()[0] == "INDETERMINATE"
    assert r["scheduler_jobs"] == 1
    assert r["pending_approvals"] == 1  # surfaced, not dispatched
    assert r["circuit_state"] == "CLOSED"
