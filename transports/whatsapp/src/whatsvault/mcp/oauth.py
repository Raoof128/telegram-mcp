"""OAuth 2.1 authorization server for the public-connector deployment.

Only needed when the MCP surface is reachable from outside this machine, because
ChatGPT's connector dialog offers OAuth, No Authentication, or Mixed — a static
bearer token fits none of them. On loopback the static token remains the
mechanism and none of this runs.

Going public moves the boundary. On 127.0.0.1, reaching the port already meant
running as the user; behind a public URL this module IS the boundary, and every
decision here is load-bearing.

The design decision worth stating: **the consent page never accepts a secret.**
An authorization request produces a short user code, and the operator approves it
from the terminal with `whatsvault oauth-approve`. A public form asking for a
password would be a phishing and brute-force target, and this project already
holds that approval belongs on a channel the requester cannot reach — the same
reason sending requires the phone's Secure Enclave.

Nothing issued here widens authority. The granted scope is a constant, the
schema CHECKs it, and the MCP surface behind it is the same read-only set of
tools with the same redaction and the same LOCAL_ONLY fence.
"""

import base64
import hashlib
import hmac
import json
import secrets

# The only scope this server issues. A client asking for more gets this.
READ_ONLY_SCOPE = "whatsvault.read"

# An authorization code is a bearer credential in a URL: in browser history, in
# a Referer header, in any proxy log along the way. It lives just long enough to
# be redeemed.
CODE_TTL_MS = 60_000
# How long the operator has to walk to the terminal and approve.
PENDING_TTL_MS = 600_000
ACCESS_TTL_MS = 3_600_000
REFRESH_TTL_MS = 30 * 24 * 3_600_000

# Every endpoint below is reachable by anyone once the vault is published, so
# every attacker-controlled field is bounded. Unbounded, a loop of authorize
# calls writes as much as the caller likes into the operator's database.
MAX_STATE_LEN = 512
MAX_CHALLENGE_LEN = 128  # RFC 7636: a base64url S256 challenge is 43 chars
MIN_CHALLENGE_LEN = 43
MAX_REDIRECT_LEN = 512
MAX_REDIRECT_URIS = 10
MAX_CLIENT_NAME_LEN = 200
# Live unapproved requests. Bounds the disk cost of a flood, and bounds how many
# codes an operator could be socially engineered into approving.
MAX_PENDING = 25

# Unambiguous alphabet: no O/0, I/1, or S/5 to misread off a screen.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRTUVWXYZ2346789"
_CODE_LEN = 10  # 30^10 > 2^49; paired with a 10-minute TTL and single use


class OAuthError(Exception):
    """Refusal. The HTTP layer maps this to an RFC 6749 error response, and the
    message is deliberately terse: a caller learns that it failed, not which
    check it failed, so failures cannot be used to enumerate state."""

    def __init__(self, code: str, description: str = ""):
        super().__init__(description or code)
        self.code = code
        self.description = description


def _audit(control_conn, key, tool: str, outcome: str, args: dict, now_ms) -> None:
    """Record a grant event.

    Tool calls were audited but the grant that authorised them was not, so after
    an incident there was no answer to "who was given access, and when".

    The key is passed explicitly rather than carried on the connection: a
    sqlcipher3 connection has no __dict__, and threading it through makes it
    visible at every call site which operations are accountable.
    """
    if key is None:  # a deployment that never enabled auditing
        return
    from . import audit as _audit_mod

    # args are hashed by audit.record, never stored: they must never carry the
    # credential itself.
    _audit_mod.record(
        control_conn, key, actor="oauth", tool=tool, args=args, outcome=outcome, now_ms=int(now_ms)
    )


def _bounded(value, limit: int, field: str) -> str:
    text = "" if value is None else str(value)
    if len(text) > limit:
        raise OAuthError("invalid_request", f"{field} exceeds {limit} characters")
    return text


def _hash(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _new_secret() -> str:
    return secrets.token_urlsafe(48)


def _new_user_code() -> str:
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))
    return f"{body[:5]}-{body[5:]}"


