"""The OAuth 2.1 authorization server (comms v0.3 Task D32; A35, G18).

Built on the SDK's ``server/auth``: its metadata and token handlers over a Comms provider, and
its authorize handler behind one owner check. The baseline registration is **one predefined
client**; nothing registers dynamically and nothing is revoked over HTTP.

- **Owner approval:** ``/authorize`` issues a code only with a one-time, 5-minute owner code
  (``comms oauth approve`` prints it locally); without one it answers ``access_denied``.
- **Codes:** 32 random bytes, 60 s, single use, bound to the S256 challenge, the exact redirect
  URI, the client, the resource and the scope (plain PKCE is refused).
- **Access tokens:** EdDSA JWS, 10 minutes: ``iss, sub, aud, resource, scope, exp, iat, jti,
  sec``. ``verify`` refuses another subject, audience, resource or scope, an expired token, a
  bumped security epoch and a disabled client.
- **Refresh tokens:** opaque, stored hashed, rotated on every use; a reuse revokes the family.
- **Resource:** ``/token`` requires ``resource`` and it must be this server's (RFC 8707).
"""

from __future__ import annotations

import base64
import os
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from comms.core.auth import oauth_refresh
from comms.core.keys.slots import KeySlotStore, load_active
from comms.core.security import security_epoch
from comms.mcp.oauth.store import CodeStore, OwnerApprovals
from comms.mcp.oauth.tokens import public_key_of, sign_access_token, verify_access_token

__all__ = ["ACCESS_TTL", "SCOPE", "Built", "OAuthSettings", "build_oauth"]

SCOPE = "comms.full_admin"
ACCESS_TTL = timedelta(minutes=10)
_APPROVED: ContextVar[bool] = ContextVar("comms_oauth_owner_approved", default=False)
_CLAIMS = frozenset({"iss", "sub", "aud", "resource", "scope", "exp", "iat", "jti", "sec"})


@dataclass(frozen=True)
class OAuthSettings:
    issuer: str
    resource: str
    client_id: str
    redirect_uris: tuple[str, ...]
    owner: str


