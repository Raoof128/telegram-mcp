"""HTTP-transport bearer auth for the Streamable-HTTP MCP surface (ledger #18, #19).

Why this exists as ASGI middleware rather than a tool argument: a `bearer`
parameter on a tool handler is published in that tool's JSON schema, so the
server would be advertising the secret as something the *model* should supply.
The token belongs on the transport, checked before any tool is reached.

Default-deny: every path is protected. 127.0.0.1 is not an auth boundary (any
local process running as the user can connect), so binding loopback does not
substitute for this check.
"""

import time

from . import auth, oauth, oauth_http

_UNAUTHORISED = b'{"error":"unauthorized"}'
_NOT_FOUND = b'{"error":"not_found"}'

# RFC 9728 / RFC 8414 discovery lives under this prefix. This server authenticates
# with a static bearer token and is not an OAuth authorization server, so the
# truthful answer is 404. Returning the gate's 401 instead was actively harmful:
# clients parse the body as protected-resource metadata, and OpenAI's
# tunnel-client reported "invalid metadata ... missing resource" and held
# readiness degraded.
#
# This is not a hole in the default-deny gate. The middleware answers the prefix
# ITSELF and never forwards it, so no request shape under it can reach the app --
# including one that tries to climb back out with `..`.
# `/.well-known/` and `/oauth/` belong to the authorization server, which only a
# public deployment mounts. On loopback neither exists, and the truthful answer
# is 404 -- not 401, which would imply an endpoint that is merely locked.
_UNMOUNTED_PREFIXES = ("/.well-known/", "/oauth/")


class BearerAuthMiddleware:
    """Rejects any HTTP request not carrying `Authorization: Bearer <token>`."""

    def __init__(self, app, token: str, *, control_conn=None, resource_metadata_url=None):
        if not token:
            raise ValueError("refusing to serve without a token; see mcp.auth.provision_token")
        self._app = app
        self._token = token
        # Public deployments additionally accept an OAuth access token. The
        # static token is NOT disabled by this: the local operator keeps working.
        self._control = control_conn
        self._resource_metadata_url = resource_metadata_url

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self._app(scope, receive, send)
        if scope["type"] != "http":
            # No websocket surface is offered; fail closed rather than pass through.
            raise RuntimeError(f"unsupported ASGI scope {scope['type']!r}")
        raw = scope.get("raw_path") or str(scope.get("path", "")).encode()
        path = raw.decode("latin-1").split("?", 1)[0]
        if path.startswith(_UNMOUNTED_PREFIXES) or str(scope.get("path", "")).startswith(_UNMOUNTED_PREFIXES):
            # Answered here, never proxied, and identical with or without a token:
            # which endpoints this deployment does not serve is not a secret.
            return await self._not_found(send)
        if not self._authorised(scope):
            return await self._reject(send)
        return await self._app(scope, receive, send)

    def _authorised(self, scope) -> bool:
        return self._presented(scope) is not None

    def _presented(self, scope):
        """Return the credential kind that authorises this request, or None."""
        found = [v for k, v in (scope.get("headers") or ()) if k.lower() == b"authorization"]
        # Ambiguous credentials fail closed rather than first-wins: an injected or
        # proxy-appended second header must never be silently ignored.
        if len(found) != 1:
            return None
        scheme, _, provided = found[0].decode("latin-1").partition(" ")
        if scheme.lower() != "bearer":
            return None
        presented = provided.strip()
        if auth.require_token(presented, self._token):
            return "static"
        # Only a public deployment has a control connection here, so a loopback
        # server cannot be talked into accepting an OAuth token it never issued.
        if self._control is not None:
            now_ms = int(time.time() * 1000)
            if oauth.validate_access_token(self._control, presented, now_ms) is not None:
                return "oauth"
        return None

    async def _reject(self, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                # RFC 6750: tell the client how to authenticate, without echoing input.
                "headers": [
                    (b"www-authenticate", self._challenge()),
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_UNAUTHORISED)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _UNAUTHORISED})

    def _challenge(self) -> bytes:
        """RFC 6750 challenge. On a public deployment it also carries the RFC 9728
        `resource_metadata` pointer, which is how an MCP client discovers the
        authorization server; without it ChatGPT cannot begin the flow."""
        challenge = b'Bearer realm="whatsvault-mcp"'
        if self._resource_metadata_url:
            url = str(self._resource_metadata_url).replace('"', "")
            challenge += b', resource_metadata="' + url.encode("latin-1") + b'"'
        return challenge

    @staticmethod
    async def _not_found(send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 404,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_NOT_FOUND)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _NOT_FOUND})


class PublicRouter:
    """Puts the authorization server in front of the guarded MCP application.

    An authorization server must answer without a token — that is what it is —
    so this introduces the first unauthenticated region on the surface. It is
    made safe by ownership rather than by exemption: `oauth_app` answers every
    path under its prefixes ITSELF and this router never forwards one onward, so
    there is no request shape under `/oauth/` or `/.well-known/` that reaches a
    tool, including one that tries to climb back out with `..`.
    """

    def __init__(self, oauth_app, guarded_app):
        self._oauth = oauth_app
        self._guarded = guarded_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self._guarded(scope, receive, send)
        if scope["type"] != "http":
            raise RuntimeError(f"unsupported ASGI scope {scope['type']!r}")
        # Matched on the RAW path: a normalising router that resolved `..` first
        # could route `/oauth/../mcp` here and then hand a rewritten path to the
        # MCP app. Nothing under the prefix is forwarded, so it cannot.
        raw = scope.get("raw_path") or str(scope.get("path", "")).encode()
        path = raw.decode("latin-1").split("?", 1)[0]
        if oauth_http.owns(path) or oauth_http.owns(str(scope.get("path", ""))):
            return await self._oauth(scope, receive, send)
        return await self._guarded(scope, receive, send)
