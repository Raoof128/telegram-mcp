"""Loopback Streamable-HTTP MCP server (spec §5, ledger #18/#20/#24/#54).

Registers exactly the read tools, each token-authenticated (#19) and keyed-HMAC
audited (#21). The negative surface (§5.5) is asserted by CI against the plain
module constants below, so the guarantee is robust to MCP-SDK version churn.

Auth is enforced by BearerAuthMiddleware at the transport, never as a tool
argument (#19). The app is built and asserted in tests; only the uvicorn serve
call in main() is uncovered."""

import functools
import inspect
import threading
import time
import urllib.parse

from whatsvault.mcp import audit, reads
from whatsvault.mcp import auth as auth_mod
from whatsvault.mcp.http_auth import BearerAuthMiddleware, PublicRouter
from whatsvault.mcp.oauth_http import OAuthApp
from whatsvault.search.query import DEFAULT_LIMIT, MAX_LIMIT, SearchQuery

# 18: loopback bind + the port the launchd unit and the tunnel client both target.
HOST = "127.0.0.1"
PORT = 8765
MCP_PATH = "/mcp"
# Deployment switch: the https origin this server is reachable at, when it is
# published. Its presence is what mounts the OAuth authorization server.
PUBLIC_URL_ENV = "WHATSVAULT_PUBLIC_URL"

# INV-CONTENT (#54): two strengths, honestly separated.
INV_CONTENT_HARD = (
    "Retrieved content cannot create write authority, create or modify policy, "
    "access credentials, or bypass server-side ACLs."
)
INV_CONTENT_ORCHESTRATION = (
    "Retrieved content should not influence the model to widen retrieval "
    "scope or invoke additional read tools; not cryptographically enforceable "
    "absent separately authorised retrieval scopes."
)
# #24 disclosure boundary.
OPENAI_DISCLOSURE = (
    "MCP returns only the minimum selected excerpts required; using ChatGPT with "
    "WhatsVault intentionally discloses those plaintext excerpts to the LLM service."
)

REGISTERED_TOOLS = frozenset(
    {
        "search",
        "get_messages",
        "list_chats",
        "get_message_status",
        "get_conversation_window",
        "list_templates",
    }
)
FORBIDDEN_TOOLS = frozenset(
    {
        "approve_draft",
        "send_prepared_message",
        "add_approval_device",
        "revoke_device",
        "set_policy",
        "create_capability",
        "set_mcp_visibility",
        "raw_fts_query",
        "sql_query",
        "http_request",
        "graph_api_call",
        "send_to_number",
        "broadcast",
        "delete_message",
        "export_vault",
        "get_credentials",
    }
)
# #20: read-only tools; the audit append is documented as outside the tool's logical environment.
_READ = {
    "read_only_hint": True,
    "open_world_hint": False,
    "idempotent_hint": True,
    "audit_exception": "appends to control.audit_log; no domain/message-state mutation",
}
TOOL_ANNOTATIONS = {name: dict(_READ) for name in REGISTERED_TOOLS}


