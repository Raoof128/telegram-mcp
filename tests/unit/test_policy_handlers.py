"""policy explain / simulate / diff (design §2.3; review #2, #3, #4; Review Focus #5)."""

from types import SimpleNamespace

import pytest

from comms.transports.telegram.authority.effective import diff_rows, normalize_new_refs
from comms.transports.telegram.authority.staging import StagingRegistry
from comms.transports.telegram.ipc.admin import ADMIN_PEER, PeerCredentials
from comms.transports.telegram.ipc.handlers._wrapper import run_tx
from comms.transports.telegram.ipc.handlers.clients import CLIENT_COMMANDS
from comms.transports.telegram.ipc.handlers.policy import policy_handlers
from comms.transports.telegram.ipc.handlers.projects import (
    PROJECT_COMMANDS,
    member_commands,
    project_handlers,
)
from comms.transports.telegram.ipc.handlers.scope import scope_commands
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.storage.effective_access import known_refs, snapshot
from comms.transports.telegram.telegram.discovery import DiscoveryStore
from tests.authority_fixtures import (
    BETA_REF,
    PROJECT_REF,
    seed_authority_rows,
    seed_project_world,
    seed_second_project,
)

CLIENT = "tcl_" + "a" * 26


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def _view(peer_type, peer_id, name):
    chat = {"user": "private", "chat": "group", "channel": "channel"}[peer_type]
    return SimpleNamespace(
        peer_type=peer_type, peer_id=peer_id, display_name=name, username=None, chat_type=chat
    )


@pytest.fixture
def world(tmp_path):
    conn = open_db(tmp_path / "m.db")
    seed_authority_rows(conn)
    seed_project_world(conn)
    seed_second_project(conn)
    discovery, members = DiscoveryStore(), DiscoveryStore()
    commands = {
        **PROJECT_COMMANDS,
        **scope_commands(discovery),
        **member_commands(members),
        **CLIENT_COMMANDS,
    }
    clock = Clock()
    registry = StagingRegistry(clock=clock)
    handlers = policy_handlers(conn, registry=registry, simulatable=commands)
    return SimpleNamespace(
        conn=conn,
        h=handlers,
        registry=registry,
        clock=clock,
        commands=commands,
        discovery=discovery,
        projects=project_handlers(conn, members=members),
    )


def _logical_state(conn):
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            " ORDER BY name"
        )
    ]
    return {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}


def _policy_epoch(conn):
    return conn.execute("SELECT policy_epoch FROM policy_state").fetchone()[0]


def _handle(w, peer_type, peer_id, name):
    listed = w.discovery.new_snapshot([_view(peer_type, peer_id, name)], _policy_epoch(w.conn))
    return listed[0]["handle"]


def _member_handle(w, project_ref, peer_ref):
    listed = w.projects["project members"]({"project_ref": project_ref})["members"]
    return next(m["handle"] for m in listed if m["peer_ref"] == peer_ref)


def _cases(w):
    """Every simulatable policy/project/client mutation (spec §33.1)."""
    bob = w.conn.execute("SELECT peer_ref FROM peers WHERE telegram_peer_id = 101").fetchone()[0]
    return [
        ("project create", lambda: {"slug": "gamma", "display_name": "Gamma"}),
        ("project rename", lambda: {"project_ref": BETA_REF, "display_name": "Beta 2"}),
        ("project enable", lambda: {"project_ref": BETA_REF}),
        ("project disable", lambda: {"project_ref": BETA_REF}),
        (
            "project grant-client",
            lambda: {"project_ref": BETA_REF, "client_ref": CLIENT, "egress_level": "full_text"},
        ),
        (
            "project set-egress",
            lambda: {
                "project_ref": PROJECT_REF,
                "client_ref": CLIENT,
                "egress_level": "excerpt",
                "excerpt_max_codepoints": 100,
            },
        ),
        ("project revoke-client", lambda: {"project_ref": PROJECT_REF, "client_ref": CLIENT}),
        ("project revoke-cross-search", lambda: {"project_ref": BETA_REF, "client_ref": CLIENT}),
        ("project grant-cross-search", lambda: {"project_ref": BETA_REF, "client_ref": CLIENT}),
        (
            "project add-peer",
            lambda: {"project_ref": BETA_REF, "handle": _handle(w, "user", 200, "New")},
        ),
        (
            "project remove-peer",
            lambda: {"project_ref": BETA_REF, "handle": _member_handle(w, BETA_REF, bob)},
        ),
        ("scope mode", lambda: {"mode": "all_cloud_chats"}),
        ("scope allow", lambda: {"handle": _handle(w, "user", 300, "Allowed")}),
        ("scope deny", lambda: {"handle": _handle(w, "user", 100, "Ali")}),
        ("scope remove", lambda: {"handle": _handle(w, "user", 101, "Bob")}),
        ("client disable", lambda: {"client": "codex_local"}),
    ]


