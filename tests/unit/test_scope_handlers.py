import pytest

from comms.transports.telegram.ipc.handlers.projects import project_handlers
from comms.transports.telegram.ipc.handlers.scope import scope_handlers
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.telegram.discovery import DiscoveryStore
from comms.transports.telegram.telegram.telethon_adapter import TelegramConfig, TelethonSession
from tests.authority_fixtures import PROJECT_REF, seed_authority_rows
from tests.telegram.fake_client import FakeClient
from tests.unit.test_dialogs_and_discovery import _dialogs_result


@pytest.fixture
async def world(tmp_path):
    conn = open_db(tmp_path / "m.db")
    seed_authority_rows(conn)
    fake = FakeClient({"messages.GetDialogsRequest": _dialogs_result()})
    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: fake,
    )
    await session.start()
    return conn, scope_handlers(conn, session, DiscoveryStore())


def _epoch(conn):
    return conn.execute("SELECT policy_epoch FROM policy_state").fetchone()[0]


async def test_discover_then_allow_writes_canonical_policy_and_bumps_the_epoch(world):
    conn, h = world
    found = await h["scope discover"]({})
    handle = next(s["handle"] for s in found["selections"] if s["display_name"] == "U0")
    before = _epoch(conn)
    h["scope allow"]({"handle": handle})
    row = conn.execute(
        "SELECT telegram_peer_type, telegram_peer_id, decision FROM peer_policy"
    ).fetchone()
    assert tuple(row) == ("user", 100, "allow") and _epoch(conn) == before + 1


async def test_a_handle_dies_with_the_policy_epoch(world):
    _conn, h = world
    found = await h["scope discover"]({})
    first, second = found["selections"][0]["handle"], found["selections"][1]["handle"]
    h["scope allow"]({"handle": first})
    with pytest.raises(ValueError):
        h["scope deny"]({"handle": second})  # discovered under the old epoch


async def test_add_peer_writes_membership_and_bumps_the_project_epoch(world):
    conn, h = world
    found = await h["scope discover"]({})
    handle = found["selections"][0]["handle"]
    h["project add-peer"]({"project_ref": PROJECT_REF, "handle": handle})
    assert conn.execute("SELECT count(*) FROM project_peers").fetchone()[0] == 1
    assert conn.execute("SELECT project_epoch FROM projects").fetchone()[0] == 2
    assert h["scope list"]({})["rules"] == []


async def test_scope_mode_bumps_the_epoch_and_refuses_unknown_modes(world):
    conn, _h = world
    mode = project_handlers(conn)["scope mode"]  # the single copy (Phase-5a plan Task 3)
    before = _epoch(conn)
    assert mode({"mode": "all_cloud_chats"}) == {"mode": "all_cloud_chats"}
    assert _epoch(conn) == before + 1
    with pytest.raises(ValueError):
        mode({"mode": "everything"})


async def test_a_failed_eviction_still_refuses_stale_handles(world, monkeypatch):
    _conn, h = world
    found = await h["scope discover"]({})
    first, second = found["selections"][0]["handle"], found["selections"][1]["handle"]
    from comms.transports.telegram.telegram.discovery import DiscoveryStore

    monkeypatch.setattr(DiscoveryStore, "invalidate_all", lambda self: None)  # eviction "fails"
    h["scope allow"]({"handle": first})
    with pytest.raises(ValueError):
        h["scope deny"]({"handle": second})  # still refused: the policy epoch moved


async def test_a_raw_row_number_or_name_is_never_a_selector(world):
    _conn, h = world
    await h["scope discover"]({})
    for bogus in ("1", "U0", "user:100"):
        with pytest.raises(ValueError):
            h["scope allow"]({"handle": bogus})


async def test_scope_remove_deletes_the_rule_and_bumps_the_epoch(world):
    conn, h = world
    found = await h["scope discover"]({})
    handle = next(s["handle"] for s in found["selections"] if s["display_name"] == "U0")
    h["scope allow"]({"handle": handle})
    found = await h["scope discover"]({})  # the allow moved the epoch
    handle = next(s["handle"] for s in found["selections"] if s["display_name"] == "U0")
    before = _epoch(conn)
    h["scope remove"]({"handle": handle})
    assert conn.execute("SELECT COUNT(*) FROM peer_policy").fetchone()[0] == 0
    assert _epoch(conn) == before + 1
