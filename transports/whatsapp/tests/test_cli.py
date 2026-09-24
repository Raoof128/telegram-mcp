import os
import pathlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from whatsvault.approval import devices as D
from whatsvault.cli import commands, main
from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _ctx(tmp_path):
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(c, "control")
    return commands.Ctx(v, c)


def _sec1(p):
    return p.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def test_no_forbidden_verb_registered():
    assert set(commands.COMMANDS).isdisjoint(commands.FORBIDDEN_VERBS)
    for v in ("approve", "send", "sign", "dispatch", "create_capability"):
        assert v not in commands.COMMANDS


def test_cli_source_never_calls_send_or_mint():
    src = pathlib.Path(commands.__file__).read_text()
    assert "execute_write" not in src and "store_grant" not in src and "sign_for_test" not in src


def test_doctor_and_health(tmp_path):
    ctx = _ctx(tmp_path)
    assert commands.cmd_doctor(ctx, {})["ok"] is True
    assert commands.cmd_health(ctx, {})["ok"] is True


def test_devices_list_and_revoke(tmp_path):
    ctx = _ctx(tmp_path)
    did = D.enroll(
        ctx.control,
        "iphone",
        signing_pub=_sec1(ec.generate_private_key(ec.SECP256R1())),
        agreement_pub=_sec1(ec.generate_private_key(ec.SECP256R1())),
    )
    assert commands.cmd_devices_list(ctx, {})["devices"][0]["id"] == did
    commands.cmd_devices_revoke(ctx, {"device_id": did})
    assert commands.cmd_devices_list(ctx, {})["devices"][0]["status"] == "REVOKED"


def test_scheduler_disable(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.control.execute(
        "INSERT INTO scheduled_jobs(job_id, conversation_id, generation_mode, enabled) "
        "VALUES('job_1','cnv','static',1)"
    )
    ctx.control.commit()
    commands.cmd_scheduler_disable(ctx, {"job_id": "job_1"})
    assert commands.cmd_scheduler_list(ctx, {})["jobs"][0]["enabled"] == 0


def test_reconcile_resolve_via_run(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.control.execute(
        "INSERT INTO reconciliation_candidates(id, wamid, state, created_at_ms) "
        "VALUES('rcn_1','w','POSSIBLE_MATCH',1)"
    )
    ctx.control.commit()
    rc = main.run(["reconcile-resolve", "--candidate-id", "rcn_1", "--decision", "dismiss"], ctx)
    assert rc == 0
    assert (
        ctx.control.execute("SELECT state FROM reconciliation_candidates WHERE id='rcn_1'").fetchone()[0]
        == "DISMISSED"
    )
