"""D39-PRE Task E10c: the egress sweeps D36 left out — proxy frames, webhook responses, backups.

The same rule as D36: no secret anywhere, and bodies or identities only where their surface is
meant to carry them. Each sweep drives the real component and captures every byte it emits.
"""

import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

from comms.core.auth import lease_format
from comms.core.backup import age
from comms.core.backup.export_import import export
from comms.core.campaigns import directory as d
from comms.core.keys import rotate as rot
from comms.core.keys.slots import load_active
from comms.transports.whatsapp.webhooks.ingress import WebhookIngress
from tests.core import schema_fixtures as fx
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW
from tests.integration.test_comms_daemon import Daemon

APP_SECRET = b"CANARY-APP-SECRET-5c1f" + b"0" * 10
VERIFY_TOKEN = "CANARY-VERIFY-TOKEN-" + "9" * 20
NAME, PHONE = "CANARY-NAME-b7d2", "+61400000077"


# -- the stdio proxy: every frame and every stderr byte ---------------------------------------


def test_the_proxy_emits_no_seed_and_no_lease(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("run").mkdir(mode=0o700)
    daemon = Daemon(tmp_path)
    daemon.provision()
    daemon.start()
    try:
        daemon.comms("client", "add", "--name", "sweep", "--helper-path", "seed")
        _cli, seed = lease_format.read_helper(Path("seed"))
        messages = (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2026-07-28", "capabilities": {},
                "clientInfo": {"name": "sweep", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "comms_location_list", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "comms_nope", "arguments": {}}},
        )  # fmt: skip
        comms = str(Path(sys.executable).parent / "comms")
        proc = subprocess.Popen(
            [comms, "mcp", "--stdio", "--client-seed", "seed", "--daemon",
             f"http://127.0.0.1:{daemon.port}", "--runtime-dir", "run"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=daemon.env,
        )  # fmt: skip
        out = []
        for message in messages:  # a real client keeps stdin open and waits for each answer
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
            if "id" in message:
                out.append(proc.stdout.readline())
        proc.stdin.close()
        rest, err = proc.stdout.read(), proc.stderr.read()
        proc.wait(timeout=30)
        done = subprocess.CompletedProcess(proc.args, proc.returncode, "".join(out) + rest, err)
    finally:
        daemon.stop()
    emitted = done.stdout + done.stderr
    answered = {json.loads(line).get("id") for line in done.stdout.splitlines() if line.strip()}
    assert {1, 2, 3, 4} <= answered, done.stderr[-500:]  # every request was answered
    for secret in (seed.hex(), lease_format.encode(seed), "cml1."):
        assert secret not in emitted, secret


# -- the webhook ingress: every response it can give ------------------------------------------


def test_webhook_responses_carry_no_secret():
    async def accept(raw):
        return None

    app = WebhookIngress(app_secret=APP_SECRET, verify_token=VERIFY_TOKEN, accept=accept,
                         clock=lambda: 0.0)  # fmt: skip
    body = b'{"object":"whatsapp_business_account","entry":[]}'
    good = "sha256=" + hmac.new(APP_SECRET, body, hashlib.sha256).hexdigest()
    json_ct = {"content-type": "application/json"}
    requests = [
        ("GET", {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "c1"}, None, {}),
        ("GET", {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "c2"}, None, {}),
        ("GET", {"hub.mode": "nope"}, None, {}),
        ("POST", None, body, {**json_ct, "x-hub-signature-256": good}),
        ("POST", None, body, {**json_ct, "x-hub-signature-256": "sha256=" + "0" * 64}),
        ("POST", None, body, {"content-type": "text/plain", "x-hub-signature-256": good}),
        ("POST", None, body, {**json_ct, "content-length": str(10**9)}),
        ("PUT", None, body, json_ct),
    ]  # fmt: skip
    seen = []

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            for method, params, content, headers in requests:
                response = await client.request(method, "/webhooks/meta", params=params,
                                                content=content, headers=headers)  # fmt: skip
                seen.append(response.status_code)
                seen.append(response.content + b"".join(k + v for k, v in response.headers.raw))
            other = await client.get("/elsewhere")
            seen.append(other.content)

    asyncio.run(go())
    emitted = b"".join(x for x in seen if isinstance(x, bytes))
    assert APP_SECRET not in emitted and VERIFY_TOKEN.encode() not in emitted
    assert [s for s in seen if isinstance(s, int)] == [200, 403, 400, 200, 401, 415, 413, 405]


# -- backup artifacts: the ciphertext and the signature sidecar -------------------------------


def test_backup_artifacts_hold_no_plaintext_canary_and_no_key(tmp_path):
    w = comms_world(tmp_path)
    conn = w["conn"]
    rot.rotate(w["writer"], w["store"], "backup-key", material=os.urandom(32), prove=lambda m: None,
               now=NOW)  # fmt: skip
    rcp = d.add_recipient(conn, now=NOW, display_name=NAME)
    d.add_contact_point(conn, rcp, "whatsapp", PHONE, normalize=fx.wa, now=NOW)
    identity = age.generate_identity()
    result = export(
        w["writer"], w["store"], age.recipient_of(identity), {"meta_waba": "123"}, now=NOW
    )
    artifacts = result.ciphertext + result.sidecar
    seed, _key_id = load_active(conn, w["store"], "backup-key")
    for canary in (NAME.encode(), PHONE.encode(), PHONE[1:].encode(), seed, seed.hex().encode(),
                   identity.encode()):  # fmt: skip
        assert canary not in artifacts, canary
    assert NAME.encode() in age.decrypt(result.ciphertext, identity)  # the canary was really there
