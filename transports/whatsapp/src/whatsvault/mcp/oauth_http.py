"""ASGI surface for the OAuth 2.1 authorization server (RFC 8414, 9728, 7591).

Mounted only when the operator deploys behind a public URL. On loopback nothing
here is reachable, so a local vault gains no attack surface from a feature it is
not using.

The router is deliberately explicit rather than a framework: every path this
module answers is listed, matched exactly, and answered HERE. Nothing under
`/oauth/` or `/.well-known/` is ever forwarded to the MCP application, so no
request shape — including one that climbs back out with `..` — can use the
unauthenticated region to reach a tool.
"""

import html
import json
import time
import unicodedata
import urllib.parse

from . import oauth

_JSON = [(b"content-type", b"application/json")]
_HTML = [(b"content-type", b"text/html; charset=utf-8")]
_NO_STORE = [(b"cache-control", b"no-store"), (b"pragma", b"no-cache")]

# Defence in depth for the one page a human looks at. Even a rendering mistake
# should not be able to send the authorization code to another origin, and the
# code must never ride out in a Referer header.
SECURITY_HEADERS = [
    (
        b"content-security-policy",
        b"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        b"img-src 'none'; connect-src 'self'; base-uri 'none'; form-action 'none'; "
        b"frame-ancestors 'none'",
    ),
    (b"referrer-policy", b"no-referrer"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
]

# Every path this module owns. Membership is by exact match on the *raw* path,
# and anything else under these prefixes is a 404 from here rather than a
# fall-through to the MCP app.
_PREFIXES = ("/oauth/", "/.well-known/")


def owns(path: str) -> bool:
    return str(path or "").startswith(_PREFIXES)


async def _respond(send, status, headers, body: bytes):
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                *headers,
                *_NO_STORE,
                *SECURITY_HEADERS,
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _json(send, status, payload):
    await _respond(send, status, _JSON, json.dumps(payload).encode())


async def _error(send, status, code, description=""):
    await _json(send, status, {"error": code, "error_description": description})


async def _read_body(receive) -> bytes:
    chunks, more = [], True
    while more:
        msg = await receive()
        chunks.append(msg.get("body", b"") or b"")
        more = msg.get("more_body", False)
        if sum(len(c) for c in chunks) > 64 * 1024:  # nothing legitimate is this big
            break
    return b"".join(chunks)


class OAuthApp:
    """Handles the authorization-server endpoints; owns its paths completely."""

    def __init__(self, control_conn, public_url: str, *, now=None, audit_key=None):
        self._control = control_conn
        self._audit_key = audit_key
        self._base = str(public_url).rstrip("/")
        self._now = now or (lambda: int(time.time() * 1000))

    # ---- metadata ---------------------------------------------------------
    def _as_metadata(self) -> dict:
        return {
            "issuer": self._base,
            "authorization_endpoint": f"{self._base}/oauth/authorize",
            "token_endpoint": f"{self._base}/oauth/token",
            "registration_endpoint": f"{self._base}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            # S256 only. Advertising `plain` would invite a client to use it.
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": [oauth.READ_ONLY_SCOPE],
        }

    def _pr_metadata(self) -> dict:
        return {
            "resource": f"{self._base}/mcp",
            "authorization_servers": [self._base],
            "scopes_supported": [oauth.READ_ONLY_SCOPE],
            "bearer_methods_supported": ["header"],
        }

    # ---- dispatch ---------------------------------------------------------
    async def __call__(self, scope, receive, send):
        raw = scope.get("raw_path") or scope.get("path", "").encode()
        path = raw.decode("latin-1").split("?", 1)[0]
        method = scope.get("method", "GET").upper()
        query = dict(urllib.parse.parse_qsl(scope.get("query_string", b"").decode("latin-1")))

        routes = {
            ("GET", "/.well-known/oauth-authorization-server"): self._get_as_metadata,
            ("GET", "/.well-known/oauth-protected-resource"): self._get_pr_metadata,
            ("GET", "/.well-known/oauth-protected-resource/mcp"): self._get_pr_metadata,
            ("POST", "/oauth/register"): self._post_register,
            ("GET", "/oauth/authorize"): self._get_authorize,
            ("GET", "/oauth/poll"): self._get_poll,
            ("POST", "/oauth/token"): self._post_token,
        }
        handler = routes.get((method, path))
        if handler is None:
            # Wrong method on a known path is 405; anything else under our
            # prefixes is 404 — never a fall-through.
            known = any(p == path for _, p in routes)
            return await _error(send, 405 if known else 404, "not_found")
        return await handler(scope, receive, send, query)

    async def _get_as_metadata(self, scope, receive, send, query):
        await _json(send, 200, self._as_metadata())

    async def _get_pr_metadata(self, scope, receive, send, query):
        await _json(send, 200, self._pr_metadata())

    # ---- registration -----------------------------------------------------
    async def _post_register(self, scope, receive, send, query):
        try:
            payload = json.loads(await _read_body(receive) or b"")
        except (ValueError, UnicodeDecodeError):
            await _error(send, 400, "invalid_client_metadata", "body must be JSON")
            return
        if not isinstance(payload, dict):
            await _error(send, 400, "invalid_client_metadata", "body must be a JSON object")
            return
        uris = payload.get("redirect_uris")
        if not isinstance(uris, list):
            await _error(send, 400, "invalid_redirect_uri", "redirect_uris must be a list")
            return
        try:
            client = oauth.register_client(
                self._control,
                client_name=payload.get("client_name"),
                redirect_uris=uris,
                now_ms=self._now(),
            )
        except oauth.OAuthError as exc:
            await _error(send, 400, exc.code, exc.description)
            return
        await _json(
            send,
            201,
            {
                "client_id": client["client_id"],
                "client_name": client["client_name"],
                "redirect_uris": client["redirect_uris"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
        )

    # ---- authorize --------------------------------------------------------
    async def _get_authorize(self, scope, receive, send, query):
        if query.get("response_type") != "code":
            await _error(send, 400, "unsupported_response_type")
            return
        try:
            pending = oauth.begin_authorization(
                self._control,
                client_id=query.get("client_id"),
                redirect_uri=query.get("redirect_uri"),
                code_challenge=query.get("code_challenge"),
                code_challenge_method=query.get("code_challenge_method"),
                state=query.get("state"),
                scope=query.get("scope"),
                now_ms=self._now(),
            )
        except oauth.OAuthError as exc:
            # Rendered here, never redirected. Bouncing an error to an
            # unvalidated redirect_uri IS the open redirect.
            await _error(send, 400, exc.code, exc.description)
            return
        await _respond(send, 200, _HTML, _consent_page(pending).encode("utf-8"))

    async def _get_poll(self, scope, receive, send, query):
        got = oauth.poll(self._control, request_id=query.get("request_id"), now_ms=self._now())
        if got is None:
            await _json(send, 200, {"ready": False})
            return
        params = {"code": got["code"]}
        if got["state"] is not None:
            params["state"] = got["state"]
        sep = "&" if "?" in got["redirect_uri"] else "?"
        redirect = got["redirect_uri"] + sep + urllib.parse.urlencode(params)
        await _json(send, 200, {"ready": True, "redirect": redirect})

    # ---- token ------------------------------------------------------------
    async def _post_token(self, scope, receive, send, query):
        form = dict(urllib.parse.parse_qsl((await _read_body(receive)).decode("latin-1")))
        grant = form.get("grant_type")
        try:
            if grant == "authorization_code":
                granted = oauth.exchange_code(
                    self._control,
                    code=form.get("code"),
                    client_id=form.get("client_id"),
                    redirect_uri=form.get("redirect_uri"),
                    code_verifier=form.get("code_verifier"),
                    now_ms=self._now(),
                    audit_key=self._audit_key,
                )
            elif grant == "refresh_token":
                granted = oauth.refresh(
                    self._control,
                    refresh_token=form.get("refresh_token"),
                    client_id=form.get("client_id"),
                    now_ms=self._now(),
                    audit_key=self._audit_key,
                )
            else:
                await _error(send, 400, "unsupported_grant_type")
                return
        except oauth.OAuthError as exc:
            await _error(send, 400, exc.code, exc.description)
            return
        await _json(send, 200, granted)


def _safe_name(name) -> str:
    """Render an attacker-supplied client name safely.

    Two separate hazards. HTML escaping stops it becoming markup on a page served
    from the vault's own origin. Stripping bidi and zero-width controls stops it
    *lying to the operator* — a right-to-left override can make a hostile name
    read as a familiar one in the very sentence asking for consent, which is the
    same class of vector tests/test_display_guard.py exists to contain.
    """
    text = str(name or "An application")
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return html.escape(text, quote=True) or "An application"


def _consent_page(pending: dict) -> str:
    """No form, no password field, nothing to submit.

    The page states what is being asked for and how to approve it from the
    terminal, then polls. A public page that accepted a secret would be a
    phishing target and a brute-force target; this one has nothing to steal.
    """
    client = _safe_name(pending.get("client_name"))
    return f"""<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WhatsVault — approve access</title>
<style>
 body{{font:16px/1.6 -apple-system,system-ui,sans-serif;max-width:34rem;
       margin:4rem auto;padding:0 1.25rem;color:#111}}
 code{{background:#f4f4f5;padding:.15em .4em;border-radius:4px}}
 .code{{font:700 2rem/1.2 ui-monospace,Menlo,monospace;letter-spacing:.12em;
        background:#f4f4f5;padding:1rem;border-radius:8px;text-align:center;margin:1.5rem 0}}
 .muted{{color:#666;font-size:.9rem}}
 @media(prefers-color-scheme:dark){{body{{background:#111;color:#eee}}code,.code{{background:#1e1e21}}}}
</style>
<h1>Approve access</h1>
<p><strong>{client}</strong> is asking to <strong>read</strong> your WhatsVault
archive. It cannot send messages, and it will never see a full phone number.</p>
<div class="code" data-request-id="{pending["request_id"]}">{pending["user_code"]}</div>
<p>This page will not ask you for a password. To approve, run this in a terminal
on the machine holding the vault:</p>
<p><code>whatsvault oauth-approve --code {pending["user_code"]}</code></p>
<p class="muted">The request expires in 10 minutes. If you did not start this,
close this page and do nothing — nothing is granted until you approve it.</p>
<p id="s" class="muted">Waiting for approval…</p>
<script>
 const id = document.querySelector('[data-request-id]').dataset.requestId;
 setInterval(async () => {{
   const r = await fetch('/oauth/poll?request_id=' + encodeURIComponent(id));
   const j = await r.json();
   if (j.ready) {{ document.getElementById('s').textContent = 'Approved — returning…';
                   location.href = j.redirect; }}
 }}, 2000);
</script>
"""
