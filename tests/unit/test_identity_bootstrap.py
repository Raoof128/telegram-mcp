from comms.transports.telegram.ipc.handlers.clients import client_handlers
from comms.transports.telegram.keys.store import read_lease_seed
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.storage.identity import ensure_account, ensure_owner_principal

KEY = b"\x07" * 32


def test_principal_and_account_are_idempotent(tmp_path):
    conn = open_db(tmp_path / "m.db")
    first = ensure_owner_principal(conn, privacy_key=KEY)
    assert ensure_owner_principal(conn, privacy_key=KEY) == first
    account = ensure_account(conn, telegram_user_id=4242, label=None)
    assert ensure_account(conn, telegram_user_id=4242, label=None) == account
    row = conn.execute(
        "SELECT mode, policy_epoch, include_archived, include_private, include_groups,"
        " include_channels FROM policy_state"
    ).fetchone()
    assert tuple(row) == ("allowlist", 1, 0, 1, 1, 1)


def test_client_rotate_creates_then_reseeds(tmp_path):
    conn = open_db(tmp_path / "m.db")
    ensure_owner_principal(conn, privacy_key=KEY)
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    handlers = client_handlers(conn, key_dir=keys)
    ref = handlers["client rotate"]({"client": "codex_local"})["client_ref"]
    seed1 = read_lease_seed(keys, ref)
    again = handlers["client rotate"]({"client": "codex_local"})["client_ref"]
    assert again == ref and read_lease_seed(keys, ref) != seed1
    listed = handlers["client list"]({})["clients"]
    assert listed == [{"client_ref": ref, "client_kind": "codex_local", "enabled": True}]


def test_unknown_client_kind_is_refused(tmp_path):
    import pytest

    conn = open_db(tmp_path / "m.db")
    ensure_owner_principal(conn, privacy_key=KEY)
    with pytest.raises(ValueError):
        client_handlers(conn, key_dir=tmp_path)["client rotate"]({"client": "curl"})
