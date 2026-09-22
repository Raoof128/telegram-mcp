"""Broker-side driver for the consent agent's rendezvous scenarios.

The plan calls this a stub broker. It drives the **real** Python halves —
``telegram_mcp.ipc.rendezvous.serve_rendezvous`` for RV-1 and
``telegram_mcp.consent.broker.ConsentBroker`` for issuance and exact-once
consume — because a hand-written stub would only prove the agent agrees with
the stub. What these scenarios exercise is byte agreement between the real
broker and the real agent binary, which is the whole point of the gate.

Prompt frames (frozen here, consumed by Plan 2a's join gate):

* daemon → agent
  ``{"type": "PROMPT", "handle": "tgu_…", "challenge": "<b64url JCS>",
     "sig": "<b64url raw 64B>", "display": {…}}``
* agent → daemon, approval
  ``{"type": "APPROVAL", "handle": "tgu_…",
     "envelope": {"challenge_sha256": …, "sig": …, "key_id": …}}``
* agent → daemon, refusal
  ``{"type": "DENIAL", "handle": "tgu_…", "reason": "DISPLAY-MISMATCH"}``

Frames use the shared codec: uint32 big-endian length, 64 KiB ceiling,
strict UTF-8, strict JSON with duplicate keys rejected.

Key material is per-run and passed to the agent through ``selftest-``-only
environment variables; the agent's production path reads none of them.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from telegram_mcp.consent.broker import ConsentBroker, ConsentError
from telegram_mcp.consent.challenge import display_digest, synthetic_exposure_digest
from telegram_mcp.ipc.framing import (
    FrameError,
    decode_json_frame,
    encode_json_frame,
    read_frame,
    write_frame,
)
from telegram_mcp.ipc.rendezvous import serve_rendezvous

AGENT_BIN = "build/consent/TelegramMCPConsent.app/Contents/MacOS/telegram-mcp-consent"

# Plan 2a's normative fixture seed for the daemon challenge key.
CHALLENGE_KEY = b"\x01" * 32
RUNTIME_ID = b"\x02" * 16
PRINCIPAL = "prn_" + "a" * 26
CLIENT = "tcl_" + "b" * 26
ACCOUNT = "tga_" + "c" * 26

SCENARIOS = (
    "good",
    "replay",
    "tamper-display",
    "wrong-daemon-key",
    "kill-mid-prompt",
    "wrong-transport-key",
)

DISPLAY = {
    "action_display": "list chats",
    "client_display": "Codex",
    "peer_display": None,
    "project_display": ["Ops"],
    "risk_class": "metadata",
}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _approval_public(seed: bytes) -> tuple[ec.EllipticCurvePublicKey, bytes, str]:
    private = ec.derive_private_key(int.from_bytes(seed, "big"), ec.SECP256R1())
    der = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private.public_key(), der, "p256:sha256:" + hashlib.sha256(der).hexdigest()


class _Run:
    """One scenario: real rendezvous server, real broker, real agent binary."""

    def __init__(self, scenario: str, socket_dir: Path, binary: str) -> None:
        self.scenario = scenario
        self.socket_dir = socket_dir
        self.binary = binary
        self.transport_seed = secrets.token_bytes(32)
        self.approval_seed = (
            bytes([0] * 31 + [9]) if scenario == "replay" else secrets.token_bytes(32)
        )
        self.result: dict[str, Any] = {
            "scenario": scenario,
            "handshake_completed": False,
            "approved": False,
            "signature_valid": False,
            "denial_reason": None,
            "second_consume": None,
            "agent_exited": False,
            "exit_seconds": None,
        }

    # --- key material -----------------------------------------------------

    @property
    def transport_private(self) -> ed25519.Ed25519PrivateKey:
        return ed25519.Ed25519PrivateKey.from_private_bytes(self.transport_seed)

    @property
    def daemon_public_hex(self) -> str:
        public = ed25519.Ed25519PrivateKey.from_private_bytes(CHALLENGE_KEY).public_key()
        return public.public_bytes_raw().hex()

    def agent_env(self) -> dict[str, str]:
        seed = self.transport_seed
        if self.scenario == "wrong-transport-key":
            seed = secrets.token_bytes(32)  # not the key the broker pinned
        return {
            **os.environ,
            "CONSENT_NO_UI": "1",
            "CONSENT_SELFTEST_TRANSPORT_SEED": seed.hex(),
            "CONSENT_SELFTEST_APPROVAL_SEED": self.approval_seed.hex(),
            "CONSENT_SELFTEST_DAEMON_PUB": self.daemon_public_hex,
        }

    # --- broker side ------------------------------------------------------

    def _broker(self) -> ConsentBroker:
        public, _der, key_id = _approval_public(self.approval_seed)

        def verify(sig: bytes, msg: bytes) -> bool:
            try:
                public.verify(sig, msg, ec.ECDSA(hashes.SHA256()))
            except Exception:  # noqa: BLE001 -- a verifier fails closed on anything
                return False
            return True

        return ConsentBroker(
            challenge_key=CHALLENGE_KEY,
            agent_verify=verify,
            runtime_id=RUNTIME_ID,
            pinned_key_id=key_id,
        )

    async def _serve_prompt(self, session, reader, writer) -> None:
        self.result["handshake_completed"] = True
        broker = self._broker()
        display = dict(DISPLAY)
        digest = display_digest(display)
        handle = await broker.issue(
            tool="telegram_list_chats",
            request_hmac="ab" * 32,
            principal=PRINCIPAL,
            client=CLIENT,
            account=ACCOUNT,
            policy_epoch=1,
            project_scope_digest="1" * 64,
            security_epoch=1,
            display_digest=digest,
            exposure_snapshot_digest=synthetic_exposure_digest(),
        )
        challenge = broker.challenge_bytes(handle)
        signature = broker.daemon_signature(handle)
        if self.scenario == "tamper-display":
            # the signed challenge still carries the honest digest
            display["action_display"] = "list chats "
        if self.scenario == "wrong-daemon-key":
            # a well-formed signature from a key the agent has not pinned
            impostor = ed25519.Ed25519PrivateKey.from_private_bytes(b"\x03" * 32)
            signature = _b64url(impostor.sign(challenge))
        await write_frame(
            writer,
            encode_json_frame(
                {
                    "type": "PROMPT",
                    "handle": handle,
                    "challenge": _b64url(challenge),
                    "sig": signature,
                    "display": display,
                }
            ),
        )
        if self.scenario == "kill-mid-prompt":
            writer.close()
            return
        try:
            raw = await read_frame(reader, idle_s=20)
        except FrameError:
            return
        if not raw:
            return
        answer = decode_json_frame(raw)
        if answer.get("type") == "DENIAL":
            self.result["denial_reason"] = answer.get("reason")
            return
        envelope = answer.get("envelope", {})
        envelope = {**envelope, "sig": _unb64url(envelope["sig"])}
        try:
            await broker.consume(answer["handle"], envelope)
        except ConsentError as exc:
            self.result["denial_reason"] = str(exc)
            return
        self.result["approved"] = True
        self.result["signature_valid"] = True
        if self.scenario == "replay":
            try:
                await broker.consume(answer["handle"], envelope)
            except ConsentError:
                self.result["second_consume"] = "rejected"
            else:
                self.result["second_consume"] = "accepted"

    # --- driver -----------------------------------------------------------

    async def drive(self, timeout: float) -> dict[str, Any]:
        socket_path = self.socket_dir / "consent.sock"
        server = await serve_rendezvous(
            socket_path,
            challenge_key=CHALLENGE_KEY,
            runtime_id=RUNTIME_ID,
            daemon_key_id="ed25519:sha256:"
            + hashlib.sha256(bytes.fromhex(self.daemon_public_hex)).hexdigest(),
            agent_transport_public=self.transport_private.public_key().public_bytes_raw(),
            on_session=self._serve_prompt,
        )
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            self.binary,
            "selftest-rendezvous",
            str(socket_path),
            env=self.agent_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            self.result["agent_exited"] = True
            self.result["exit_seconds"] = time.monotonic() - started
            self.result["agent_returncode"] = process.returncode
            self.result["agent_stdout"] = stdout.decode(errors="replace")
            self.result["agent_stderr"] = stderr.decode(errors="replace")
        except TimeoutError:
            process.kill()
            await process.wait()
            self.result["agent_exited"] = False
        finally:
            server.close()
            await server.wait_closed()
        return self.result


def run_scenario(name: str, *, timeout: float = 60.0, binary: str = AGENT_BIN) -> dict[str, Any]:
    """Run one scenario end to end and return its result dictionary."""
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario: {name}")
    if not Path(binary).exists():
        raise FileNotFoundError(f"agent binary is absent: {binary}")

    async def main() -> dict[str, Any]:
        import tempfile

        # AF_UNIX paths cap near 104 bytes, so the socket lives in a short dir.
        with tempfile.TemporaryDirectory(dir="/tmp") as short_dir:
            return await _Run(name, Path(short_dir), binary).drive(timeout)

    return asyncio.run(main())