def _json_safe(value):
    """Audit args are HMACed over canonical JSON; coerce non-JSON values (e.g. a
    SearchQuery) so hashing a structured query cannot raise mid-request."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return repr(value)


def _clamp_limit(limit) -> int:
    """SQLite treats LIMIT -1 as unbounded, so min(limit, MAX_LIMIT) is not a cap:
    a caller asking for -1 received the whole table. Clamp both ends."""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(n, MAX_LIMIT))


WINDOW_TOOL_PARAMETERS = ("conversation_id",)


def build_tool_handlers(vault_conn, control_conn, audit_key) -> dict:
    """Audited read handlers, independent of the MCP SDK transport.

    Authentication is NOT here — it is enforced by BearerAuthMiddleware on the
    transport (#19). A `bearer` parameter on these functions would be published
    in each tool's JSON schema, i.e. the server would be asking the model to
    supply the secret.
    """

    # The SDK runs synchronous handlers on a worker thread, and both connections
    # are opened once on the main thread. They are therefore opened with
    # check_same_thread=False, which lifts the DB-API's thread check but NOT the
    # requirement to serialise: two concurrent calls on one SQLite connection
    # interleave. This lock is that serialisation, and it also covers the audit
    # write, which touches control.db from the same worker thread.
    db_lock = threading.Lock()

    def guard(tool_name, fn):
        @functools.wraps(fn)  # keeps the real signature for schema generation
        def handler(*args, **kwargs):
            with db_lock:
                return _run(tool_name, fn, args, kwargs)

        def _run(tool_name, fn, args, kwargs):
            try:
                bound = inspect.signature(fn).bind(*args, **kwargs)
                recorded = _json_safe(dict(bound.arguments))
            except TypeError:
                recorded = {"_wv_unbindable": True}
            outcome = "ok"
            try:
                return fn(*args, **kwargs)
            except BaseException as exc:  # audit the failure, then re-raise
                outcome = f"error:{type(exc).__name__}"
                raise
            finally:
                # Recorded AFTER the call so the outcome is the real one: a probe
                # that errors must not leave a clean trail (§5.8).
                audit.record(
                    control_conn,
                    audit_key,
                    actor="mcp",
                    tool=tool_name,
                    args=recorded,
                    outcome=outcome,
                    now_ms=int(time.time() * 1000),
                )

        return handler

    def search_tool(
        q: str,
        conversation_id: str | None = None,
        direction: str | None = None,
        from_ms: int | None = None,
        to_ms: int | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list:
        """Full-text search over the vault.

        The tool takes the primitives a client can send and builds the query
        itself. It previously took `q` and handed it to reads.search unchanged,
        which expects a SearchQuery — so every call over the wire raised
        AttributeError on a string.
        """
        return reads.search(
            vault_conn,
            SearchQuery(
                terms=str(q).split(),
                conversations=[conversation_id] if conversation_id else [],
                direction=direction,
                from_ms=from_ms,
                to_ms=to_ms,
                limit=_clamp_limit(limit),
            ),
        )

    def conversation_window_tool(conversation_id: str) -> dict:
        """Whether the 24-hour send window is open.

        The clock is the server's. This took now_ms as a parameter, which let the
        caller assert the time that decides the answer (INV-SENDPOLICY).
        """
        return reads.get_conversation_window(
            control_conn, vault_conn, conversation_id, int(time.time() * 1000)
        )

    return {
        "search": guard("search", search_tool),
        "get_messages": guard(
            "get_messages",
            lambda conversation_id, from_ms=None, to_ms=None, limit=50: reads.get_messages(
                vault_conn, conversation_id, from_ms, to_ms, limit
            ),
        ),
        "list_chats": guard(
            "list_chats", lambda query=None, limit=20: reads.list_chats(vault_conn, query, limit)
        ),
        "get_message_status": guard(
            "get_message_status", lambda message_id: reads.get_message_status(vault_conn, message_id)
        ),
        "get_conversation_window": guard("get_conversation_window", conversation_window_tool),
        "list_templates": guard("list_templates", lambda: reads.list_templates(control_conn)),
    }


def transport_security_settings(port: int = PORT, public_url: str | None = None):
    """DNS-rebinding protection. Binding loopback is not sufficient on its own —
    a hostile page can still drive a browser at 127.0.0.1 unless Host is pinned.

    A published deployment adds the one hostname it is served at. The protection
    stays ON and no wildcard is ever admitted: an OAuth token that authenticates
    correctly still got `421 Invalid Host header` until the exact host was
    listed, and the fix for that is to name the host, not to stop checking.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = [f"127.0.0.1:{port}", "127.0.0.1", f"localhost:{port}", "localhost"]
    origins = [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
    if public_url:
        base = str(public_url).rstrip("/")
        hostname = urllib.parse.urlsplit(base).netloc
        if not hostname:
            raise ValueError(f"public_url has no host: {public_url!r}")
        hosts.append(hostname)
        origins.append(base)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def build_mcp_server(vault_conn, control_conn, audit_key, *, name="whatsvault"):
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    server = MCPServer(name)
    ann = ToolAnnotations(read_only_hint=True, open_world_hint=False, idempotent_hint=True)
    for tool_name, handler in build_tool_handlers(vault_conn, control_conn, audit_key).items():
        server.tool(name=tool_name, annotations=ann)(handler)
    return server


def assert_usable_from_worker_threads(**conns) -> None:
    """Fail at startup if a connection is pinned to the thread that opened it.

    Every database-backed tool raised `SQLite objects created in a thread can
    only be used in that same thread` on the live server while the in-process
    tests — which call handlers on the opening thread — stayed green. A wrong
    connection flag must stop the daemon here, with the fix in the message,
    rather than turning every tool call into an opaque 500.
    """
    for label, conn in conns.items():
        box = {}

        def probe(conn=conn, box=box):
            try:
                conn.execute("SELECT 1").fetchone()
            except BaseException as exc:  # noqa: BLE001 - re-raised below with context
                box["error"] = exc

        t = threading.Thread(target=probe)
        t.start()
        t.join()
        if "error" in box:
            raise RuntimeError(
                f"{label} connection cannot be used from a worker thread "
                f"({type(box['error']).__name__}: {box['error']}); open it with "
                "check_same_thread=False — see whatsvault.db.connection.open_db"
            ) from box["error"]


def build_oauth_app(inner, control_conn, token, *, public_url=None, audit_key=None):
    """Wrap an already-built MCP application in the auth surface.

    public_url=None is the loopback deployment: a static bearer token and no
    authorization server at all, so a local vault gains no attack surface from a
    feature it is not using. Passing a URL mounts the OAuth endpoints and lets
    the gate additionally accept the access tokens they issue.
    """
    if not public_url:
        return BearerAuthMiddleware(inner, token)
    base = str(public_url).rstrip("/")
    if not base.startswith("https://"):
        # The whole flow moves bearer credentials over this origin.
        raise ValueError(f"public_url must be https, got {public_url!r}")
    guarded = BearerAuthMiddleware(
        inner,
        token,
        control_conn=control_conn,
        resource_metadata_url=f"{base}/.well-known/oauth-protected-resource",
    )
    return PublicRouter(OAuthApp(control_conn, base, audit_key=audit_key), guarded)


def build_app(vault_conn, control_conn, token, audit_key, *, name="whatsvault", port=PORT, public_url=None):
    """The full ASGI app: auth surface in front of the Streamable-HTTP transport."""
    assert_usable_from_worker_threads(vault=vault_conn, control=control_conn)
    mcp_server = build_mcp_server(vault_conn, control_conn, audit_key, name=name)
    inner = mcp_server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        transport_security=transport_security_settings(port, public_url),
        host=HOST,
    )
    return build_oauth_app(inner, control_conn, token, public_url=public_url, audit_key=audit_key)


BLOCKED_ON = "keys_not_provisioned"
DETAIL = (
    "whatsvault.mcp.token.v1 / whatsvault.mcp.audit.v1 are absent from the "
    "Keychain; run `whatsvault mcp-provision`"
)


def preflight(ks) -> dict | None:
    """Return a blocked record if the daemon cannot serve, else None.

    Raising here would exit non-zero and, under KeepAlive, restart forever. A
    stated precondition should be reported once and then stop.
    """
    from whatsvault.crypto import keystore
    from whatsvault.ops import structlog

    try:
        ks.require(auth_mod.TOKEN_KEY_NAME, 32)
        ks.require(audit.AUDIT_KEY_NAME, 32)
    except keystore.KeyMissing:
        return structlog.event(
            {"service": "mcp", "status": "not_started", "blocked_on": BLOCKED_ON, "detail": DETAIL}
        )
    return None


def main():  # pragma: no cover - process entrypoint; see test_main_symbols_resolve
    """Serve the loopback MCP. Mirrors cli.main's production wiring exactly."""
    import json
    import os

    import uvicorn

    from whatsvault.crypto.keystore import KeyringKeyStore
    from whatsvault.ops import daemon, structlog

    vault_conn, control_conn, blocked = daemon.open_databases("mcp", check_same_thread=False)
    if blocked is not None:
        print(json.dumps(blocked))
        return 0
    ks = KeyringKeyStore()
    blocked = preflight(ks)
    if blocked is not None:
        print(json.dumps(blocked))
        return 0
    token = ks.require(auth_mod.TOKEN_KEY_NAME, 32).hex()
    audit_key = ks.require(audit.AUDIT_KEY_NAME, 32)
    # Set only for a deployment behind a public URL. Absent -- the default, and
    # how the local connectors talk to this vault -- no authorization server is
    # mounted at all.
    public_url = os.environ.get(PUBLIC_URL_ENV) or None
    app = build_app(vault_conn, control_conn, token, audit_key, public_url=public_url)
    if public_url:
        print(json.dumps(structlog.event({"service": "mcp", "status": "public", "issuer": public_url})))
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":  # pragma: no cover - `python -m apps.mcp.server`
    import sys

    sys.exit(main())
