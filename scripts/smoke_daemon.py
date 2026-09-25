"""D39-A runtime acceptance (D39-PRE Task E11): the real daemon, in its own process.

Everything is driven through the installed ``comms`` binary, the admin Unix socket, the stdio
proxy under a real MCP client, and HTTP ``/mcp`` with fresh ``cml1`` leases. The daemon is
``comms selftest-daemon``: the production daemon with deterministic local providers injected
(the seam). Nothing here imports the composition; each check returns ``True`` or a reason.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

COMMS = str(Path(sys.executable).parent / "comms")
PROTOCOL = "2026-07-28"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Daemon:
    """One production-shaped state directory and its selftest daemon process."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.state, self.run = root / "state", root / "run"
        self.run.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.port = _free_port()
        self.env = {**os.environ, "TELEGRAM_MCP_RUNTIME_DIR": str(self.run)}
        self.proc: subprocess.Popen[str] | None = None

    def comms(self, *argv: str, stdin: Any = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [COMMS, *argv],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=90,
            stdin=stdin if stdin is not None else subprocess.DEVNULL,
        )

    def json(self, *argv: str) -> Any:
        done = self.comms(*argv)
        if done.returncode != 0:
            raise AssertionError(f"comms {' '.join(argv)}: {done.stderr.strip()[:300]}")
        return json.loads(done.stdout)

    def settings(self, **extra: Any) -> None:
        path = self.state / "comms" / "comms.json"
        path.write_text(json.dumps({"local_port": self.port, **extra}), encoding="utf-8")
        path.chmod(0o600)

    def start(self, *, wait: bool = True) -> subprocess.Popen[str]:
        self.proc = subprocess.Popen(
            [
                COMMS,
                "selftest-daemon",
                "--state-dir",
                str(self.state),
                "--runtime-dir",
                str(self.run),
            ],
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if wait:
            for _ in range(600):
                if self.proc.poll() is not None:
                    raise AssertionError(f"daemon exited: {self.proc.stderr.read()[-300:]}")  # type: ignore[union-attr]
                if (self.run / "admin.sock").exists() and self._listening():
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("the daemon did not start")
        return self.proc

    def _listening(self) -> bool:
        with socket.socket() as s:
            return s.connect_ex(("127.0.0.1", self.port)) == 0

    def stop(self, sig: int = signal.SIGTERM) -> int:
        assert self.proc is not None
        self.proc.send_signal(sig)
        return int(self.proc.wait(timeout=30))

    async def mcp(self, seed: Path, action: Any) -> Any:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=COMMS,
            args=["mcp", "--stdio", "--client-seed", str(seed), "--daemon",
                  f"http://127.0.0.1:{self.port}", "--runtime-dir", str(self.run)],
            env=self.env,
        )  # fmt: skip
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            return await action(session)

    def http(self, seed: Path, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One tools/call over HTTP with a freshly minted cml1 lease (A33)."""
        import httpx

        from comms.cli import _hello
        from comms.core.auth import lease_format

        client, raw = lease_format.read_helper(seed)
        lease = lease_format.mint(raw, client, _hello(str(self.run))(), now=datetime.now(UTC))
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments,
                       "_meta": {"io.modelcontextprotocol/protocolVersion": PROTOCOL,
                                 "io.modelcontextprotocol/clientCapabilities": {}}},
        }  # fmt: skip
        headers = {
            "Mcp-Protocol-Version": PROTOCOL,
            "Mcp-Method": "tools/call",
            "Mcp-Name": name,
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {lease}",
        }
        response = httpx.post(
            f"http://127.0.0.1:{self.port}/mcp", json=body, headers=headers, timeout=30
        )
        result: dict[str, Any] = response.json()["result"]
        return result


def _until(predicate: Any, *, seconds: float = 15.0) -> bool:
    for _ in range(int(seconds / 0.1)):
        if predicate():
            return True
        time.sleep(0.1)
    return bool(predicate())


def drive_install_and_surfaces(root: Path) -> dict[str, Any]:
    """E11a: install, the write hold, the cutover, the doctor, clients, reads, writes, replay,
    audit, the refusals, and the three ways a daemon stops (SIGTERM, kill -9, a stale anchor)."""
    out: dict[str, Any] = {}
    d = Daemon(root / "main")
    provisioned = d.json(
        "keys", "provision", "--state-dir", str(d.state), "--runtime-dir", str(d.run)
    )
    db = d.state / "comms" / "comms.db"
    out["provision"] = (
        "comms-db-key" in provisioned["provisioned"]
        and db.read_bytes()[:16] != b"SQLite format 3\x00"
    )
    d.settings()
    d.start()
    try:
        d.json("client", "add", "--name", "smoke", "--helper-path", str(root / "seed"))
        seed = root / "seed"

        async def held(session: Any) -> Any:
            read = await session.call_tool("comms_location_list", {})
            write = await session.call_tool("comms_location_create",
                                             {"name": "Held", "request_id": "req_" + "h" * 26})  # fmt: skip
            return read, write

        read, write = asyncio.run(d.mcp(seed, held))
        out["held"] = (
            (not read.is_error)
            and write.is_error
            and (write.structured_content["error"]["code"] == "AUDIT_INTEGRITY_DEGRADED")
        )
        cut = d.json("cutover", "run")
        out["cutover"] = cut == {"phase": "COMPLETE", "writes_released": True}

        def doctor_ok() -> bool:
            return bool(d.json("doctor", "--state-dir", str(d.state))["ok"])

        out["doctor"] = _until(doctor_ok)

        async def listed(session: Any) -> Any:
            return [t.name for t in (await session.list_tools()).tools]

        from comms.mcp.catalog import TOOL_CATALOG

        out["stdio_tools"] = asyncio.run(d.mcp(seed, listed)) == [s.name for s in TOOL_CATALOG]

        async def reading(session: Any) -> Any:
            return await session.call_tool("comms_location_list", {})

        got = asyncio.run(d.mcp(seed, reading))
        out["stdio_read"] = (not got.is_error) and "items" in got.structured_content
        request = "req_" + "s" * 26
        first = d.http(seed, "comms_location_create", {"name": "Parramatta", "request_id": request})
        out["http_write"] = (not first["isError"]) and first["structuredContent"][
            "location"
        ].startswith("loc_")
        again = d.http(seed, "comms_location_create", {"name": "Parramatta", "request_id": request})
        listing = d.http(seed, "comms_location_list", {})["structuredContent"]["items"]
        out["replay"] = (
            again["structuredContent"]["location"] == first["structuredContent"]["location"]
            and again["structuredContent"]["replayed"] is True
            and [i["name"] for i in listing].count("Parramatta") == 1
        )
        report = d.json("audit", "verify", "--all")
        out["verify_all"] = report == {
            "ok": True,
            "legacy": "ok",
            "lineage": "ok",
            "comms": "ok",
            "problems": [],
        }
        second = subprocess.run(
            [COMMS, "selftest-daemon", "--state-dir", str(d.state), "--runtime-dir", str(d.run)],
            env=d.env, capture_output=True, text=True, timeout=60, check=False,
        )  # fmt: skip
        out["second_daemon"] = second.returncode != 0 and "already running" in second.stderr
        no_tty = d.comms("credential", "set", "telegram-bot-token", stdin=subprocess.PIPE)
        out["credential_no_tty"] = no_tty.returncode != 0 and "interactively" in no_tty.stderr
        stale_anchor = (d.state / "comms" / "anchor" / "head.anchor").read_bytes()
        d.http(seed, "comms_location_create", {"name": "After", "request_id": "req_" + "t" * 26})

        out["sigterm"] = d.stop() == 0 and not (d.run / "admin.sock").exists()
        d.start()
        out["restart_clean"] = (
            _until(doctor_ok) and d.json("audit", "verify", "--all")["ok"] is True
        )
        d.http(seed, "comms_location_create", {"name": "Mid", "request_id": "req_" + "k" * 26})
        d.stop(signal.SIGKILL)  # no cleanup at all
        d.start()
        out["kill9_restart"] = d.json("audit", "verify", "--all")["ok"] is True and _until(
            doctor_ok
        )
        d.stop()

        anchor = d.state / "comms" / "anchor" / "head.anchor"
        anchor.write_bytes(stale_anchor)  # the anchor falls behind the chain head
        d.start()
        stale_read = d.http(seed, "comms_location_list", {})
        stale_write = d.http(
            seed, "comms_location_create", {"name": "X", "request_id": "req_" + "x" * 26}
        )
        repaired = d.json("audit", "repair")
        healed = d.http(
            seed, "comms_location_create", {"name": "Y", "request_id": "req_" + "y" * 26}
        )
        out["stale_anchor"] = (
            not stale_read["isError"]
            and stale_write["isError"]
            and stale_write["structuredContent"]["error"]["code"] == "AUDIT_INTEGRITY_DEGRADED"
            and repaired["repaired"] is True
            and not healed["isError"]
        )
    finally:
        if d.proc is not None and d.proc.poll() is None:
            d.stop()

    wrong = Daemon(root / "wrong")
    wrong.json(
        "keys", "provision", "--state-dir", str(wrong.state), "--runtime-dir", str(wrong.run)
    )
    wrong.settings()
    key = next((wrong.state / "comms" / "secrets" / "comms-db-key").iterdir())
    key.write_bytes(os.urandom(32))
    proc = wrong.start(wait=False)
    code = proc.wait(timeout=60)
    stderr = proc.stderr.read() if proc.stderr else ""
    out["wrong_key"] = (
        code != 0 and "does not open comms.db" in stderr and not (wrong.run / "admin.sock").exists()
    )
    return out


def _source_backup(root: Path) -> dict[str, Any]:
    """A directory prepared elsewhere and exported as a real signed, encrypted backup."""
    from comms.core.backup import age
    from comms.core.backup.export_import import export
    from comms.core.campaigns import directory as d
    from comms.core.keys import rotate as rot
    from tests.core import schema_fixtures as fx
    from tests.core.audit.legacy_fixtures import comms_world

    now = datetime.now(UTC)
    source = root / "source"
    source.mkdir(mode=0o700)
    w = comms_world(source)
    rot.rotate(w["writer"], w["store"], "backup-key", material=os.urandom(32),
               prove=lambda m: None, now=now)  # fmt: skip
    conn = w["conn"]
    loc = d.add_location(conn, "Parramatta", now=now)
    rcp = d.add_recipient(conn, now=now, display_name="Sara")
    d.add_contact_point(conn, rcp, "whatsapp", "+61400000001", normalize=fx.wa, now=now)
    d.add_destination(conn, loc, "telegram", "channel:1234567890", "MQ Society",
                      normalize=fx.tg, now=now)  # fmt: skip
    identity = age.generate_identity()
    exported = export(w["writer"], w["store"], age.recipient_of(identity), {}, now=now)
    base = root / "restore"
    for suffix, data in ((".age", exported.ciphertext), (".sig", exported.sidecar)):
        path = base.with_suffix(suffix)
        path.write_bytes(data)
        path.chmod(0o600)
    key = root / "restore.key"
    key.write_text(identity + "\n", encoding="ascii")
    key.chmod(0o600)
    conn.close()
    return {"base": base, "key": key, "signer": exported.signer_key_id, "rcp": rcp}


def _campaign(d: Daemon, rcp: str, title: str) -> str:
    cmp = str(d.json("campaign", "create", "--title", title)["result"]["campaign"])
    d.json("campaign", "set-content", "--campaign", cmp, "--content", '{"canonical": "Salaam"}')
    d.json("campaign", "set-targets", "--campaign", cmp, "--targets",
           json.dumps({"recipients": [rcp]}), "--transports", '["whatsapp"]')  # fmt: skip
    d.json("campaign", "validate", "--campaign", cmp)
    return cmp


def _jobs(d: Daemon, cmp: str) -> dict[str, int]:
    status = d.json("campaign", "status", "--campaign", cmp)["result"]
    return dict(status.get("jobs") or {})


def drive_directory_and_campaigns(root: Path) -> dict[str, Any]:
    """E11b: a backup seeds the directory; a campaign is delivered by the daemon; a scheduled
    campaign survives kill -9 and runs once; a cursor-key rotation invalidates old cursors."""
    out: dict[str, Any] = {}
    d = Daemon(root / "dir")
    d.json("keys", "provision", "--state-dir", str(d.state), "--runtime-dir", str(d.run))
    d.settings()
    d.start()
    try:
        d.json("cutover", "run")
        seed = root / "seed2"
        d.json("client", "add", "--name", "smoke2", "--helper-path", str(seed))
        backup = _source_backup(root)
        staged = d.json("backup", "import", "stage", "--from", str(backup["base"]), "--identity",
                        str(backup["key"]), "--trust-key", backup["signer"], "--adopt")  # fmt: skip
        committed = d.json("backup", "import", "commit", "--handle", staged["handle"])
        groups = d.http(seed, "comms_group_list", {})["structuredContent"]["items"]
        out["backup_seed"] = committed["committed"] is True and [g["name"] for g in groups] == [
            "MQ Society"
        ]

        cmp = _campaign(d, backup["rcp"], "Now")
        d.json("campaign", "send", "--campaign", cmp)
        out["campaign_delivered"] = _until(
            lambda: (jobs := _jobs(d, cmp)) and "PENDING" not in jobs and sum(jobs.values()) == 1,
            seconds=30,
        )

        later = _campaign(d, backup["rcp"], "Later")
        at = datetime.fromtimestamp(time.time() + 6, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        d.json("campaign", "schedule", "--campaign", later, "--at", at)
        d.stop(signal.SIGKILL)  # before it is due
        d.start()
        out["scheduled_once"] = _until(
            lambda: (jobs := _jobs(d, later)) and "PENDING" not in jobs and sum(jobs.values()) == 1,
            seconds=45,
        )

        grp = groups[0]["group"]
        page = d.http(seed, "comms_context_recent", {"group": grp, "limit": 2})["structuredContent"]
        cursor = page["next_cursor"]
        d.json("keys", "rotate", "cursor-key")
        stale = d.http(seed, "comms_context_page", {"cursor": cursor})
        fresh = d.http(seed, "comms_context_recent", {"group": grp, "limit": 2})
        out["cursor_rotation"] = bool(cursor) and stale["isError"] and not fresh["isError"]
        out["verify_after"] = d.json("audit", "verify", "--all")["ok"] is True
    finally:
        if d.proc is not None and d.proc.poll() is None:
            d.stop()
    return out
