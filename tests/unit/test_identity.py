from telegram_mcp.runtime.identity import resolve_principal
from telegram_mcp.storage.db import open_db
from tests.authority_fixtures import seed_authority_rows

CLIENT = "tcl_" + "a" * 26


def test_an_enabled_bearer_client_resolves_to_identity_only(tmp_path):
    conn = open_db(tmp_path / "meta.db")
    seed_authority_rows(conn)
    ctx = resolve_principal(conn, CLIENT)
    assert ctx is not None
    assert (ctx.client_ref, ctx.client_kind, ctx.account_id) == (CLIENT, "codex_local", 1)
    assert not hasattr(ctx, "grants"), "identity must not carry authority"


def test_disabled_unknown_and_non_bearer_clients_do_not_resolve(tmp_path):
    conn = open_db(tmp_path / "meta.db")
    seed_authority_rows(conn)
    assert resolve_principal(conn, "tcl_" + "z" * 26) is None
    conn.execute("UPDATE mcp_clients SET auth_kind = 'mtls'")
    conn.commit()
    assert resolve_principal(conn, CLIENT) is None
    conn.execute("UPDATE mcp_clients SET auth_kind = 'bearer', enabled = 0")
    conn.commit()
    assert resolve_principal(conn, CLIENT) is None