# ---- clients ------------------------------------------------------------------
def register_client(control_conn, *, client_name, redirect_uris, now_ms) -> dict:
    """RFC 7591 dynamic client registration.

    Open registration is intentional and safe here: registering a client grants
    nothing. Every request still waits for an out-of-band approval, so the worst
    an attacker achieves is a row in a table and a code nobody types.
    """
    uris = [str(u) for u in (redirect_uris or [])]
    if not uris:
        raise OAuthError("invalid_redirect_uri", "at least one redirect_uri is required")
    if len(uris) > MAX_REDIRECT_URIS:
        raise OAuthError("invalid_redirect_uri", f"at most {MAX_REDIRECT_URIS} redirect_uris")
    for uri in uris:
        _bounded(uri, MAX_REDIRECT_LEN, "redirect_uri")
        # https only. A cleartext redirect hands the authorization code to
        # anyone on the path, and PKCE does not protect the code in transit.
        if not uri.startswith("https://"):
            raise OAuthError("invalid_redirect_uri", "redirect_uri must be https")
        if "#" in uri:
            raise OAuthError("invalid_redirect_uri", "redirect_uri must not carry a fragment")
    client_id = "wvc_" + secrets.token_urlsafe(16)
    control_conn.execute(
        "INSERT INTO oauth_clients(client_id, client_name, redirect_uris, created_ms) VALUES(?,?,?,?)",
        (client_id, str(client_name or "")[:MAX_CLIENT_NAME_LEN], json.dumps(uris), int(now_ms)),
    )
    control_conn.commit()
    # A public client: no secret is issued, because a browser-based client cannot
    # keep one. PKCE is what proves the exchange comes from the same requester.
    return {"client_id": client_id, "redirect_uris": uris, "client_name": client_name}


def _client(control_conn, client_id):
    row = control_conn.execute(
        "SELECT client_id, client_name, redirect_uris FROM oauth_clients WHERE client_id=?",
        (str(client_id or ""),),
    ).fetchone()
    if not row:
        raise OAuthError("invalid_client", "unknown client")
    return row


def _check_redirect(row, redirect_uri) -> str:
    """Exact string match against a registered URI.

    Not prefix, not normalised, not case-insensitive: every one of those turns
    this endpoint into an open redirect that hands authorization codes to an
    attacker-chosen host.
    """
    registered = json.loads(row["redirect_uris"])
    provided = str(redirect_uri or "")
    if not any(hmac.compare_digest(provided, known) for known in registered):
        raise OAuthError("invalid_redirect_uri", "redirect_uri is not registered for this client")
    return provided


# ---- authorization ------------------------------------------------------------
def begin_authorization(
    control_conn,
    *,
    client_id,
    redirect_uri,
    code_challenge,
    code_challenge_method,
    state,
    scope,
    now_ms,
) -> dict:
    """Record a request and return the code the operator must approve.

    `scope` is accepted and ignored beyond being narrowed to READ_ONLY_SCOPE — a
    client does not get to ask for more authority than this server has.
    """
    collect_expired(control_conn, now_ms)
    live = control_conn.execute(
        "SELECT COUNT(*) FROM oauth_pending WHERE approved_ms IS NULL AND expires_ms > ?",
        (int(now_ms),),
    ).fetchone()[0]
    if live >= MAX_PENDING:
        raise OAuthError("temporarily_unavailable", "too many pending authorizations")

    row = _client(control_conn, client_id)
    redirect = _check_redirect(row, redirect_uri)
    state = _bounded(state, MAX_STATE_LEN, "state") if state is not None else None
    if not code_challenge:
        raise OAuthError("invalid_request", "PKCE is required")
    challenge = _bounded(code_challenge, MAX_CHALLENGE_LEN, "code_challenge")
    if len(challenge) < MIN_CHALLENGE_LEN:
        raise OAuthError("invalid_request", "code_challenge is too short for S256")
    if str(code_challenge_method or "").upper() != "S256":
        # `plain` makes the challenge equal the verifier, so anyone who sees the
        # authorization request can complete the exchange (OAuth 2.1 drops it).
        raise OAuthError("invalid_request", "code_challenge_method must be S256")

    request_id = "wvr_" + secrets.token_urlsafe(16)
    user_code = _new_user_code()
    control_conn.execute(
        "INSERT INTO oauth_pending(request_id, user_code, client_id, redirect_uri, state, "
        "code_challenge, scope, created_ms, expires_ms) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            request_id,
            user_code,
            row["client_id"],
            redirect,
            state,
            challenge,
            READ_ONLY_SCOPE,
            int(now_ms),
            int(now_ms) + PENDING_TTL_MS,
        ),
    )
    control_conn.commit()
    return {
        "request_id": request_id,
        "user_code": user_code,
        "client_name": row["client_name"],
        "expires_ms": int(now_ms) + PENDING_TTL_MS,
        "scope": READ_ONLY_SCOPE,
    }


def collect_expired(control_conn, now_ms) -> int:
    """Delete state nobody can use any more.

    Every unauthenticated authorize call leaves a row. Without collection the
    tables grow for as long as anyone cares to send requests, and the disk is
    the operator's.
    """
    n = 0
    for sql in (
        "DELETE FROM oauth_pending WHERE expires_ms <= ?",
        "DELETE FROM oauth_codes WHERE expires_ms <= ?",
        "DELETE FROM oauth_tokens WHERE expires_ms <= ?",
    ):
        n += control_conn.execute(sql, (int(now_ms),)).rowcount
    control_conn.commit()
    return n


