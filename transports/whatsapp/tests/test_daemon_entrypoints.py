"""Every launchd-managed daemon must start, state its position, and stop cleanly.

Three of the four daemons cannot yet do their job: their dependencies are
Phase-0-gated or unbuilt. The defect being fixed here is not that they are
incomplete — it is that a well-formed plist pointed at a module with no entry
point, so launchd imported it, saw an immediate exit, and restarted it forever.
An unavailable daemon must say exactly what it is blocked on, once, and exit 0.
"""

import importlib
import os
from pathlib import Path

import pytest

from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.ops import launchd

DAEMONS = ["apps.ingest.consumer", "apps.scheduler.scheduler", "apps.dispatcher.dispatch", "apps.mcp.server"]


@pytest.fixture
def dbs(tmp_path):
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(c, "control")
    return v, c


@pytest.mark.parametrize("mod", DAEMONS)
def test_every_daemon_module_has_an_entrypoint(mod):
    m = importlib.import_module(mod)
    assert callable(getattr(m, "main", None)), f"{mod} has no main()"
    src = Path(m.__file__).read_text(encoding="utf-8")
    assert '__name__ == "__main__"' in src, f"{mod} cannot be run with -m"


@pytest.mark.parametrize(
    "mod,blocked_on",
    [
        ("apps.ingest.consumer", "queue_client"),
        ("apps.scheduler.scheduler", "job_payload_schema"),
        ("apps.dispatcher.dispatch", "whatsvault_meta_daemon"),
    ],
)
def test_blocked_daemon_reports_its_blocker_and_does_not_loop(dbs, mod, blocked_on):
    v, c = dbs
    m = importlib.import_module(mod)
    rec = m.run(v, c, now_ms=1_700_000_000_000)
    assert rec["status"] == "not_started"
    assert rec["blocked_on"] == blocked_on
    assert rec["detail"], "must say why, not just that"


@pytest.mark.parametrize(
    "mod", ["apps.ingest.consumer", "apps.scheduler.scheduler", "apps.dispatcher.dispatch"]
)
def test_blocked_daemon_log_record_is_content_free(dbs, mod):
    """structlog.event refuses content-bearing keys; run() must pass through it."""
    from whatsvault.ops import structlog

    v, c = dbs
    rec = importlib.import_module(mod).run(v, c, now_ms=1)
    assert structlog.event(rec) == rec


def test_dispatcher_never_sends(dbs):
    """The dispatcher drives whatsvault-meta on a valid approval envelope. Until
    that exists it must not acquire a send path by accident (spec §6, §11).

    Parsed with ast rather than grepped: the module's docstring legitimately
    *describes* transmission, and a substring scan would flag the prose while
    missing an aliased call. Only real identifiers and imports are inspected.
    """
    import ast

    m = importlib.import_module("apps.dispatcher.dispatch")
    tree = ast.parse(Path(m.__file__).read_text(encoding="utf-8"))
    forbidden = {"execute_write", "send_message", "transmit", "post", "urlopen", "request"}
    referenced, imported = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
            imported |= {a.name for a in node.names}
    assert not (referenced & forbidden), f"dispatcher calls {referenced & forbidden}"
    assert not (imported & {"requests", "httpx", "urllib", "socket", "http"}), (
        f"dispatcher imports a network client: {imported}"
    )
    assert "sender" not in imported, "dispatcher imports the sender write path"


def test_ingest_runs_the_drain_loop_when_a_queue_is_supplied(dbs):
    """The blocker is the missing queue client, not the loop: given one, it drains."""
    from apps.ingest.queue_client import FakeQueue

    v, c = dbs
    rec = importlib.import_module("apps.ingest.consumer").run(
        v, c, now_ms=1, queue=FakeQueue(), key_lookup=lambda _k: b"\x00" * 32, once=True
    )
    assert rec["status"] == "drained"
    assert rec["leased"] == 0


# ---- launchd: crash-restart without hot-looping a clean exit ------------------
def _write_plist(path, **overrides):
    """Write a minimal valid plist, so each test states only what it varies."""
    import plistlib

    pl = {
        "Label": "test",
        "ProgramArguments": ["a"],
        "RunAtLoad": True,
        "StandardOutPath": "/x/logs/x.log",
        "StandardErrorPath": "/x/logs/x.log",
    }
    pl.update(overrides)
    with path.open("wb") as fh:
        plistlib.dump(pl, fh)
    return {x["check"]: x["ok"] for x in launchd.validate(str(path))}


def test_validate_accepts_successfulexit_false_as_crash_restart(tmp_path):
    """KeepAlive=true restarts on ANY exit, so a daemon that cleanly reports it is
    unavailable would loop forever. {SuccessfulExit: false} still restarts on crash."""
    f = _write_plist(tmp_path / "x.plist", KeepAlive={"SuccessfulExit": False})
    assert all(f.values()), f


def test_validate_still_accepts_plain_true(tmp_path):
    """The original form stays valid; this is a widening, not a replacement."""
    f = _write_plist(tmp_path / "t.plist", KeepAlive=True)
    assert all(f.values()), f


def test_validate_still_rejects_missing_keepalive(tmp_path):
    f = _write_plist(tmp_path / "y.plist")
    assert f["keepalive_true"] is False


def test_validate_rejects_successfulexit_true(tmp_path):
    """{SuccessfulExit: true} restarts only on CLEAN exit — the opposite of what a
    daemon needs, and it would loop on the not-available path."""
    f = _write_plist(tmp_path / "z.plist", KeepAlive={"SuccessfulExit": True})
    assert f["keepalive_true"] is False


def test_mcp_preflight_reports_unprovisioned_keys_instead_of_raising():
    """Raising KeyMissing exits non-zero and, under KeepAlive, restarts forever."""
    from apps.mcp import server
    from whatsvault.crypto import keystore as KS
    from whatsvault.mcp import audit, auth

    ks = KS.MemoryKeyStore()
    rec = server.preflight(ks)
    assert rec["status"] == "not_started" and rec["blocked_on"] == "keys_not_provisioned"
    auth.provision_token(ks)
    ks.provision(audit.AUDIT_KEY_NAME, 32)
    assert server.preflight(ks) is None


def test_every_shipped_plist_uses_crash_restart_without_hot_looping():
    import glob
    import plistlib

    for path in glob.glob("apps/launchd/*.plist"):
        with open(path, "rb") as fh:
            pl = plistlib.load(fh)
        ka = pl["KeepAlive"]
        assert ka == {"SuccessfulExit": False}, (path, ka)