def test_the_case_list_is_every_simulatable_command(world):
    assert {name for name, _ in _cases(world)} == set(world.commands)


@pytest.mark.parametrize("index", range(16))
def test_simulate_equals_commit_semantically(world, index):
    """Review #3: every command, including ones that mint refs inside the TX."""
    name, make_args = _cases(world)[index]
    args = make_args()
    known = known_refs(world.conn)
    simulated = world.h["policy simulate"]({"command": name, "args": args})["diff"]
    before = snapshot(world.conn)
    run_tx(world.conn, world.commands[name], args)  # the SAME args, handle included
    real = normalize_new_refs(diff_rows(before, snapshot(world.conn)), known)
    assert simulated == real, name


def test_simulation_leaves_a_selection_handle_usable(world):
    """Review #2: take() is non-consuming, so the dry run spends nothing."""
    handle = _handle(world, "user", 300, "Allowed")
    world.h["policy simulate"]({"command": "scope allow", "args": {"handle": handle}})
    assert run_tx(world.conn, world.commands["scope allow"], {"handle": handle}) == {
        "decision": "allow"
    }


def test_simulate_leaves_logical_state_identical(world):
    before = _logical_state(world.conn)
    world.h["policy simulate"](
        {"command": "project create", "args": {"slug": "g", "display_name": "G"}}
    )
    assert _logical_state(world.conn) == before
    assert len(world.registry) == 1  # the one intended in-memory difference


def test_simulate_refuses_non_policy_commands(world):
    before = _logical_state(world.conn)
    for command in ("lock", "auth login", "policy simulate", "project list", "client rotate"):
        with pytest.raises(ValueError, match="cannot be simulated"):
            world.h["policy simulate"]({"command": command, "args": {}})
    assert _logical_state(world.conn) == before


def test_diff_returns_the_staged_change_until_the_base_moves(world):
    staged = world.h["policy simulate"](
        {"command": "project disable", "args": {"project_ref": BETA_REF}}
    )
    assert world.h["policy diff"]({"staged": staged["staged"]})["diff"] == staged["diff"]
    run_tx(world.conn, PROJECT_COMMANDS["scope mode"], {"mode": "all_cloud_chats"})
    with pytest.raises(ValueError, match="stale"):
        world.h["policy diff"]({"staged": staged["staged"]})


def test_a_stage_is_bound_to_the_admin_peer(world):
    """Review #4: another admin peer cannot read an operator's staged change."""
    token = ADMIN_PEER.set(PeerCredentials(uid=501, gid=20))
    try:
        staged = world.h["policy simulate"](
            {"command": "project disable", "args": {"project_ref": BETA_REF}}
        )
    finally:
        ADMIN_PEER.reset(token)
    token = ADMIN_PEER.set(PeerCredentials(uid=502, gid=20))
    try:
        with pytest.raises(ValueError, match="stale"):
            world.h["policy diff"]({"staged": staged["staged"]})
    finally:
        ADMIN_PEER.reset(token)


def test_the_registry_is_bounded(world):
    registry = StagingRegistry(clock=world.clock, max_live=2)
    handlers = policy_handlers(world.conn, registry=registry, simulatable=world.commands)
    args = {"command": "scope mode", "args": {"mode": "all_cloud_chats"}}
    handlers["policy simulate"](args)
    handlers["policy simulate"](args)
    with pytest.raises(ValueError, match="too many"):
        handlers["policy simulate"](args)


def test_staged_handles_expire(world):
    staged = world.h["policy simulate"](
        {"command": "scope mode", "args": {"mode": "all_cloud_chats"}}
    )
    world.clock.now = 601.0
    with pytest.raises(ValueError, match="stale"):
        world.h["policy diff"]({"staged": staged["staged"]})


def test_explain_requires_client_and_project(world):
    rows = world.h["policy explain"]({"client_ref": CLIENT, "project_ref": PROJECT_REF})["rows"]
    assert rows[0]["operation"] == "discover"
    with pytest.raises(ValueError):
        world.h["policy explain"]({"client_ref": CLIENT})