def pending_requests(control_conn, now_ms) -> list:
    """What `whatsvault oauth-pending` shows the operator before approving."""
    rows = control_conn.execute(
        "SELECT p.user_code, p.client_id, p.redirect_uri, p.created_ms, p.expires_ms, "
        "c.client_name FROM oauth_pending p JOIN oauth_clients c ON c.client_id=p.client_id "
        "WHERE p.approved_ms IS NULL AND p.expires_ms > ? ORDER BY p.created_ms",
        (int(now_ms),),
    ).fetchall()
    return [dict(r) for r in rows]


def approve(control_conn, *, user_code, now_ms, audit_key=None) -> dict:
    """Out-of-band approval. Reaching this function at all means terminal access
    to the machine holding the vault, which is the authority being asserted."""
    code = str(user_code or "").strip().upper()
    row = control_conn.execute(
        "SELECT request_id, client_id, expires_ms, approved_ms FROM oauth_pending WHERE user_code=?",
        (code,),
    ).fetchone()
    if not row:
        raise OAuthError("invalid_request", "no such pending authorization")
    if row["approved_ms"] is not None:
        raise OAuthError("invalid_request", "already approved")
    if int(now_ms) > row["expires_ms"]:
        raise OAuthError("expired_token", "authorization request expired")
    control_conn.execute(
        "UPDATE oauth_pending SET approved_ms=? WHERE request_id=?", (int(now_ms), row["request_id"])
    )
    _audit(
        control_conn,
        audit_key,
        "oauth.approve",
        "ok",
        {"request_id": row["request_id"], "client_id": row["client_id"]},
        now_ms,
    )
    control_conn.commit()
    return {"request_id": row["request_id"], "client_id": row["client_id"]}


def poll(control_conn, *, request_id, now_ms):
    """Return the authorization code once approved, exactly once, else None.

    The browser polls this; it never reveals anything before approval, and the
    approval itself cannot be caused from here.
    """
    row = control_conn.execute(
        "SELECT * FROM oauth_pending WHERE request_id=?", (str(request_id or ""),)
    ).fetchone()
    if not row or row["approved_ms"] is None:
        return None
    if row["collected_ms"] is not None:
        return None
    if int(now_ms) > row["expires_ms"]:
        return None

    code = _new_secret()
    control_conn.execute(
        "INSERT INTO oauth_codes(code_hash, client_id, redirect_uri, code_challenge, scope, "
        "issued_ms, expires_ms) VALUES(?,?,?,?,?,?,?)",
        (
            _hash(code),
            row["client_id"],
            row["redirect_uri"],
            row["code_challenge"],
            row["scope"],
            int(now_ms),
            int(now_ms) + CODE_TTL_MS,
        ),
    )
    control_conn.execute(
        "UPDATE oauth_pending SET collected_ms=? WHERE request_id=?", (int(now_ms), row["request_id"])
    )
    control_conn.commit()
    return {
        "code": code,
        "state": row["state"],
        "redirect_uri": row["redirect_uri"],
        "scope": row["scope"],
    }


# ---- token ---------------------------------------------------------------------
def _issue(control_conn, client_id, now_ms, grant_id=None, audit_key=None) -> dict:
    grant_id = grant_id or "wvg_" + secrets.token_urlsafe(16)
    access, refresh_token = _new_secret(), _new_secret()
    for token, kind, ttl in (
        (access, "access", ACCESS_TTL_MS),
        (refresh_token, "refresh", REFRESH_TTL_MS),
    ):
        control_conn.execute(
            "INSERT INTO oauth_tokens(token_hash, kind, client_id, scope, issued_ms, expires_ms, "
            "grant_id) VALUES(?,?,?,?,?,?,?)",
            (_hash(token), kind, client_id, READ_ONLY_SCOPE, int(now_ms), int(now_ms) + ttl, grant_id),
        )
    # The credential itself is never an audit argument; the grant it belongs to
    # is what an investigation needs.
    _audit(
        control_conn,
        audit_key,
        "oauth.token_issued",
        "ok",
        {"client_id": client_id, "grant_id": grant_id, "scope": READ_ONLY_SCOPE},
        now_ms,
    )
    control_conn.commit()
    return {
        "access_token": access,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL_MS // 1000,
        "scope": READ_ONLY_SCOPE,
    }


def _revoke_family(control_conn, grant_id, now_ms) -> int:
    cur = control_conn.execute(
        "UPDATE oauth_tokens SET revoked_ms=? WHERE grant_id=? AND revoked_ms IS NULL",
        (int(now_ms), grant_id),
    )
    control_conn.commit()
    return cur.rowcount