class _Provider:
    def __init__(
        self,
        conn: Any,
        store: KeySlotStore,
        settings: OAuthSettings,
        *,
        clock: Callable[[], datetime],
        client_enabled: Callable[[str], bool],
        approvals: OwnerApprovals,
    ) -> None:
        self._conn, self._store, self._s = conn, store, settings
        self._clock, self._enabled, self.approvals = clock, client_enabled, approvals
        self._codes = CodeStore(clock)

    def __repr__(self) -> str:
        return "CommsOAuthProvider(<redacted>)"

    # -- clients ---------------------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if client_id != self._s.client_id or not self._enabled(client_id):
            return None
        return OAuthClientInformationFull(
            client_id=client_id,
            redirect_uris=[AnyUrl(u) for u in self._s.redirect_uris],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=SCOPE,
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        raise NotImplementedError("the baseline registration is one predefined client")

    # -- authorization codes -----------------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if not _APPROVED.get():
            raise AuthorizeError(error="access_denied", error_description="owner approval required")
        if params.resource != self._s.resource:
            raise AuthorizeError(error="invalid_request", error_description="resource required")
        if set(params.scopes or [SCOPE]) != {SCOPE}:
            raise AuthorizeError(error="invalid_scope", error_description="unknown scope")
        code = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
        self._codes.put(
            code,
            AuthorizationCode(
                code=code,
                scopes=[SCOPE],
                expires_at=(self._clock() + timedelta(seconds=60)).timestamp(),
                client_id=client.client_id or "",
                code_challenge=params.code_challenge,
                redirect_uri=params.redirect_uri,
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                resource=params.resource,
                subject=self._s.owner,
            ),
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        found = self._codes.get(authorization_code)
        if found is None or found.client_id != client.client_id:
            return None
        return found  # type: ignore[no-any-return]

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if self._codes.spend(authorization_code.code) is None:
            raise TokenError(error="invalid_grant", error_description="authorization code is spent")
        return self._issue(client.client_id or "", family=None)

    # -- refresh tokens --------------------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        row = oauth_refresh.peek(self._conn, refresh_token, now=self._clock())
        if row is None or row.client_id != client.client_id or not self._enabled(row.client_id):
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row.client_id,
            scopes=list(row.scopes),
            expires_at=int(row.expires_at.timestamp()),
            resource=row.resource,
            subject=row.subject,
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        if not set(scopes) <= set(refresh_token.scopes):
            raise TokenError(error="invalid_scope", error_description="scope cannot grow")
        row = oauth_refresh.consume(self._conn, refresh_token.token, now=self._clock())
        if row is None:
            raise TokenError(error="invalid_grant", error_description="refresh token is spent")
        return self._issue(row.client_id, family=row.family)

    # -- access tokens ---------------------------------------------------------------------

    def _issue(self, client_id: str, *, family: str | None) -> OAuthToken:
        now = self._clock()
        iat = int(now.timestamp())
        claims = {
            "iss": self._s.issuer,
            "sub": self._s.owner,
            "aud": self._s.resource,
            "resource": self._s.resource,
            "scope": SCOPE,
            "exp": iat + int(ACCESS_TTL.total_seconds()),
            "iat": iat,
            "jti": base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode(),
            "sec": security_epoch(self._conn),
        }
        seed, _key_id = load_active(self._conn, self._store, "oauth-signing-key")
        refresh, _family = oauth_refresh.issue(
            self._conn,
            family=family,
            client_id=client_id,
            subject=self._s.owner,
            scopes=(SCOPE,),
            resource=self._s.resource,
            now=now,
        )
        return OAuthToken(
            access_token=sign_access_token(seed, claims),
            token_type="Bearer",
            expires_in=int(ACCESS_TTL.total_seconds()),
            scope=SCOPE,
            refresh_token=refresh,
        )

    def verify(self, token: str) -> dict[str, Any] | None:
        """The claims of a currently valid access token for this resource, or None."""
        seed, _key_id = load_active(self._conn, self._store, "oauth-signing-key")
        claims = verify_access_token(public_key_of(seed), token)
        if claims is None or set(claims) != _CLAIMS:
            return None
        now = int(self._clock().timestamp())
        valid = (
            claims["iss"] == self._s.issuer
            and claims["sub"] == self._s.owner
            and claims["aud"] == self._s.resource
            and claims["resource"] == self._s.resource
            and claims["scope"] == SCOPE
            and type(claims["exp"]) is int
            and type(claims["iat"]) is int
            and claims["iat"] <= now + 30
            and now < claims["exp"]
            and claims["sec"] == security_epoch(self._conn)
            and self._enabled(self._s.client_id)
        )
        return claims if valid else None

    async def load_access_token(self, token: str) -> AccessToken | None:
        claims = self.verify(token)
        if claims is None:
            return None
        return AccessToken(
            token=token,
            client_id=self._s.client_id,
            scopes=[claims["scope"]],
            expires_at=claims["exp"],
            resource=claims["resource"],
            subject=claims["sub"],
        )

    async def revoke_token(self, token: Any) -> None:
        raise NotImplementedError("revocation is by security epoch or client disable")


@dataclass(frozen=True)
class Built:
    routes: list[Route]
    approvals: OwnerApprovals
    provider: _Provider = field(repr=False)

    def __repr__(self) -> str:
        return "OAuth(<redacted>)"

    def verify(self, token: str) -> dict[str, Any] | None:
        return self.provider.verify(token)


class _ResourceRequired:
    """``/token`` must name this resource (RFC 8707); the body is replayed to the SDK handler.
    A class, so Starlette mounts it as an ASGI app rather than a request handler."""

    def __init__(self, app: Any, resource: str) -> None:
        self._app, self._resource = app, resource

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self._app(scope, receive, send)
            return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        form = parse_qs(body.decode("utf-8", "replace"))
        if form.get("resource") != [self._resource]:
            refused = Response(
                b'{"error":"invalid_target"}', status_code=400, media_type="application/json"
            )
            await refused(scope, receive, send)
            return
        replayed = False

        async def replay() -> Any:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        await self._app(scope, replay, send)


def build_oauth(
    conn: Any,
    store: KeySlotStore,
    settings: OAuthSettings,
    *,
    clock: Callable[[], datetime],
    client_enabled: Callable[[str], bool],
) -> Built:
    approvals = OwnerApprovals(clock)
    provider = _Provider(
        conn, store, settings, clock=clock, client_enabled=client_enabled, approvals=approvals
    )
    sdk_routes = create_auth_routes(
        provider,  # type: ignore[arg-type]
        issuer_url=AnyHttpUrl(settings.issuer),
        client_registration_options=ClientRegistrationOptions(enabled=False),
        revocation_options=RevocationOptions(enabled=False),
    )
    authorize = AuthorizationHandler(provider)  # type: ignore[arg-type]

    async def authorize_endpoint(request: Request) -> Response:
        source = request.query_params if request.method == "GET" else await request.form()
        token = _APPROVED.set(approvals.consume(source.get("owner_code")))
        try:
            return await authorize.handle(request)
        finally:
            _APPROVED.reset(token)

    routes: list[Route] = []
    for route in sdk_routes:
        if route.path == "/authorize":
            routes.append(Route("/authorize", authorize_endpoint, methods=["GET", "POST"]))
        elif route.path == "/token":
            routes.append(
                Route("/token", _ResourceRequired(route.app, settings.resource), methods=["POST"])
            )
        else:
            routes.append(route)
    return Built(routes=routes, approvals=approvals, provider=provider)
