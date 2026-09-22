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
import json
import os
import secrets
import subprocess
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

# Plan 2a Task 9 Step 4's join-gate set, plus the two handshake scenarios
# this driver needs to cover RV-1 itself. Names are the gate's.
SCENARIOS = (
    "good-approval",
    "display-tamper",
    "challenge-tamper",
    "wrong-daemon-key",
    "wrong-approval-key",
    "wrong-key-id",
    "wrong-challenge-sha256",
    "duplicate-approval",
    "runtime-id-mismatch",
    "broker-death-mid-prompt",
    "agent-death-mid-prompt",
    "daemon-key-rotation",
    "agent-key-rotation",
    "wrong-transport-key",
)

# Scenarios whose subject is the broker's verification order rather than the
# agent's behaviour: the real agent emits a correct envelope, so the driver
# mutates it after receipt to stand in for a hostile or buggy signer.
BROKER_SIDE_TAMPERS = frozenset({"wrong-approval-key", "wrong-key-id", "wrong-challenge-sha256"})

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
    """One scenario: real rendezvous server, real broker, real agent binary.

    ``interactive`` drives the production ``run`` path instead of
    ``selftest-rendezvous``: the agent uses its paired Secure Enclave key
    behind a real Touch ID prompt, and the broker pins the public halves the
    agent exports rather than seeds it was handed.
    """

    def __init__(
        self, scenario: str, socket_dir: Path, binary: str, *, interactive: bool = False
    ) -> None:
        self.scenario = scenario
        self.socket_dir = socket_dir
        self.binary = binary
        self.interactive = interactive
        self.transport_seed = secrets.token_bytes(32)
        self.approval_seed = secrets.token_bytes(32)
        # The pin the broker checks against. "agent-key-rotation" pins a key
        # the agent no longer holds, which is the state right after the
        # operator rotates the Enclave key and before the daemon is re-pinned.
        self.pinned_approval_seed = (
            secrets.token_bytes(32) if scenario == "agent-key-rotation" else self.approval_seed
        )
        self._process: Any = None
        self.result: dict[str, Any] = {
            "scenario": scenario,
            "agent_rendered": False,
            "broker_accepted": False,
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

    def _export(self, which: str) -> dict[str, Any]:
        done = subprocess.run(  # noqa: PLW1510 -- returncode is asserted below
            [self.binary, "pairing", "export", which],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)

    @property
    def agent_transport_public(self) -> bytes:
        """What the broker pins as the agent's transport identity."""
        if self.interactive:
            return _unb64url(self._export("transport")["public_b64url"])
        return self.transport_private.public_key().public_bytes_raw()

    @property
    def daemon_public_hex(self) -> str:
        public = ed25519.Ed25519PrivateKey.from_private_bytes(CHALLENGE_KEY).public_key()
        return public.public_bytes_raw().hex()

    def agent_env(self) -> dict[str, str]:
        seed = self.transport_seed
        if self.scenario == "wrong-transport-key":
            seed = secrets.token_bytes(32)  # not the key the broker pinned
        env = {
            **os.environ,
            "CONSENT_NO_UI": "1",
            "CONSENT_SELFTEST_TRANSPORT_SEED": seed.hex(),
            "CONSENT_SELFTEST_APPROVAL_SEED": self.approval_seed.hex(),
            "CONSENT_SELFTEST_DAEMON_PUB": self.daemon_public_hex,
        }
        if self.scenario == "runtime-id-mismatch":
            # an agent left over from a previous runtime pins the old id
            env["CONSENT_SELFTEST_EXPECT_RUNTIME"] = (b"\x77" * 16).hex()
        return env

    # --- broker side ------------------------------------------------------

    def _broker(self) -> ConsentBroker:
        if self.interactive:
            exported = self._export("approval")
            der = _unb64url(exported["public_der_b64url"])
            public = serialization.load_der_public_key(der)
            key_id = exported["fingerprint"]
        else:
            public, _der, key_id = _approval_public(self.pinned_approval_seed)

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
        wire_challenge = challenge
        if self.scenario == "display-tamper":
            # the signed challenge still carries the honest digest
            display["action_display"] = "list chats "
        if self.scenario == "challenge-tamper":
            # one byte of the challenge changes after it was signed
            mutated = bytearray(challenge)
            mutated[-2] ^= 0x01
            wire_challenge = bytes(mutated)
        if self.scenario in ("wrong-daemon-key", "daemon-key-rotation"):
            # a well-formed signature from a key the agent has not pinned:
            # an impostor, or a daemon key rotated without re-pairing
            impostor = ed25519.Ed25519PrivateKey.from_private_bytes(b"\x03" * 32)
            signature = _b64url(impostor.sign(wire_challenge))
        await write_frame(
            writer,
            encode_json_frame(
                {
                    "type": "PROMPT",
                    "handle": handle,
                    "challenge": _b64url(wire_challenge),
                    "sig": signature,
                    "display": display,
                }
            ),
        )
        if self.scenario == "broker-death-mid-prompt":
            writer.close()
            return
        if self.scenario == "agent-death-mid-prompt":
            self.result["pending_before_death"] = broker.pending_count()
            assert self._process is not None
            self._process.kill()
            try:
                await asyncio.wait_for(read_frame(reader, idle_s=10), timeout=10)
            except (FrameError, TimeoutError):
                pass
            # the daemon's disconnect sweep releases the pending challenge
            self.result["swept"] = broker.invalidate_where(lambda record: True)
            self.result["pending_after_sweep"] = broker.pending_count()
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
            self.result["agent_rendered"] = True
            return
        envelope = answer.get("envelope", {})
        self.result["agent_rendered"] = True
        envelope = {**envelope, "sig": _unb64url(envelope["sig"])}
        envelope = self._tamper(envelope, challenge)
        try:
            await broker.consume(answer["handle"], envelope)
        except ConsentError as exc:
            self.result["denial_reason"] = str(exc)
            self.result["broker_accepted"] = False
            return
        self.result["approved"] = True
        self.result["signature_valid"] = True
        self.result["broker_accepted"] = True
        if self.scenario == "duplicate-approval":
            try:
                await broker.consume(answer["handle"], envelope)
            except ConsentError:
                self.result["second_consume"] = "rejected"
            else:
                self.result["second_consume"] = "accepted"

    def _tamper(self, envelope: dict[str, Any], challenge: bytes) -> dict[str, Any]:
        """Stand in for a hostile signer; the broker is the subject here."""
        if self.scenario == "wrong-approval-key":
            other = ec.derive_private_key(
                int.from_bytes(secrets.token_bytes(32), "big"), ec.SECP256R1()
            )
            return {
                **envelope,
                "sig": other.sign(challenge, ec.ECDSA(hashes.SHA256())),
            }
        if self.scenario == "wrong-key-id":
            _public, _der, other_id = _approval_public(secrets.token_bytes(32))
            return {**envelope, "key_id": other_id}
        if self.scenario == "wrong-challenge-sha256":
            return {**envelope, "challenge_sha256": hashlib.sha256(b"not it").hexdigest()}
        return envelope

    # --- driver -----------------------------------------------------------

    async def drive(self, timeout: float) -> dict[str, Any]:
        socket_path = self.socket_dir / "consent.sock"
        server = await serve_rendezvous(
            socket_path,
            challenge_key=CHALLENGE_KEY,
            runtime_id=RUNTIME_ID,
            daemon_key_id="ed25519:sha256:"
            + hashlib.sha256(bytes.fromhex(self.daemon_public_hex)).hexdigest(),
            agent_transport_public=self.agent_transport_public,
            on_session=self._serve_prompt,
        )
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            self.binary,
            "run" if self.interactive else "selftest-rendezvous",
            str(socket_path),
            env=dict(os.environ) if self.interactive else self.agent_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._process = process
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


def run_scenario(
    name: str,
    *,
    timeout: float = 60.0,
    binary: str = AGENT_BIN,
    interactive: bool = False,
) -> dict[str, Any]:
    """Run one scenario end to end and return its result dictionary.

    ``interactive`` drives the agent's production ``run`` path — paired
    Enclave key, real Touch ID prompt — instead of the headless selftest
    route.
    """
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario: {name}")
    if not Path(binary).exists():
        raise FileNotFoundError(f"agent binary is absent: {binary}")

    async def main() -> dict[str, Any]:
        import tempfile

        # AF_UNIX paths cap near 104 bytes, so the socket lives in a short dir.
        with tempfile.TemporaryDirectory(dir="/tmp") as short_dir:
            run = _Run(name, Path(short_dir), binary, interactive=interactive)
            return await run.drive(timeout)

    return asyncio.run(main())
