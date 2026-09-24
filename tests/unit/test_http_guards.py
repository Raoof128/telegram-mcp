"""Bearer gate, rate limiter and preflight, below the SDK."""

import contextvars

from comms.transports.telegram.http_guards import (
    RATE_LIMITED_BODY,
    UNAUTHORIZED_BODY,
    RateLimiter,
    bearer_gate,
    duplicate_key_preflight,
)

VAR: contextvars.ContextVar = contextvars.ContextVar("who", default=None)


async def _drive(app, *, client=("127.0.0.1", 5000), headers=(), body=b"{}"):
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "client": client,
        "headers": list(headers),
        "path": "/mcp",
    }
    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, payload


async def _inner(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": str(VAR.get()).encode()})


async def test_the_gate_refuses_every_bad_case_with_identical_bytes():
    gate = bearer_gate(
        _inner, authenticate=lambda token: "alice" if token == "good" else None, principal_var=VAR
    )
    cases = [
        {"headers": ()},
        {"headers": [(b"authorization", b"Basic good")]},
        {"headers": [(b"authorization", b"Bearer bad")]},
        {"headers": [(b"authorization", b"Bearer good")], "client": ("10.0.0.7", 5000)},
        {"headers": [(b"authorization", b"Bearer good")], "client": None},
    ]
    for case in cases:
        assert await _drive(gate, **case) == (401, UNAUTHORIZED_BODY), case
    assert await _drive(gate, headers=[(b"authorization", b"Bearer good")]) == (200, b"alice")


async def test_the_preflight_rejects_duplicates_and_admits_by_callback():
    blocked = duplicate_key_preflight(_inner, 65536, admit=lambda body: 7.0)
    status, payload = await _drive(blocked, body=b'{"a":1}')
    assert (status, payload) == (429, RATE_LIMITED_BODY)
    status, _ = await _drive(blocked, body=b'{"a":1,"a":2}')
    assert status == 400


def test_the_limiter_is_per_client_with_an_owner_ceiling():
    now = [0.0]
    limiter = RateLimiter({"telegram_list_projects": 2}, owner_factor=2, clock=lambda: now[0])
    assert limiter.check("tcl_a", "telegram_list_projects") is None
    assert limiter.check("tcl_a", "telegram_list_projects") is None
    assert limiter.check("tcl_a", "telegram_list_projects") > 0  # per-client limit
    assert limiter.check("tcl_b", "telegram_list_projects") is None
    assert limiter.check("tcl_b", "telegram_list_projects") is None
    assert limiter.check("tcl_c", "telegram_list_projects") > 0  # owner ceiling: 2 x 2
    now[0] = 61.0
    assert limiter.check("tcl_a", "telegram_list_projects") is None
