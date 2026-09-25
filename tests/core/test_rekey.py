"""comms v0.3 Task B12: the comms-db-key rekey protocol, crash-safe at every boundary (A14, N1)."""

import os
import stat

import pytest

from comms.core.keys.secrets import FileSecretStore
from comms.core.storage.db import CommsDbKeyError, TransactionIOError, open_comms_db
from comms.core.storage.migrations import migrate
from comms.core.storage.rekey import BOUNDARIES, KeyPointer, RekeyCrash, open_with_recovery, rekey

ITEM = "comms-db-key"


@pytest.fixture
def env(tmp_path):
    secrets = FileSecretStore(tmp_path / "secrets")
    pointer = KeyPointer(tmp_path / "secrets" / "comms-db-key.pointer")
    key = os.urandom(32)
    secrets.put(ITEM, 1, key)
    pointer.set(1)
    path = tmp_path / "comms.db"
    conn = open_comms_db(path, key)
    migrate(conn)
    conn.execute("CREATE TABLE marker (v TEXT)")
    conn.execute("INSERT INTO marker VALUES ('kept')")
    return {"path": path, "secrets": secrets, "pointer": pointer, "conn": conn, "old": key}


def _reads(conn):
    return conn.execute("SELECT v FROM marker").fetchone()[0] == "kept"


def test_rekey_refuses_inside_a_transaction(env):
    env["conn"].execute("BEGIN IMMEDIATE")
    with pytest.raises(TransactionIOError):
        rekey(env["conn"], env["path"], env["secrets"], env["pointer"])
    env["conn"].execute("ROLLBACK")
    assert env["pointer"].get() == 1 and env["secrets"].versions(ITEM) == [1]


def test_rekey_happy_path_old_key_fails_new_opens(env):
    assert rekey(env["conn"], env["path"], env["secrets"], env["pointer"]) == 2
    with pytest.raises(CommsDbKeyError):
        open_comms_db(env["path"], env["old"])
    assert env["pointer"].get() == 2 and env["secrets"].versions(ITEM) == [2]
    assert _reads(open_comms_db(env["path"], env["secrets"].get(ITEM, 2)))


@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_recovery_after_each_boundary_opens_with_the_right_key_and_repairs_the_pointer(
    env, boundary
):
    with pytest.raises(RekeyCrash):
        rekey(env["conn"], env["path"], env["secrets"], env["pointer"], crash_at=boundary)
    rewritten = boundary != "after_stage"
    conn = open_with_recovery(env["path"], env["secrets"], env["pointer"])
    assert _reads(conn)
    assert env["pointer"].get() == (2 if rewritten else 1)
    # the key the file does not use is kept until the next successful run cleans it
    assert set(env["secrets"].versions(ITEM)) >= {env["pointer"].get()}
    conn.close()
    assert (
        rekey(
            open_with_recovery(env["path"], env["secrets"], env["pointer"]),
            env["path"],
            env["secrets"],
            env["pointer"],
        )
        == 3
    )
    assert env["secrets"].versions(ITEM) == [3]


def test_neither_key_opens_fails_closed_with_comms_db_key_error(env):
    env["conn"].close()
    env["secrets"].delete(ITEM, 1)
    env["secrets"].put(ITEM, 1, os.urandom(32))
    env["secrets"].put(ITEM, 2, os.urandom(32))
    with pytest.raises(CommsDbKeyError):
        open_with_recovery(env["path"], env["secrets"], env["pointer"])
    assert env["pointer"].get() == 1


def test_the_pointer_is_atomic_and_private(env):
    path = env["pointer"].path
    env["pointer"].set(7)
    assert env["pointer"].get() == 7 and stat.S_IMODE(path.stat().st_mode) == 0o600
    path.write_text("not-a-version")
    with pytest.raises(CommsDbKeyError):
        env["pointer"].get()
