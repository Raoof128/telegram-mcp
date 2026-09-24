"""Handlers on the runners: behaviour kept, two shipped defects fixed (0B G11)."""

import os
import stat

import pytest

from telegram_mcp.ipc.handlers.clients import client_handlers
from telegram_mcp.ipc.handlers.projects import PROJECT_COMMANDS, project_handlers
from telegram_mcp.storage.db import open_db
from tests.authority_fixtures import seed_authority_rows


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "m.db")
    seed_authority_rows(c)
    return c


def test_scope_mode_is_keyed_to_the_owner_row(conn):
    h = project_handlers(conn)
    before = conn.execute("SELECT policy_epoch FROM policy_state").fetchone()[0]
    assert h["scope mode"]({"mode": "all_cloud_chats"}) == {"mode": "all_cloud_chats"}
    row = conn.execute("SELECT mode, policy_epoch FROM policy_state").fetchone()
    assert tuple(row) == ("all_cloud_chats", before + 1)


def test_every_project_command_is_a_tx_command():
    assert set(PROJECT_COMMANDS) == {
        "project create",
        "project enable",
        "project disable",
        "project grant-client",
        "project set-egress",
        "project revoke-client",
        "scope mode",
    }


def test_rotate_refuses_a_disabled_client_without_enable(conn, tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    h = client_handlers(conn, key_dir=keys)
    ref = h["client rotate"]({"client": "claude_code_local"})["client_ref"]
    conn.execute("UPDATE mcp_clients SET enabled = 0 WHERE client_ref = ?", (ref,))
    conn.commit()
    seed = (keys / f"lease-seed.{ref}").read_bytes()
    with pytest.raises(ValueError, match="disabled"):
        h["client rotate"]({"client": "claude_code_local"})
    assert (keys / f"lease-seed.{ref}").read_bytes() == seed  # refused before any write
    assert h["client rotate"]({"client": "claude_code_local", "enable": True})["client_ref"] == ref
    enabled = conn.execute("SELECT enabled FROM mcp_clients WHERE client_ref = ?", (ref,))
    assert enabled.fetchone()[0] == 1


def test_a_refused_rotation_leaves_the_live_seed_untouched(conn, tmp_path):
    """Review #10: an error must not have a side effect."""
    from telegram_mcp.ipc.handlers._wrapper import BUSY

    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    h = client_handlers(conn, key_dir=keys)
    ref = h["client rotate"]({"client": "codex_local"})["client_ref"]
    live = (keys / f"lease-seed.{ref}").read_bytes()
    holder = open_db(tmp_path / "m.db")
    holder.execute("BEGIN IMMEDIATE")
    conn.execute("PRAGMA busy_timeout = 50")
    try:
        with pytest.raises(ValueError, match=BUSY):
            h["client rotate"]({"client": "codex_local"})
    finally:
        holder.rollback()
    assert (keys / f"lease-seed.{ref}").read_bytes() == live
    assert not (keys / f"lease-seed.{ref}.next").exists()


def test_a_successful_rotation_activates_a_new_seed(conn, tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    h = client_handlers(conn, key_dir=keys)
    ref = h["client rotate"]({"client": "codex_local"})["client_ref"]
    first = (keys / f"lease-seed.{ref}").read_bytes()
    assert h["client rotate"]({"client": "codex_local"})["seed"] == "activated"
    assert (keys / f"lease-seed.{ref}").read_bytes() != first
    assert not (keys / f"lease-seed.{ref}.next").exists()


def test_list_commands_reject_arguments(conn, tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    with pytest.raises(ValueError):
        client_handlers(conn, key_dir=keys)["client list"]({"x": 1})
    with pytest.raises(ValueError):
        project_handlers(conn)["project list"]({"x": 1})


def test_rotated_seeds_are_0600(conn, tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    ref = client_handlers(conn, key_dir=keys)["client rotate"]({"client": "codex_local"})[
        "client_ref"
    ]
    assert stat.S_IMODE(os.stat(keys / f"lease-seed.{ref}").st_mode) == 0o600
