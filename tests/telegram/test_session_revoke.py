"""comms v0.3 Task B14: Telegram session revoke — two transactions, one LogOut, crash-finished (O3)."""

import ast
import json
from pathlib import Path

import pytest
from telethon.tl import functions

from comms.core.audit.anchor import COMMS_ANCHOR, read_anchor
from comms.core.audit.chain import COMMS, head
from comms.transports.telegram.storage.authority_view import load_security
from comms.transports.telegram.storage.settings import get_setting
from comms.transports.telegram.telegram import admin_rpc
from comms.transports.telegram.telegram.deadline import Deadline, WorkBudget
from comms.transports.telegram.telegram.errors import GatewayError
from comms.transports.telegram.telegram.telethon_adapter import (
    ADMIN_RPCS,
    OPERATIONS,
    TelegramConfig,
    TelethonSession,
)
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW
from tests.telegram.fake_client import FakeClient

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def env(tmp_path):
    world = comms_world(tmp_path)
    fake = FakeClient()
    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: fake,
    )
    await session.start()
    fake.calls.clear()
    world.update(fake=fake, session=session, legacy=world["port"].conn)
    return world


def _events(conn):
    return [
        json.loads(r[0])
        for r in conn.execute(
            "SELECT payload FROM audit_events WHERE kind = 'admin.session_revoke' ORDER BY chain_epoch, chain_seq"
        )
    ]


async def _revoke(env, crash_at=None):
    return await admin_rpc.revoke_session(
        env["legacy"], env["writer"], env["session"], now=NOW, crash_at=crash_at
    )


async def _finish(env):
    return await admin_rpc.finish_revocation(env["legacy"], env["writer"], env["session"], now=NOW)


async def test_revoke_order_and_single_rpc(env):
    epoch = load_security(env["legacy"])[0]
    assert await _revoke(env) == "confirmed"
    assert env["fake"].calls == ["auth.LogOutRequest"]
    assert [(e["phase"], e["outcome"]) for e in _events(env["conn"])] == [
        ("started", "pending"),
        ("finished", "confirmed"),
    ]
    assert get_setting(env["legacy"], "telegram.session_state") == "logged_out"
    assert get_setting(env["legacy"], "telegram.remote_revoke") == "confirmed"
    assert load_security(env["legacy"])[0] == epoch + 1
    assert not list((env["tmp"] / "s").glob("primary.session*"))  # wiped whatever the outcome


async def test_logout_rpc_only_after_the_started_event_is_anchored(env):
    seen = {}

    def at_logout(request):
        current = head(env["conn"], COMMS)
        anchored = read_anchor(
            COMMS_ANCHOR, env["anchor"], env["keys"].for_epoch(current["chain_epoch"])
        )
        seen["anchored"] = anchored["event_id"]
        seen["started"] = _events(env["conn"])[-1]["phase"]
        seen["head"] = current["event_id"]
        return True

    env["fake"].script["auth.LogOutRequest"] = at_logout
    await _revoke(env)
    assert seen["started"] == "started" and seen["anchored"] == seen["head"]


@pytest.mark.parametrize(
    "error, outcome", [(OSError("down"), "unknown"), (RuntimeError("refused"), "failed")]
)
async def test_a_failed_or_unknown_logout_still_wipes_and_finishes(env, error, outcome):
    env["fake"].script["auth.LogOutRequest"] = error
    assert await _revoke(env) == outcome
    assert env["fake"].calls == ["auth.LogOutRequest"]  # never retried
    assert _events(env["conn"])[-1]["outcome"] == outcome
    assert get_setting(env["legacy"], "telegram.session_state") == "logged_out"


async def test_cross_db_crash_states_converge(env):
    # REVOKING without the comms event: the event is appended first, then it finishes.
    with pytest.raises(admin_rpc.RevokeCrash):
        await _revoke(env, crash_at="after_tx1")
    assert _events(env["conn"]) == []
    assert await _finish(env) == "unknown"
    assert [(e["phase"], e["outcome"]) for e in _events(env["conn"])] == [
        ("started", "pending"),
        ("finished", "unknown"),
    ]
    assert env["fake"].calls == []  # nothing is sent at startup


async def test_an_event_without_revoking_means_tx1_never_happened(env):
    with env["writer"].transaction() as tx:
        tx.append(
            "admin.session_revoke",
            payload={"phase": "started", "outcome": "pending", "security_epoch": 1},
        )
    assert await _finish(env) == "aborted"
    assert env["fake"].calls == []
    assert get_setting(env["legacy"], "telegram.session_state") == "active"
    assert _events(env["conn"])[-1] == {
        "phase": "finished",
        "outcome": "aborted",
        "security_epoch": 1,
    }


async def test_reads_answer_session_revoked_after_tx1(env):
    with pytest.raises(admin_rpc.RevokeCrash):
        await _revoke(env, crash_at="after_tx1")
    assert env["session"].readiness() == "SESSION_REVOKED"
    fresh = TelethonSession(
        TelegramConfig(api_id=1, session_dir=env["tmp"] / "s2"), api_hash="0" * 32
    )
    admin_rpc.apply_session_state(env["legacy"], fresh)
    assert fresh.readiness() == "SESSION_REVOKED"


async def test_startup_finishes_revoking_without_resending(env):
    with pytest.raises(admin_rpc.RevokeCrash):
        await _revoke(env, crash_at="after_started")
    assert await _finish(env) == "unknown"
    assert env["fake"].calls == []
    assert await _finish(env) is None  # nothing left to finish
    assert get_setting(env["legacy"], "telegram.session_state") == "logged_out"


def test_admin_rpc_imported_only_by_the_auth_handler():
    importers = set()
    for path in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if any(n.endswith("telegram.telegram.admin_rpc") for n in names):
                importers.add(path.relative_to(ROOT).as_posix())
    assert importers == {"src/comms/transports/telegram/ipc/handlers/auth.py"}


async def test_runtime_recorder_never_sees_admin_rpcs_on_read_paths(env):
    assert ADMIN_RPCS == frozenset({"auth.LogOutRequest"})
    for operation, allowed in OPERATIONS.items():
        if operation != "admin.revoke":
            assert not (allowed & ADMIN_RPCS), operation
    with pytest.raises(GatewayError, match="INTERNAL_ERROR"):
        await env["session"]._call_reviewed(
            functions.auth.LogOutRequest(),
            operation="mcp.retrieval",
            client_ref="c",
            deadline=Deadline(5),
            budget=WorkBudget(max_rpcs=1),
        )
    assert env["fake"].calls == []
