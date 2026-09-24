"""comms v0.3 Task C28: the Meta webhook ingress — raw-bytes HMAC before parse, bounded (A36)."""

import asyncio
import hashlib
import hmac

import httpx
import pytest

from comms.transports.whatsapp.webhooks.ingress import MAX_BODY_BYTES, WebhookIngress

APP_SECRET = b"fixture-app-secret"
VERIFY_TOKEN = "fixture-verify-token"
BODY = b'{"object":"whatsapp_business_account","entry":[]}'


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _sign(body, secret=APP_SECRET):
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def _app(accepted=None, clock=None, **kw):
    accepted = [] if accepted is None else accepted

    async def accept(raw):
        accepted.append(raw)

    return WebhookIngress(
        app_secret=APP_SECRET,
        verify_token=VERIFY_TOKEN,
        accept=accept,
        clock=clock or Clock(),
        **kw,
    ), accepted


async def _post(app, body=BODY, headers=None, path="/webhooks/meta"):
    base = {"content-type": "application/json", "x-hub-signature-256": _sign(body)}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://hooks.example"
    ) as client:
        return await client.post(path, content=body, headers={**base, **(headers or {})})


async def test_a_signed_json_body_is_accepted_raw():
    app, accepted = _app()
    response = await _post(app)
    assert response.status_code == 200 and accepted == [BODY]


@pytest.mark.parametrize(
    "signature",
    [
        None,
        "",
        "sha256=",
        "sha1=abc",
        "sha256=" + "0" * 64,
        _sign(BODY, b"other-secret"),
        _sign(BODY).upper(),
    ],
)
async def test_bad_signature_refused_before_parse(signature):
    app, accepted = _app()
    headers = (
        {"x-hub-signature-256": signature} if signature is not None else {"x-hub-signature-256": ""}
    )
    response = await _post(app, headers=headers)
    assert response.status_code == 401 and accepted == []  # the body never reached a parser


async def test_the_hmac_covers_the_exact_raw_bytes():
    app, accepted = _app()
    reformatted = b'{"object": "whatsapp_business_account", "entry": []}'  # same JSON, other bytes
    response = await _post(app, body=reformatted, headers={"x-hub-signature-256": _sign(BODY)})
    assert response.status_code == 401 and accepted == []


async def test_oversize_body_refused():
    app, accepted = _app()
    big = b"x" * (MAX_BODY_BYTES + 1)
    response = await _post(app, body=big, headers={"x-hub-signature-256": _sign(big)})
    assert response.status_code == 413 and accepted == []


async def test_oversize_body_refused_while_streaming_without_a_length():
    app, accepted = _app()
    sent = []

    async def receive_parts():
        for _ in range(MAX_BODY_BYTES // 65536 + 4):
            sent.append(1)
            yield b"y" * 65536

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://hooks.example"
    ) as client:
        response = await client.post(
            "/webhooks/meta",
            content=receive_parts(),
            headers={"content-type": "application/json", "x-hub-signature-256": "sha256=00"},
        )
    assert response.status_code == 413 and accepted == []


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", ""])
async def test_wrong_content_type_refused(content_type):
    app, accepted = _app()
    response = await _post(app, headers={"content-type": content_type})
    assert response.status_code == 415 and accepted == []


async def test_json_with_a_charset_is_json():
    app, _accepted = _app()
    assert (
        await _post(app, headers={"content-type": "application/json; charset=utf-8"})
    ).status_code == 200


async def test_get_handshake_is_not_post_auth():
    app, accepted = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://hooks.example"
    ) as client:
        ok = await client.get(
            "/webhooks/meta",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": "1158201444",
            },
        )
        wrong = await client.get(
            "/webhooks/meta",
            params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"},
        )
        missing = await client.get("/webhooks/meta")
        signed_get = await client.get("/webhooks/meta", headers={"x-hub-signature-256": _sign(b"")})
    assert (ok.status_code, ok.text) == (200, "1158201444")
    assert wrong.status_code == 403 and missing.status_code == 400 and signed_get.status_code == 400
    assert accepted == []  # a handshake never delivers anything


async def test_rate_bound():
    clock = Clock()
    app, accepted = _app(clock=clock, rate_capacity=3, rate_per_second=1.0)
    codes = [(await _post(app)).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429] and len(accepted) == 3
    clock.now += 2.0
    assert (await _post(app)).status_code == 200


async def test_only_webhook_routes_exist():
    app, accepted = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://hooks.example"
    ) as client:
        other = await client.post(
            "/mcp",
            content=BODY,
            headers={"content-type": "application/json", "x-hub-signature-256": _sign(BODY)},
        )
        put = await client.put("/webhooks/meta", content=BODY)
        root = await client.get("/")
    assert (other.status_code, put.status_code, root.status_code) == (
        404,
        405,
        404,
    ) and accepted == []


async def test_a_slow_body_hits_the_deadline():
    app, accepted = _app(read_deadline_s=0.05)

    async def slow():
        yield b"{"
        await asyncio.sleep(0.3)
        yield b"}"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://hooks.example"
    ) as client:
        response = await client.post(
            "/webhooks/meta",
            content=slow(),
            headers={"content-type": "application/json", "x-hub-signature-256": _sign(b"{}")},
        )
    assert response.status_code == 408 and accepted == []


async def test_a_failing_accept_is_not_acked():
    async def accept(raw):
        raise RuntimeError("inbox unavailable")

    app = WebhookIngress(
        app_secret=APP_SECRET, verify_token=VERIFY_TOKEN, accept=accept, clock=Clock()
    )
    assert (await _post(app)).status_code == 503  # Meta retries; nothing is ACKed unrecorded