def exchange_code(
    control_conn, *, code, client_id, redirect_uri, code_verifier, now_ms, audit_key=None
) -> dict:
    row = control_conn.execute("SELECT * FROM oauth_codes WHERE code_hash=?", (_hash(code or ""),)).fetchone()
    if not row:
        raise OAuthError("invalid_grant", "unknown authorization code")
    if row["consumed_ms"] is not None:
        raise OAuthError("invalid_grant", "authorization code already used")
    if int(now_ms) > row["expires_ms"]:
        raise OAuthError("invalid_grant", "authorization code expired")
    if not hmac.compare_digest(str(client_id or ""), row["client_id"]):
        raise OAuthError("invalid_grant", "code was not issued to this client")
    if not hmac.compare_digest(str(redirect_uri or ""), row["redirect_uri"]):
        raise OAuthError("invalid_grant", "redirect_uri does not match the authorization request")

    digest = hashlib.sha256(str(code_verifier or "").encode("ascii", "ignore")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    if not hmac.compare_digest(expected, row["code_challenge"]):
        raise OAuthError("invalid_grant", "PKCE verification failed")

    # Consumed before the token is minted, in the same transaction, so two
    # concurrent redemptions cannot both succeed.
    cur = control_conn.execute(
        "UPDATE oauth_codes SET consumed_ms=? WHERE code_hash=? AND consumed_ms IS NULL",
        (int(now_ms), row["code_hash"]),
    )
    # rowcount, not connection.total_changes: the latter is cumulative for the
    # whole connection and is never 0 after any earlier write, so the guard it
    # was meant to be would never have fired.
    if cur.rowcount != 1:
        raise OAuthError("invalid_grant", "authorization code already used")
    return _issue(control_conn, row["client_id"], now_ms, audit_key=audit_key)


def refresh(control_conn, *, refresh_token, client_id, now_ms, audit_key=None) -> dict:
    row = control_conn.execute(
        "SELECT * FROM oauth_tokens WHERE token_hash=? AND kind='refresh'",
        (_hash(refresh_token or ""),),
    ).fetchone()
    if not row:
        raise OAuthError("invalid_grant", "refresh token is not valid")
    if row["revoked_ms"] is not None:
        # Reuse of a ROTATED refresh token is evidence that it was captured
        # (OAuth 2.1 §4.14.2). Refusing only this call would leave the thief's
        # freshly rotated pair working; the whole family goes.
        killed = _revoke_family(control_conn, row["grant_id"], now_ms)
        _audit(
            control_conn,
            audit_key,
            "oauth.refresh_reuse_detected",
            "revoked",
            {"grant_id": row["grant_id"], "tokens_revoked": killed},
            now_ms,
        )
        raise OAuthError("invalid_grant", "refresh token is not valid")
    if int(now_ms) > row["expires_ms"]:
        raise OAuthError("invalid_grant", "refresh token is not valid")
    if not hmac.compare_digest(str(client_id or ""), row["client_id"]):
        raise OAuthError("invalid_grant", "refresh token was not issued to this client")
    # Rotation retires the whole previous pair, not just the refresh token:
    # leaving the old access token alive gave a thief a working credential for
    # its full hour, which the operator had no reason to believe existed.
    control_conn.execute(
        "UPDATE oauth_tokens SET revoked_ms=? WHERE grant_id=? AND revoked_ms IS NULL",
        (int(now_ms), row["grant_id"]),
    )
    return _issue(control_conn, row["client_id"], now_ms, grant_id=row["grant_id"], audit_key=audit_key)


def validate_access_token(control_conn, token, now_ms):
    """Return claims for a live access token, or None. Never raises: this runs on
    every request and an exception here is a denial of service."""
    if not token or not str(token).strip():
        return None
    try:
        row = control_conn.execute(
            "SELECT client_id, scope, expires_ms, revoked_ms FROM oauth_tokens "
            "WHERE token_hash=? AND kind='access'",
            (_hash(token),),
        ).fetchone()
    except Exception:  # noqa: BLE001 - a lookup failure is "not authenticated"
        return None
    if not row or row["revoked_ms"] is not None or int(now_ms) > row["expires_ms"]:
        return None
    return {"client_id": row["client_id"], "scope": row["scope"]}


def revoke_all(control_conn, now_ms, audit_key=None) -> int:
    cur = control_conn.execute(
        "UPDATE oauth_tokens SET revoked_ms=? WHERE revoked_ms IS NULL", (int(now_ms),)
    )
    _audit(control_conn, audit_key, "oauth.revoke_all", "ok", {"tokens_revoked": cur.rowcount}, now_ms)
    control_conn.commit()
    return cur.rowcount
