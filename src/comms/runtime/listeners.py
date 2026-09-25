"""Three isolated listeners, each its own ASGI app on its own socket (comms v0.3 Task D34;
A36, G18).

| listener | routes | authentication |
|---|---|---|
| local   | ``/mcp`` | ``cml1`` |
| remote  | ``/mcp``, the protected-resource and authorization-server metadata, ``/authorize``, ``/token`` | OAuth |
| webhook | ``/webhooks/meta`` | Meta signature |

Each app answers only its exact paths; every other path is 404 before anything else runs, so
the webhook listener has no route to MCP dispatch and neither MCP listener has a webhook
route. The protected-resource metadata is served at both RFC 9728 forms (path-inserted
``/.well-known/oauth-protected-resource/mcp``, which MCP clients probe first, and the root).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from mcp.server.auth.routes import create_protected_resource_routes
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route

from comms.mcp.dispatch import AuthenticatedClient, Dispatcher
from comms.mcp.http import build_http_app, oauth_authenticator
from comms.mcp.oauth.server import SCOPE, Built, OAuthSettings

__all__ = ["REMOTE_PATHS", "Listeners", "RemoteListener", "build_listeners"]

REMOTE_PATHS = (
    "/mcp",
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
    "/.well-known/oauth-authorization-server",
    "/authorize",
    "/token",
)
_NOT_FOUND = Response(b'{"error":"not_found"}', status_code=404, media_type="application/json")


class _ExactPaths:
    """An ASGI router over exact paths; anything else is 404 before any app runs."""

    def __init__(self, table: Mapping[str, Any]) -> None:
        self._table = dict(table)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":  # the MCP app's session manager needs its lifespan
            await self._table.get("/mcp", _lifespan_noop)(scope, receive, send)
            return
        app = self._table.get(scope.get("path", "")) if scope["type"] == "http" else None
        if app is None:
            await _NOT_FOUND(scope, receive, send)
            return
        await app(scope, receive, send)


async def _lifespan_noop(scope: Any, receive: Any, send: Any) -> None:
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


@dataclass(frozen=True)
class RemoteListener:
    oauth: Built
    settings: OAuthSettings
    client_ref: str
    port: int


@dataclass(frozen=True)
class Listeners:
    local: Any
    remote: Any | None = None
    webhook: Any | None = None
    paths: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


def _remote_app(dispatcher: Dispatcher, remote: RemoteListener, host: str) -> Any:
    public = urlparse(remote.settings.issuer)
    mcp = build_http_app(
        dispatcher,
        oauth_authenticator(remote.oauth, remote.client_ref),
        host=host,
        port=remote.port,
        allowed_hosts=(f"{host}:{remote.port}", public.netloc),
        allowed_origins=(f"http://{host}:{remote.port}", f"{public.scheme}://{public.netloc}"),
    )
    resource_routes = create_protected_resource_routes(
        AnyHttpUrl(remote.settings.resource),
        [AnyHttpUrl(remote.settings.issuer)],
        scopes_supported=[SCOPE],
    )
    metadata = resource_routes[0].endpoint
    oauth_app = Starlette(
        routes=[
            *remote.oauth.routes,
            *resource_routes,
            Route("/.well-known/oauth-protected-resource", metadata, methods=["GET", "OPTIONS"]),
        ]
    )
    table = {path: oauth_app for path in REMOTE_PATHS if path != "/mcp"}
    table["/mcp"] = mcp
    return _ExactPaths(table)


def build_listeners(
    dispatcher: Dispatcher,
    *,
    local_authenticate: Callable[[str], AuthenticatedClient | None],
    host: str,
    local_port: int,
    remote: RemoteListener | None = None,
    webhook: Any | None = None,
) -> Listeners:
    local = _ExactPaths(
        {"/mcp": build_http_app(dispatcher, local_authenticate, host=host, port=local_port)}
    )
    return Listeners(
        local=local,
        remote=_remote_app(dispatcher, remote, host) if remote is not None else None,
        webhook=_ExactPaths({"/webhooks/meta": webhook}) if webhook is not None else None,
    )
