import os

from apps.mcp import server
from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def test_no_forbidden_tool_registered():
    assert server.REGISTERED_TOOLS.isdisjoint(server.FORBIDDEN_TOOLS)


def test_expected_read_tools():
    assert {
        "search",
        "get_messages",
        "list_chats",
        "get_message_status",
        "get_conversation_window",
        "list_templates",
    } == server.REGISTERED_TOOLS


def test_all_tools_read_only():
    for a in server.TOOL_ANNOTATIONS.values():
        assert a["read_only_hint"] is True and a["open_world_hint"] is False


def test_handlers_take_no_bearer_argument(tmp_path):
    """#19 regression: auth moved to the transport. A `bearer` parameter here
    would be published in the tool JSON schema — asking the model for the secret."""
    import inspect

    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32), check_same_thread=False)
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32), check_same_thread=False)
    M.migrate(c, "control")
    handlers = server.build_tool_handlers(v, c, os.urandom(32))
    for name, fn in handlers.items():
        params = inspect.signature(fn).parameters
        assert "bearer" not in params, f"{name} still advertises a bearer parameter"


def test_handlers_still_audit(tmp_path):
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32), check_same_thread=False)
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32), check_same_thread=False)
    M.migrate(c, "control")
    handlers = server.build_tool_handlers(v, c, os.urandom(32))
    assert handlers["list_templates"]()["status"] == "OK"
    row = c.execute("SELECT tool, args_hash FROM audit_log").fetchone()
    assert row["tool"] == "list_templates" and len(row["args_hash"]) == 64


def test_build_app_is_auth_wrapped_and_refuses_anonymous(tmp_path):
    """The transport must not be reachable without the token (#18/#19)."""
    import asyncio

    from whatsvault.mcp.http_auth import BearerAuthMiddleware

    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32), check_same_thread=False)
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32), check_same_thread=False)
    M.migrate(c, "control")
    app = server.build_app(v, c, "tok", os.urandom(32))
    assert isinstance(app, BearerAuthMiddleware)

    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        sent.append(m)

    asyncio.run(app({"type": "http", "method": "POST", "path": "/mcp", "headers": []}, receive, send))
    assert next(m["status"] for m in sent if m["type"] == "http.response.start") == 401


def test_build_app_enables_dns_rebinding_protection(tmp_path):
    """A loopback listener still needs Host/Origin validation."""
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32), check_same_thread=False)
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32), check_same_thread=False)
    M.migrate(c, "control")
    settings = server.transport_security_settings()
    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts, "allowed_hosts must be pinned, not empty"
    assert all("127.0.0.1" in h or "localhost" in h for h in settings.allowed_hosts)


def test_main_symbols_resolve():
    """`main()` and ops.daemon.open_databases are pragma-no-cover, so nothing else
    would catch a renamed import. Assert every symbol they touch exists (an early
    draft referenced a non-existent whatsvault.db.paths and a 1-arg
    KeyStore.require, neither of which any test would have caught)."""
    import inspect

    from whatsvault.crypto.keystore import KeyringKeyStore
    from whatsvault.db import connection as C
    from whatsvault.mcp import audit
    from whatsvault.mcp import auth as auth_mod
    from whatsvault.ops import daemon, fsperms, paths

    assert callable(fsperms.harden_umask)
    p = paths.from_env({})
    assert p.vault_db and p.control_db
    positional = [
        n
        for n, prm in inspect.signature(C.open_existing).parameters.items()
        if prm.kind is not inspect.Parameter.KEYWORD_ONLY
    ]
    assert positional == ["kind", "path", "ks"]
    assert list(inspect.signature(KeyringKeyStore.require).parameters)[1:] == ["name", "nbytes"]
    assert auth_mod.TOKEN_KEY_NAME and audit.AUDIT_KEY_NAME

    helper = inspect.getsource(daemon.open_databases)
    for symbol in ("harden_umask", "from_env", "KeyringKeyStore", "open_existing"):
        assert symbol in helper, f"ops.daemon.open_databases lost {symbol}"
    entry = inspect.getsource(server.main)
    for symbol in ("open_databases", "preflight", "build_app", "uvicorn"):
        assert symbol in entry, f"server.main lost {symbol}"


def test_daemon_open_databases_reports_instead_of_raising(monkeypatch):
    """A non-macOS/unavailable Keychain must produce a clean blocked record, not an
    exception: raising exits non-zero and launchd restarts it forever."""
    from whatsvault.crypto import keystore
    from whatsvault.ops import daemon

    def boom():
        raise keystore.KeyStoreError("no keychain here")

    monkeypatch.setattr(keystore, "KeyringKeyStore", boom)
    v, c, blocked = daemon.open_databases("mcp")
    assert v is None and c is None
    assert blocked["status"] == "not_started"
    assert blocked["blocked_on"] == "keystore_unavailable"


def test_daemon_distinguishes_missing_keys_from_a_broken_keystore(monkeypatch):
    """KeyMissing subclasses KeyStoreError, so ordering matters. Reporting an
    unprovisioned key as 'keystore_unavailable' would send the operator to repair
    a Keychain that is fine."""
    from whatsvault.crypto import keystore
    from whatsvault.ops import daemon

    def missing():
        raise keystore.KeyMissing("whatsvault.vault.key.v1")

    monkeypatch.setattr(keystore, "KeyringKeyStore", missing)
    _, _, blocked = daemon.open_databases("ingest")
    assert blocked["blocked_on"] == "keys_not_provisioned"
    assert "provision" in blocked["detail"]
