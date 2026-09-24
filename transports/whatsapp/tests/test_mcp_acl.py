import os

import pytest

from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.mcp import acl


def _vault(tmp_path):
    conn = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(conn, "vault")
    conn.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn')")
    conn.execute("INSERT INTO conversations(id, account_id, type) VALUES('cnv','acc','dm')")
    return conn


def test_default_is_allow_mcp(tmp_path):
    conn = _vault(tmp_path)
    assert (
        conn.execute("SELECT mcp_visibility FROM conversations WHERE id='cnv'").fetchone()[0] == "ALLOW_MCP"
    )


def test_set_local_only_and_listed(tmp_path):
    conn = _vault(tmp_path)
    acl.set_visibility(conn, "cnv", "LOCAL_ONLY")
    assert acl.local_only_ids(conn) == {"cnv"}


def test_bad_value_rejected(tmp_path):
    conn = _vault(tmp_path)
    with pytest.raises(ValueError):
        acl.set_visibility(conn, "cnv", "PUBLIC")
    assert acl.local_only_ids(conn) == set()
