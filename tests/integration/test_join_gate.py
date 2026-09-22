"""Task 9 Step 4: the Phase-2J join gate — real broker against the real agent.

Both halves green in isolation proves nothing about byte agreement, so
Phase 2 is not complete until this gate passes. It cannot pass yet: the
signed agent bundle is Plan 2b Task 4, and the prompt frames the driver
must speak are frozen in Plan 2b Task 3. Neither exists, so every scenario
skips with its reason rather than reporting a green it has not earned.

The bundle path is taken from ``TELEGRAM_MCP_AGENT_BUNDLE`` and never
hardcoded. With a bundle present, the handshake scenario runs for real
against :func:`serve_rendezvous`; the rest skip until their driver half
lands with Plan 2b.
"""

import asyncio
import os
from pathlib import Path

import pytest

from telegram_mcp.ipc.rendezvous import serve_rendezvous

# The full gate set from Plan 2a Task 9 Step 4.
JOIN_SCENARIOS = (
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
)

# Scenarios whose driver half exists on the Python side today.
IMPLEMENTED_SCENARIOS = frozenset({"handshake"})

BUNDLE_ENV = "TELEGRAM_MCP_AGENT_BUNDLE"


class JoinGate:
    """Drives one join scenario against the real signed agent binary."""

    def __init__(self, binary: Path, socket_dir: Path) -> None:
        self.binary = binary
        self.socket_dir = socket_dir

    def run(self, scenario: str, *, timeout: float = 120.0) -> dict[str, object]:
        if scenario not in IMPLEMENTED_SCENARIOS:
            raise NotImplementedError(
                f"scenario {scenario!r} needs the Plan 2b prompt frames (Task 3) "
                "and the signed bundle (Task 4)"
            )
        return asyncio.run(self._handshake(timeout))

    async def _handshake(self, timeout: float) -> dict[str, object]:
        from telegram_mcp.keys.store import load_key

        socket_path = self.socket_dir / "consent.sock"
        sessions: list[object] = []
        server = await serve_rendezvous(
            socket_path,
            challenge_key=load_key("challenge-key"),
            runtime_id=os.urandom(16),
            daemon_key_id="ed25519:sha256:" + "0" * 64,
            agent_transport_public=b"\x00" * 32,
            on_session=lambda session, reader, writer: sessions.append(session),
        )
        try:
            process = await asyncio.create_subprocess_exec(
                str(self.binary),
                "run",
                "--socket",
                str(socket_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except TimeoutError:
                process.kill()
                raise
        finally:
            server.close()
            await server.wait_closed()
        return {"handshake_completed": bool(sessions)}


@pytest.fixture
def join_gate(tmp_path, monkeypatch):
    bundle = os.environ.get(BUNDLE_ENV)
    if not bundle:
        pytest.skip(f"set {BUNDLE_ENV} to the signed agent binary (Plan 2b Task 4)")
    binary = Path(bundle)
    if not binary.exists():
        pytest.skip(f"{BUNDLE_ENV} does not point at an existing binary")
    monkeypatch.chdir(tmp_path)
    return JoinGate(binary, Path("run"))


def test_the_gate_set_is_the_one_the_plan_names():
    """A guard against the gate quietly shrinking to what happens to pass."""
    assert len(JOIN_SCENARIOS) == 13
    assert len(set(JOIN_SCENARIOS)) == 13
    assert "good-approval" in JOIN_SCENARIOS
    assert not IMPLEMENTED_SCENARIOS & set(JOIN_SCENARIOS)


@pytest.mark.platform_gated
@pytest.mark.parametrize("scenario", JOIN_SCENARIOS)
def test_join_gate_scenario(join_gate, scenario):
    try:
        result = join_gate.run(scenario)
    except NotImplementedError as exc:
        pytest.skip(str(exc))
    assert result["broker_accepted"] is True
    assert result["agent_rendered"] is True


@pytest.mark.platform_gated
def test_join_gate_handshake_against_the_real_agent(join_gate):
    assert join_gate.run("handshake")["handshake_completed"] is True
