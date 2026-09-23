"""A real Touch ID approval for a real catalogue disclosure (platform-gated).

Drives the paired, certificate-signed agent's production ``run`` path through
RV-1 into the composed runtime. The operator sees one prompt reading
"list gateway projects" and approves it with Touch ID. The temporary daemon
pin is imported and removed exactly as the Phase-2J join gate does.
"""

import asyncio
import base64
import json
import subprocess

import pytest
import uvicorn

from tests.integration.test_phase4a_end_to_end import CODEX, _free_port, call

pytestmark = pytest.mark.platform_gated

RUNTIME = b"\x02" * 16


def _run(binary, *args, check=True):
    return subprocess.run(
        [str(binary), *args], capture_output=True, text=True, timeout=120, check=check
    )


async def test_touch_id_approves_a_real_list_projects(paired_agent_binary, tmp_path, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from telegram_mcp.consent.challenge import verify_agent_signature
    from telegram_mcp.keys.store import provision_lease_seed, provision_missing
    from telegram_mcp.runtime.composition import build_runtime, serve_consent
    from telegram_mcp.storage.db import open_db
    from tests.agent.stub_broker import CHALLENGE_KEY
    from tests.authority_fixtures import seed_authority_rows

    monkeypatch.chdir(tmp_path)
    keys = tmp_path / "keys"
    keys.mkdir(mode=0o700)
    # The paired agent will pin the fixture daemon key (join-gate convention).
    (keys / "challenge-key").write_bytes(CHALLENGE_KEY)
    (keys / "challenge-key").chmod(0o600)
    provision_missing(keys, phases=(2, 3))
    seed = provision_lease_seed(keys, CODEX)

    approval = json.loads(_run(paired_agent_binary, "pairing", "export", "approval").stdout)
    der = base64.urlsafe_b64decode(
        approval["public_der_b64url"] + "=" * (-len(approval["public_der_b64url"]) % 4)
    )
    exported = json.loads(_run(paired_agent_binary, "pairing", "export", "transport").stdout)[
        "public_b64url"
    ]
    transport = base64.urlsafe_b64decode(exported + "=" * (-len(exported) % 4))

    conn = open_db(tmp_path / "meta.db")
    seed_authority_rows(conn)
    conn.execute(
        "INSERT INTO client_projects (client_id, project_id, can_read, can_cross_search, egress_level,"
        " excerpt_max_codepoints, created_at, updated_at) VALUES (1, 1, 1, 0, 'full_text', NULL, 'now', 'now')"
    )
    conn.commit()
    (tmp_path / "anchor").mkdir(mode=0o700)
    port = _free_port()
    services = build_runtime(
        conn,
        key_dir=keys,
        anchor_path=tmp_path / "anchor" / "anchor.json",
        runtime_id=RUNTIME,
        agent_verify=lambda sig, msg: verify_agent_signature(sig, msg, der),
        pinned_key_id=approval["fingerprint"],
        port=port,
    )

    daemon_public = (
        ed25519.Ed25519PrivateKey.from_private_bytes(CHALLENGE_KEY).public_key().public_bytes_raw()
    )
    _run(
        paired_agent_binary,
        "pairing",
        "import-daemon-pin",
        base64.urlsafe_b64encode(daemon_public).decode().rstrip("="),
    )
    consent_socket = await serve_consent(
        services, tmp_path / "c.sock", agent_transport_public=transport
    )
    agent = await asyncio.create_subprocess_exec(str(paired_agent_binary), "run", "c.sock")
    server = uvicorn.Server(
        uvicorn.Config(services.ingress_app, host="127.0.0.1", port=port, log_level="warning")
    )
    serving = asyncio.create_task(server.serve())
    try:
        while not server.started or not services.prompter.connected:
            await asyncio.sleep(0.05)
        body = await call(
            {"seeds": {CODEX: seed}, "port": port},
            CODEX,
            "telegram_list_projects",
            {},
            runtime=RUNTIME,
        )
        assert body["ok"] is True, body
        assert body["meta"]["disclosure"]["receipt_ref"].startswith("tdr_")
    finally:
        server.should_exit = True
        await serving
        agent.kill()
        await agent.wait()
        consent_socket.close()
        _run(paired_agent_binary, "pairing", "forget-daemon-pin", check=False)
        status = json.loads(_run(paired_agent_binary, "pairing-status", check=False).stdout)
        assert "daemon_pin" not in status["records"], "the temporary pin outlived the test"
