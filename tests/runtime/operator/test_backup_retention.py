"""D39-PRE Task E8b: retention and backup over the admin socket (chunked, peer-bound)."""

import base64
import hashlib

import pytest

from comms.core.backup import age
from comms.core.backup.export_import import StagedImports
from comms.core.backup.transfer import TransferRegistry
from comms.runtime.operator import OperatorContext, operator_handler
from comms.transports.telegram.ipc.admin import ADMIN_PEER, PeerCredentials

OWNER = PeerCredentials(uid=501, gid=20)
STRANGER = PeerCredentials(uid=502, gid=20)


@pytest.fixture
def world(daemon_world, tmp_path):
    identity = age.generate_identity()
    key_file = tmp_path / "backup.key"
    key_file.write_text(identity + "\n", encoding="ascii")
    key_file.chmod(0o600)
    ctx = daemon_world["ctx"]
    full = OperatorContext(
        writer=ctx.writer, store=ctx.store, clock=ctx.clock, legacy=ctx.legacy,
        transfers=TransferRegistry(), staged=StagedImports(),
        backup_recipient=age.recipient_of(identity), providers={"meta_waba": "123"},
        retention_days={"campaign_body_days": 365, "identity_retention_days": 365},
    )  # fmt: skip
    handle = operator_handler(full)

    def run(*words, peer=OWNER, **args):
        token = ADMIN_PEER.set(peer)
        try:
            return handle({"command": list(words), **args})
        finally:
            ADMIN_PEER.reset(token)

    daemon_world["run"]("cutover", "run")
    return {**daemon_world, "run": run, "key_file": key_file}


def _pull(run, reply, peer=OWNER):
    chunks = []
    for index in range(reply["chunks"]):
        pulled = run(
            "backup", "transfer", "pull", peer=peer, transfer=reply["transfer"], index=index
        )
        chunks.append(base64.b64decode(pulled["chunk"]))
    data = b"".join(chunks)
    assert hashlib.sha256(data).hexdigest() == reply["sha256"]
    return data


def _push(run, data):
    transfer = run("backup", "transfer", "begin-push", size=len(data),
                   sha256=hashlib.sha256(data).hexdigest())["transfer"]  # fmt: skip
    for i in range(0, max(len(data), 1), 32 * 1024):
        run("backup", "transfer", "push", transfer=transfer, index=i // (32 * 1024),
            chunk=base64.b64encode(data[i : i + 32 * 1024]).decode())  # fmt: skip
    return transfer


def test_export_pull_push_stage_commit_round_trip(world):
    run = world["run"]
    exported = run("backup", "export")
    assert set(exported) == {
        "transfer",
        "size",
        "sha256",
        "chunks",
        "sidecar",
        "binding",
        "signer_key_id",
    }
    ciphertext = _pull(run, exported)
    transfer = _push(run, ciphertext)
    staged = run("backup", "import", "stage", transfer=transfer, sidecar=exported["sidecar"],
                 identity=str(world["key_file"]))  # fmt: skip
    assert staged["handle"] and staged["incompatibilities"] == []
    committed = run("backup", "import", "commit", handle=staged["handle"])
    assert set(committed["diff"]) >= {"locations", "recipients"}
    assert run("audit", "verify", all=True)["ok"] is True


def test_a_transfer_belongs_to_the_peer_that_began_it(world):
    run = world["run"]
    exported = run("backup", "export")
    with pytest.raises(ValueError, match="another peer"):
        _pull(run, exported, peer=STRANGER)


def test_without_a_peer_the_backup_steps_refuse(world):
    ctx = world["ctx"]
    bare = operator_handler(OperatorContext(writer=ctx.writer, store=ctx.store, clock=ctx.clock))
    with pytest.raises(ValueError, match="backups are managed by the daemon|admin peer"):
        bare({"command": ["backup", "export"]})


def test_export_needs_a_recipient(world):
    ctx = world["ctx"]
    no_recipient = operator_handler(OperatorContext(
        writer=ctx.writer, store=ctx.store, clock=ctx.clock, transfers=TransferRegistry(),
        staged=StagedImports(), providers={}))  # fmt: skip
    token = ADMIN_PEER.set(OWNER)
    try:
        with pytest.raises(ValueError, match="backup_recipient"):
            no_recipient({"command": ["backup", "export"]})
    finally:
        ADMIN_PEER.reset(token)


def test_retention_runs_and_reports_counts_only(world):
    report = world["run"]("retention", "run")
    assert set(report) == {"phases", "outcome"} and report["phases"]
    assert all(isinstance(v, int) for v in report["phases"].values())
    assert world["run"]("audit", "verify", all=True)["ok"] is True


def test_the_cli_exports_to_private_files_and_restores_by_handle(
    world, tmp_path, monkeypatch, capsys
):
    """The CLI flows over the admin socket, the socket hop replaced by the real handlers."""
    import json
    import stat

    from comms import cli

    run = world["run"]
    monkeypatch.setattr(cli, "_admin_request",
                        lambda d, request: {"ok": True, "data": run(*request["args"]["command"],
                                            **{k: v for k, v in request["args"].items() if k != "command"})})  # fmt: skip
    out = tmp_path / "nightly"
    cli.main(["backup", "export", "--out", str(out)])
    exported = json.loads(capsys.readouterr().out)
    for path in (out.with_suffix(".age"), out.with_suffix(".sig")):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert exported["files"] == [str(out.with_suffix(".age")), str(out.with_suffix(".sig"))]
    cli.main(
        ["backup", "import", "stage", "--from", str(out), "--identity", str(world["key_file"])]
    )
    staged = json.loads(capsys.readouterr().out)
    cli.main(["backup", "import", "commit", "--handle", staged["handle"]])
    assert json.loads(capsys.readouterr().out)["committed"] is True
