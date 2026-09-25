#!/usr/bin/env python3
"""End-to-end smoke across every Phase-1 and Phase-2 function.

This is not the test suite. The suite proves each unit; this drives the
*shipped artifacts* end to end in one run — the real demo server over a real
TCP socket, the real SQLite schema on disk, real Unix sockets, the real CLI
and the real ingress into the real coordinator — and prints one ledger with
an exit code. There is no consent agent (comms spec v0.2).

It mutates nothing outside its own sandbox: no service accounts, no
keychain writes, no Telegram, no host paths.

    uv run python scripts/e2e_smoke.py [--verbose]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
CLI = Path(sys.executable).parent / "telegram-mcp"  # the installed console script

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

# The retired Phase-1 demo server (comms v0.3 A3), run for its historical checks only.
LEGACY_DEMO = """
import sys, uvicorn
from comms.transports.telegram.config import DemoConfig
from comms.transports.telegram.server import create_app
config = DemoConfig(host="127.0.0.1", port=int(sys.argv[1]))
uvicorn.run(create_app(config), host=config.host, port=config.port, access_log=False)
"""


@dataclass
class Ledger:
    rows: list[tuple[str, str, str, str]] = field(default_factory=list)
    verbose: bool = False

    def run(self, area: str, name: str, check: Callable[[], Any]) -> Any:
        try:
            detail = check()
        except _Skip as skip:
            self.rows.append((area, name, SKIP, str(skip)))
            return None
        except Exception as exc:  # noqa: BLE001 -- the ledger records every failure
            detail = f"{type(exc).__name__}: {exc}"
            if self.verbose:
                traceback.print_exc()
            self.rows.append((area, name, FAIL, detail))
            return None
        self.rows.append((area, name, PASS, "" if detail is None else str(detail)))
        return detail

    @property
    def failures(self) -> int:
        return sum(1 for row in self.rows if row[2] == FAIL)

    def report(self) -> None:
        width = max(len(row[1]) for row in self.rows) + 2
        current = None
        for area, name, status, detail in self.rows:
            if area != current:
                print(f"\n{area}")
                current = area
            mark = {PASS: "ok  ", FAIL: "FAIL", SKIP: "skip"}[status]
            line = f"  {mark} {name.ljust(width)}{detail}"
            print(line.rstrip())
        counts = {
            status: sum(1 for r in self.rows if r[2] == status) for status in (PASS, FAIL, SKIP)
        }
        print(
            f"\n{counts[PASS]} passed, {counts[FAIL]} failed, {counts[SKIP]} skipped"
            f" — {len(self.rows)} checks"
        )


class _Skip(Exception):
    """A check that cannot apply on this host."""


def free_port() -> int:
    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_for_port(port: int, *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with closing(socket.socket()) as probe:
            probe.settimeout(0.3)
            try:
                probe.connect(("127.0.0.1", port))
            except OSError:
                time.sleep(0.15)
            else:
                return
    raise TimeoutError(f"port {port} never opened")


def port_is_closed(port: int) -> bool:
    with closing(socket.socket()) as probe:
        probe.settimeout(0.3)
        try:
            probe.connect(("127.0.0.1", port))
        except OSError:
            return True
        return False


# --------------------------------------------------------------------------
# Phase 1: contracts, validation, configuration
# --------------------------------------------------------------------------


def phase1_contracts(ledger: Ledger) -> None:
    area = "Phase 1 — contracts and validation"

    def contracts():
        from comms.transports.telegram.contract import EXPECTED_TOOLS, load_contracts

        contracts = load_contracts()
        assert len(contracts) == 10, len(contracts)
        assert set(contracts) == set(EXPECTED_TOOLS)
        return "10 tools"

    def packaged_check():
        done = subprocess.run(
            [sys.executable, str(REPO / "scripts/extract_contracts.py"), "--check"],
            capture_output=True,
            text=True,
            cwd=REPO,
            check=False,
            timeout=120,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        return done.stdout.strip()

    def hoisted_defs():
        from comms.transports.telegram.contract import load_contracts

        schema = load_contracts()["telegram_get_messages"].output_schema
        text = json.dumps(schema)
        assert "$defs" in schema, "C3: definitions must be hoisted to the assembled root"
        assert "#/$defs/" in text, "C3: references must resolve against the root"
        assert "#/properties/data/$defs" not in text, "C3: definitions still buried under data"
        return f"{len(schema['$defs'])} definitions at the root"

    def validation_bounds():
        from comms.transports.telegram.contract import load_contracts
        from comms.transports.telegram.validation import ArgumentError, validate_arguments

        contract = load_contracts()["telegram_list_chats"]
        good = validate_arguments(contract, {"project_ref": "tpr_" + "a" * 26})
        assert good["limit"] == 20, good
        for bad in ({}, {"project_ref": "nope"}, {"project_ref": "tpr_" + "a" * 26, "limit": 101}):
            try:
                validate_arguments(contract, bad)
            except ArgumentError:
                continue
            raise AssertionError(f"accepted {bad!r}")
        return "defaults applied, three refusals"

    def config_refusals():
        from comms.transports.telegram.config import DemoConfig, validate_environment

        DemoConfig(host="127.0.0.1", port=8766)
        for env in ({"TELEGRAM_API_HASH": "x"}, {"TELEGRAM_API_ID": "1"}):
            try:
                validate_environment(env)
            except ValueError:
                continue
            raise AssertionError(f"accepted {env!r}")
        for host in ("0.0.0.0", "localhost", "::"):
            refused = False
            try:
                DemoConfig(host=host, port=8766)
            except Exception:  # noqa: BLE001 -- pydantic or ValueError, both are refusals
                refused = True
            assert refused, f"accepted host {host!r}"
        return "credentials and non-loopback binds refused"

    def dispatch_closed():
        from comms.transports.telegram.dispatch import dispatch

        assert dispatch("telegram_status", {}).is_error is False
        assert dispatch("telegram_list_chats", {"project_ref": "tpr_" + "a" * 26}).is_error is True
        assert dispatch("not_a_tool", {}).is_error is True
        return "status ok, sensitive closed, unknown refused"

    ledger.run(area, "ten frozen contracts load", contracts)
    ledger.run(area, "packaged contracts match source", packaged_check)
    ledger.run(area, "C3 schema definitions hoisted", hoisted_defs)
    ledger.run(area, "argument validation bounds", validation_bounds)
    ledger.run(area, "safe_demo configuration refusals", config_refusals)
    ledger.run(area, "closed dispatch", dispatch_closed)


# --------------------------------------------------------------------------
# Phase 1: the real server over a real socket
# --------------------------------------------------------------------------


def phase1_live_server(ledger: Ledger) -> None:
    area = "Phase 1 — retired demo MCP server, historical (real TCP)"
    import httpx
    from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY

    port = free_port()
    # comms v0.3 retired the `demo` verb (A3); the historical server is launched directly.
    server = subprocess.Popen(
        [sys.executable, "-c", LEGACY_DEMO, str(port)],
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}/mcp"

    def post(method: str, params: dict[str, Any], *, raw: bytes | None = None):
        headers = {
            "Mcp-Protocol-Version": "2026-07-28",
            "Mcp-Method": method,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if method == "tools/call":
            headers["Mcp-Name"] = params["name"]
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": {
                **params,
                "_meta": {
                    PROTOCOL_VERSION_META_KEY: "2026-07-28",
                    CLIENT_CAPABILITIES_META_KEY: {},
                },
            },
        }
        with httpx.Client(timeout=15) as client:
            if raw is not None:
                return client.post(url, headers=headers, content=raw)
            return client.post(url, headers=headers, json=body)

    try:
        ledger.run(area, "server binds loopback", lambda: (wait_for_port(port), f"port {port}")[1])

        def tools_list():
            response = post("tools/list", {})
            assert response.status_code == 200, response.text
            names = [tool["name"] for tool in response.json()["result"]["tools"]]
            assert len(names) == 10, names
            assert names[0] == "telegram_status"
            return f"{len(names)} tools advertised"

        def status_call():
            response = post("tools/call", {"name": "telegram_status", "arguments": {}})
            assert response.status_code == 200, response.text
            data = response.json()["result"]["structuredContent"]["data"]
            assert data["connected"] is False and data["authorised"] is False
            return "connected=false, authorised=false"

        def sensitive_closed():
            response = post(
                "tools/call",
                {"name": "telegram_list_chats", "arguments": {"project_ref": "tpr_" + "a" * 26}},
            )
            body = response.json()["result"]
            assert body["isError"] is True, body
            assert body["structuredContent"]["error"]["code"] == "POLICY_UNCONFIGURED", body
            # the human-readable text must stay non-enumerating
            text = body["content"][0]["text"]
            assert "POLICY_UNCONFIGURED" not in text and "tpr_" not in text, text
            return "POLICY_UNCONFIGURED, non-enumerating text"

        def unknown_tool():
            body = post("tools/call", {"name": "telegram_nope", "arguments": {}}).json()["result"]
            assert body["isError"] is True
            return "refused"

        def duplicate_keys():
            meta = json.dumps(
                {PROTOCOL_VERSION_META_KEY: "2026-07-28", CLIENT_CAPABILITIES_META_KEY: {}}
            )
            raw = (
                '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":'
                '{"name":"telegram_status","arguments":{},"arguments":{},'
                f'"_meta":{meta}}}}}'
            ).encode()
            response = post("tools/call", {"name": "telegram_status"}, raw=raw)
            assert response.status_code >= 400, response.status_code
            return f"rejected at the protocol level ({response.status_code})"

        def privacy_headers():
            response = post("tools/call", {"name": "telegram_status", "arguments": {}})
            cache = response.headers.get("cache-control", "")
            assert "no-store" in cache, cache
            assert "mcp-session-id" not in response.headers
            body = response.text.lower()
            for leak in ("api_hash", "api_id", "session", "bearer"):
                assert leak not in body, leak
            return "private/no-store, no session id, no credential echo"

        ledger.run(area, "tools/list advertises ten", tools_list)
        ledger.run(area, "telegram_status succeeds", status_call)
        ledger.run(area, "sensitive tool fails closed", sensitive_closed)
        ledger.run(area, "unknown tool refused", unknown_tool)
        ledger.run(area, "duplicate JSON keys rejected", duplicate_keys)
        ledger.run(area, "wire privacy headers", privacy_headers)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
    ledger.run(
        area,
        "port closed after shutdown",
        lambda: (
            "closed"
            if port_is_closed(port)
            else (_ for _ in ()).throw(AssertionError("port still open"))
        ),
    )


# --------------------------------------------------------------------------
# Phase 2a: keys, storage, authority
# --------------------------------------------------------------------------


def phase2a_core(ledger: Ledger, sandbox: Path) -> dict[str, Any]:
    area = "Phase 2a — keys, storage, authority"
    state: dict[str, Any] = {}

    def keys():
        from comms.transports.telegram.keys.store import FILE_BACKED_KEYS, key_id, provision_missing

        # Phases 2 and 3: FILE_BACKED_KEYS now covers the three signing rows,
        # and doctor/CLI both report every row in it.
        created = provision_missing(sandbox / "keys", phases=(2, 3))
        assert set(created) == set(FILE_BACKED_KEYS), created
        ids = {name: key_id(name) for name in sorted(FILE_BACKED_KEYS)}
        assert len(set(ids.values())) == len(ids), "key ids must differ per purpose"
        for path in (sandbox / "keys").iterdir():
            assert oct(path.stat().st_mode)[-3:] == "600", path
        state["keys"] = ids
        return f"{len(ids)} keys, distinct ids, 0600"

    def database():
        from comms.transports.telegram.storage.db import open_db

        conn = open_db(sandbox / "db" / "meta.db")
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert len(tables) >= 19, sorted(tables)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        state["conn"] = conn
        return f"{len(tables)} tables, foreign_keys on, quick_check ok"

    def c2_rule():
        conn = state["conn"]
        _seed_authority(conn)
        try:
            conn.execute(
                "INSERT INTO client_projects(client_id, project_id, egress_level,"
                " excerpt_max_codepoints, created_at, updated_at)"
                " VALUES (1, 1, 'excerpt', NULL, '2026-09-22T00:00:00Z', '2026-09-22T00:00:00Z')"
            )
        except sqlite3.IntegrityError:
            return "excerpt with NULL width rejected"
        raise AssertionError("C2: NULL excerpt width was accepted")

    def c4_rule():
        conn = state["conn"]
        conn.execute(
            "INSERT INTO projects(id, account_id, project_ref, slug, display_name, enabled,"
            " project_epoch, created_at, updated_at)"
            " VALUES (2, 1, ?, 's2', 'S2', 1, 1, '2026-09-22T00:00:00Z', '2026-09-22T00:00:00Z')",
            ("tpr_" + "z" * 26,),
        )
        try:
            conn.execute(
                "INSERT INTO project_peers(project_id, peer_id, membership_kind, created_at,"
                " updated_at) VALUES (2, 1, 'primary', '2026-09-22T00:00:00Z', '2026-09-22T00:00:00Z')"
            )
        except sqlite3.IntegrityError:
            conn.execute(
                "INSERT INTO project_peers(project_id, peer_id, membership_kind, created_at,"
                " updated_at) VALUES (2, 1, 'shared', '2026-09-22T00:00:00Z', '2026-09-22T00:00:00Z')"
            )
            conn.commit()
            return "undeclared overlap rejected, shared accepted"
        raise AssertionError("C4: undeclared overlap was accepted")

    def authority():
        from comms.transports.telegram.authority.policy import (
            AuthorityRequest,
            ClientProjectGrant,
            ClientState,
            Denial,
            ProjectState,
            evaluate,
            make_view,
        )

        client, project, peer = "tcl_" + "b" * 26, "tpr_" + "d" * 26, "user:5001"
        view = make_view(
            clients={
                client: ClientState(
                    client_ref=client, enabled=True, principal_ref="prn_" + "a" * 26
                )
            },
            projects={project: ProjectState(project_ref=project, enabled=True, project_epoch=1)},
            grants={
                (client, project): ClientProjectGrant(True, False, "full_text", None, "0" * 64)
            },
            memberships={project: {peer}},
            owner_allows={peer},
            policy_epoch=1,
            security_epoch=1,
        )
        request = AuthorityRequest(
            operation="read", client_ref=client, project_refs=(project,), peer_identity=peer
        )
        allowed = evaluate(view, request)
        assert not isinstance(allowed, Denial), allowed
        denied_view = make_view(
            clients=view.clients,
            projects=view.projects,
            grants={},
            memberships=view.memberships,
            owner_allows=view.owner_allows,
        )
        denial = evaluate(denied_view, request)
        assert isinstance(denial, Denial) and denial.code == "NOT_ACCESSIBLE", denial
        state["view"] = view
        return "allow with grant, NOT_ACCESSIBLE without"

    def cursors():
        from comms.transports.telegram.authority.cursors import (
            CursorError,
            CursorPresenter,
            ProjectScopeEntry,
            check_cursor,
            mint_cursor,
        )
        from comms.transports.telegram.storage.db import bind_cursor_store

        store = bind_cursor_store(state["conn"])
        entry = ProjectScopeEntry(
            project_ref="tpr_" + "d" * 26,
            project_epoch=1,
            can_read=True,
            can_cross_search=False,
            egress_level="full_text",
            excerpt_limit=None,
        )
        presenter = CursorPresenter(
            principal="prn_" + "a" * 26,
            client="tcl_" + "b" * 26,
            account="tga_" + "c" * 26,
            tool="telegram_get_messages",
            request={"peer_ref": "tgp_" + "e" * 26, "limit": 20},
            policy_epoch=1,
            security_epoch=1,
            scope=(entry,),
        )
        keys = {"cursor_key": b"\x11" * 32, "privacy_key": b"\x22" * 32}
        ref = mint_cursor(
            store,
            presenter=presenter,
            state={"offset_id": 7},
            now=1_000_000.0,
            runtime_id=b"\x33" * 16,
            **keys,
        )
        record = check_cursor(
            store, ref=ref, presenter=presenter, now=1_000_010.0, runtime_id=b"\x33" * 16, **keys
        )
        assert record.state == {"offset_id": 7}
        outcomes = {}
        for label, kwargs in (
            ("restart", {"runtime_id": b"\x44" * 16}),
            ("policy", {"presenter": CursorPresenter(**{**presenter.__dict__, "policy_epoch": 2})}),
        ):
            probe = mint_cursor(
                store,
                presenter=presenter,
                state={},
                now=1_000_000.0,
                runtime_id=b"\x33" * 16,
                **keys,
            )
            call = {
                "ref": probe,
                "presenter": presenter,
                "now": 1_000_010.0,
                "runtime_id": b"\x33" * 16,
                **keys,
            }
            call.update(kwargs)
            try:
                check_cursor(store, **call)
            except CursorError as exc:
                outcomes[label] = exc.code
        assert outcomes == {"restart": "INVALID_CURSOR", "policy": "CURSOR_POLICY_CHANGED"}, (
            outcomes
        )
        return "round trip, restart and policy-change invalidation"

    def epochs():
        from comms.transports.telegram.authority.epochs import PresenceRequired, set_locked
        from comms.transports.telegram.storage.db import bind_epoch_state, save_epoch_state

        epoch_state = bind_epoch_state(state["conn"])
        assert epoch_state["security_state"]["security_epoch"] == 1
        assert set_locked(epoch_state, True) == 2
        try:
            set_locked(epoch_state, False)
        except PresenceRequired:
            pass
        else:
            raise AssertionError("unlock without presence was allowed")
        assert set_locked(epoch_state, False, presence=True) == 3
        save_epoch_state(state["conn"], epoch_state)
        reloaded = bind_epoch_state(state["conn"])
        assert reloaded["security_state"]["security_epoch"] == 3
        assert reloaded["security_state"]["locked"] == 0
        state["security_epoch"] = 3
        return "lock 1->2, unlock needs presence, 2->3, persisted"

    def settings():
        from comms.transports.telegram.storage.settings import (
            SETTINGS_REGISTRY,
            get_setting,
            set_setting,
        )

        assert get_setting(state["conn"], "exposure_budget.rolling_window_minutes") == 30
        set_setting(state["conn"], "audit.checkpoint_cadence_events", 250)
        assert get_setting(state["conn"], "audit.checkpoint_cadence_events") == 250
        try:
            set_setting(state["conn"], "telegram.last_query", "invoice")
        except ValueError:
            pass
        else:
            raise AssertionError("unknown setting key accepted")
        return f"{len(SETTINGS_REGISTRY)} allowlisted keys, unknown refused"

    def reserved_tables_empty():
        counts = {
            table: state["conn"].execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "disclosure_receipts",
                "exposure_ledger",
                "audit_checkpoints",
                "audit_events",
                "verification_keys",
            )
        }
        assert set(counts.values()) == {0}, counts
        return "five Phase-3 tables still empty"

    ledger.run(area, "key provisioning", keys)
    ledger.run(area, "database open path", database)
    ledger.run(area, "C2 excerpt-width rule", c2_rule)
    ledger.run(area, "C4 shared-membership rule", c4_rule)
    ledger.run(area, "authority intersection", authority)
    ledger.run(area, "cursor binding and invalidation", cursors)
    ledger.run(area, "epoch lock/unlock", epochs)
    ledger.run(area, "closed settings registry", settings)
    ledger.run(area, "Phase-3 tables reserved only", reserved_tables_empty)
    return state


def _seed_authority(conn: sqlite3.Connection) -> None:
    ts = "2026-09-22T00:00:00Z"
    conn.execute(
        "INSERT INTO accounts(id, account_ref, telegram_user_id, created_at, updated_at)"
        " VALUES (1, ?, 1001, ?, ?)",
        ("tga_" + "c" * 26, ts, ts),
    )
    conn.execute(
        "INSERT INTO principals(id, principal_ref, principal_key, auth_mode, created_at)"
        " VALUES (1, ?, 'k1', 'local', ?)",
        ("prn_" + "a" * 26, ts),
    )
    conn.execute(
        "INSERT INTO mcp_clients(id, principal_id, client_ref, auth_kind, auth_binding,"
        " client_kind, enabled, created_at)"
        " VALUES (1, 1, ?, 'bearer', 'b1', 'codex_local', 1, ?)",
        ("tcl_" + "b" * 26, ts),
    )
    conn.execute(
        "INSERT INTO policy_state(principal_id, account_id, mode, policy_epoch, include_archived,"
        " include_private, include_groups, include_channels, updated_at)"
        " VALUES (1, 1, 'allowlist', 1, 0, 1, 1, 1, ?)",
        (ts,),
    )
    conn.execute(
        "INSERT INTO projects(id, account_id, project_ref, slug, display_name, enabled,"
        " project_epoch, created_at, updated_at) VALUES (1, 1, ?, 's1', 'Ops', 1, 1, ?, ?)",
        ("tpr_" + "d" * 26, ts, ts),
    )
    conn.execute(
        "INSERT INTO peers(id, account_id, peer_ref, telegram_peer_type, telegram_peer_id,"
        " first_seen_at, last_seen_at) VALUES (1, 1, ?, 'user', 5001, ?, ?)",
        ("tgp_" + "e" * 26, ts, ts),
    )
    conn.execute(
        "INSERT INTO project_peers(project_id, peer_id, membership_kind, created_at, updated_at)"
        " VALUES (1, 1, 'primary', ?, ?)",
        (ts, ts),
    )
    conn.commit()


# --------------------------------------------------------------------------
# Phase 2a: IPC — leases, admin socket, tunnel pins
# --------------------------------------------------------------------------


def phase2a_ipc(ledger: Ledger, sandbox: Path, state: dict[str, Any]) -> None:
    area = "Phase 2a — IPC, leases, tunnel"

    def leases():
        from comms.transports.telegram.ipc.leases import LeaseError, mint_lease, verify_lease

        seed, client = b"\x03" * 32, "tcl_" + "b" * 26
        runtime_a, runtime_b = b"\x0a" * 16, b"\x0b" * 16
        token = mint_lease(seed=seed, client=client, epoch=7, now=1_000_000, runtime_id=runtime_a)
        claims = verify_lease(
            token, seeds={client: seed}, epoch=7, now=1_000_030, runtime_id=runtime_a
        )
        assert claims.exp == claims.iat + 60
        refusals = 0
        for kwargs in (
            {"epoch": 8, "now": 1_000_030, "runtime_id": runtime_a},  # epoch moved
            {"epoch": 7, "now": 1_000_066, "runtime_id": runtime_a},  # past skew
            {"epoch": 7, "now": 1_000_030, "runtime_id": runtime_b},  # restarted
        ):
            try:
                verify_lease(token, seeds={client: seed}, **kwargs)
            except LeaseError:
                refusals += 1
        assert refusals == 3, refusals
        return "round trip, then epoch/skew/restart refusals"

    def admin_socket():
        import asyncio

        from comms.transports.telegram.ipc.admin import AdminRouter, serve_admin
        from comms.transports.telegram.ipc.framing import (
            decode_json_frame,
            encode_json_frame,
            read_frame,
        )

        async def drive() -> str:
            with tempfile.TemporaryDirectory(dir="/tmp") as short:
                path = Path(short) / "admin.sock"
                seen: list[str] = []
                router = AdminRouter(
                    {
                        "lock status": lambda args: {
                            "locked": False,
                            "security_epoch": state.get("security_epoch", 1),
                        },
                        "lock": lambda args: {"locked": True},
                    },
                    control_handlers={"stop": lambda args: seen.append("stop") or {"ok": 1}},
                )
                server = await serve_admin(path, router)
                try:

                    async def call(payload: bytes) -> dict[str, Any]:
                        reader, writer = await asyncio.open_unix_connection(str(path))
                        try:
                            writer.write(len(payload).to_bytes(4, "big") + payload)
                            await writer.drain()
                            return decode_json_frame(await read_frame(reader, idle_s=5))
                        finally:
                            writer.close()

                    ok = await call(encode_json_frame({"cmd": "lock status"}))
                    assert ok["ok"] is True, ok
                    stray = await call(encode_json_frame({"cmd": "lock", "args": {"presence": {}}}))
                    assert stray["code"] == "MALFORMED_REQUEST", stray
                    allowed = await call(encode_json_frame({"cmd": "lock"}))
                    assert allowed["ok"] is True, allowed
                    unrouted = await call(encode_json_frame({"cmd": "audit verify"}))
                    assert unrouted["code"] == "NOT_AVAILABLE_IN_PHASE", unrouted
                    retired = await call(encode_json_frame({"cmd": "project rename"}))
                    assert retired["code"] == "RETIRED_IN_V0_3", retired
                    unknown = await call(encode_json_frame({"cmd": "drop everything"}))
                    assert unknown["code"] == "UNKNOWN_COMMAND", unknown
                    duplicate = await call(b'{"cmd": "lock status", "cmd": "lock"}')
                    assert duplicate["code"] == "MALFORMED_REQUEST", duplicate
                    control = await call(encode_json_frame({"control": "stop"}))
                    assert control["ok"] is True and seen == ["stop"], (control, seen)
                    mode = oct(path.stat().st_mode)[-3:]
                    assert mode == "660", mode
                    return "served on peer authority, presence refused, unknown, duplicates, stop, 0660"
                finally:
                    server.close()
                    await server.wait_closed()

        return asyncio.run(drive())

    def tunnel_pins():
        from comms.transports.telegram.ipc.tunnel import (
            TunnelPinError,
            add_pin,
            pin_history,
            rotate_binding,
            spki_digest,
            verify_pin,
        )

        store = sandbox / "keys"
        first, second = spki_digest(b"client-cert-one"), spki_digest(b"client-cert-two")
        add_pin(store, first, now=1_000)
        verify_pin(store, first, now=2_000)
        rotate_binding(store, second, now=3_000)
        verify_pin(store, second, now=4_000)
        for bad in (first,):
            try:
                verify_pin(store, bad, now=4_000)
            except TunnelPinError:
                break
        else:
            raise AssertionError("retired pin still accepted")
        history = pin_history(store)
        assert [record.spki for record in history] == [first, second], history
        return "pin, rotate, retired refused, history retained"

    ledger.run(area, "tgml1 lease wire", leases)
    ledger.run(area, "admin socket routing", admin_socket)
    ledger.run(area, "tunnel SPKI pins", tunnel_pins)


# --------------------------------------------------------------------------
# Phase 2a: runtime lifecycle, doctor, CLI
# --------------------------------------------------------------------------


def phase2a_runtime(ledger: Ledger, sandbox: Path) -> None:
    area = "Phase 2a — runtime, doctor, CLI"

    def lifecycle():
        import asyncio
        import threading

        from comms.transports.telegram.runtime.lifecycle import drain, run_lifecycle, startup

        steps: list[str] = []
        ctx = startup(
            None,
            mode="local",
            lock_path=sandbox / "runtime.lock",
            open_db=lambda c: steps.append("open_db"),
        )
        assert ctx.state == "READY", ctx.state
        assert len(ctx.runtime_id) == 16
        assert steps == ["open_db"], steps
        assert ctx.executed_steps[0] == "mint_runtime_id", ctx.executed_steps[:1]
        result = asyncio.run(drain(ctx))
        assert ctx.state == "DRAINING"
        assert "INTERNAL_ERROR" in str(result), result
        if ctx.lock is not None:
            ctx.lock.release()

        stopped = threading.Event()
        stopped.set()
        run_lifecycle(None, mode="local", stopped=stopped, lock_path=sandbox / "runtime2.lock")
        return "ordered startup, fresh runtime_id, DRAINING refuses, clean stop"

    def single_runtime():
        from comms.transports.telegram.runtime.lock import RuntimeActive, acquire_lock

        handle = acquire_lock(sandbox / "single.lock")
        try:
            acquire_lock(sandbox / "single.lock")
        except RuntimeActive:
            return "second start refused while the first holds the lock"
        finally:
            handle.release()
        raise AssertionError("two runtimes acquired the same lock")

    def off_state():
        from comms.transports.telegram.runtime.bootstrap import bootstrap_status

        os.environ["TELEGRAM_MCP_RUNTIME_DIR"] = str(sandbox / "absent-run")
        try:
            report = bootstrap_status()
        finally:
            del os.environ["TELEGRAM_MCP_RUNTIME_DIR"]
        assert report["state"] == "OFF", report
        assert report["ports"] == (), report
        return "OFF with no ports"

    def doctor_headless():
        from comms.transports.telegram.doctor import DoctorContext, doctor

        report = doctor(
            context=DoctorContext(store_dir=sandbox / "keys", db_path=sandbox / "db" / "meta.db"),
            off=True,
        )
        statuses = {check["name"]: check["status"] for check in report["checks"]}
        assert statuses["keys.inventory"] == "ok", statuses
        assert statuses["db.integrity"] == "ok", statuses
        assert not {"consent.selftest", "keys.pairing"} & set(statuses), statuses
        assert statuses["off.probe"] == "ok", statuses
        assert report["status"] == "ok", report
        return "keys, db and OFF probe all ok; no consent checks remain"

    def doctor_production_fails():
        from comms.transports.telegram.doctor import doctor

        report = doctor(production=True)
        assert report["status"] == "fail", report
        assert report["unmet_production_checks"], report
        return f"fails honestly, {len(report['unmet_production_checks'])} unmet checks"

    def cli_verbs():
        results = {}
        for args in (
            ["status"],
            ["doctor"],
            ["keys", "list", "--store-dir", str(sandbox / "keys")],
        ):
            done = subprocess.run(
                [str(CLI), *args],
                capture_output=True,
                text=True,
                cwd=REPO,
                check=False,
                timeout=120,
                env={**os.environ, "TELEGRAM_MCP_RUNTIME_DIR": str(sandbox / "absent-run")},
            )
            results[args[0]] = done.returncode
            assert done.returncode == 0, (args, done.stderr)
            json.loads(done.stdout)
        admin = subprocess.run(
            [str(CLI), "admin", "lock", "status", "--runtime-dir", str(sandbox / "absent-run")],
            capture_output=True,
            text=True,
            cwd=REPO,
            check=False,
            timeout=120,
        )
        assert admin.returncode == 3, (admin.returncode, admin.stderr)
        for verb in ("demo", "serve"):
            retired = subprocess.run(
                [str(CLI), verb], capture_output=True, text=True, cwd=REPO, check=False, timeout=60
            )
            assert retired.returncode == 8, (verb, retired.returncode)
            assert retired.stderr.strip() == (
                f"telegram-mcp {verb} is retired in comms v0.3; use comms mcp"
            ), retired.stderr
        return "status/doctor/keys emit JSON; admin reports a stopped runtime; demo/serve retired"

    def install_plans():
        outputs = {}
        for script, extra in (("install_service_users.sh", []), ("install_paths.sh", [])):
            done = subprocess.run(
                ["/bin/bash", str(REPO / "scripts" / script), "install", "--dry-run", *extra],
                capture_output=True,
                text=True,
                cwd=REPO,
                check=False,
                timeout=120,
                env={
                    **os.environ,
                    "TELEGRAM_MCP_RUNTIME_DIR": str(sandbox / "plan-run"),
                    "TELEGRAM_MCP_STATE_DIR": str(sandbox / "plan-state"),
                },
            )
            assert done.returncode == 0, (script, done.stderr)
            outputs[script] = done.stdout
        assert "/usr/bin/false" in outputs["install_service_users.sh"]
        assert "0770" in outputs["install_paths.sh"]
        assert not (sandbox / "plan-run").exists(), "dry run touched the host"
        return "both installers plan without touching anything"

    ledger.run(area, "runtime lifecycle and drain", lifecycle)
    ledger.run(area, "single-runtime lock", single_runtime)
    ledger.run(area, "OFF state reporting", off_state)
    ledger.run(area, "doctor headless", doctor_headless)
    ledger.run(area, "doctor --production fails honestly", doctor_production_fails)
    ledger.run(area, "CLI verbs", cli_verbs)
    ledger.run(area, "install plans are inert", install_plans)


# --------------------------------------------------------------------------
# Phase 4a: the authenticated catalogue, end to end
# --------------------------------------------------------------------------


def phase4a_catalogue(ledger: Ledger) -> None:
    """Real ingress over TCP into the real coordinator, owner-direct (comms spec v0.2).

    One event loop drives everything, because the SQLite connection is
    thread-bound: uvicorn serves as a task on it. No agent, no prompt.
    """
    area = "Phase 4a — authenticated catalogue"
    results: dict[str, Any] = {}

    def drive() -> dict[str, Any]:
        if results:
            return results
        import asyncio
        import secrets
        import tempfile

        import httpx
        import uvicorn
        from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY

        sys.path.insert(0, str(REPO))
        from comms.transports.telegram.disclosure.receipts import verify_proof
        from comms.transports.telegram.disclosure.verify import verify_persisted_receipt
        from comms.transports.telegram.ipc.leases import mint_lease
        from comms.transports.telegram.keys.store import provision_lease_seed, provision_missing
        from comms.transports.telegram.runtime.legacy_composition import build_runtime
        from comms.transports.telegram.storage.db import open_db
        from tests.authority_fixtures import seed_authority_rows

        client = "tcl_" + "a" * 26
        runtime = secrets.token_bytes(16)

        async def main() -> dict[str, Any]:
            with tempfile.TemporaryDirectory(dir="/tmp") as short:
                root = Path(short)
                keys = root / "keys"
                provision_missing(keys, phases=(2, 3))
                seed = provision_lease_seed(keys, client)
                conn = open_db(root / "meta.db")
                seed_authority_rows(conn)
                (root / "anchor").mkdir(mode=0o700)
                port = free_port()
                services = build_runtime(
                    conn,
                    key_dir=keys,
                    anchor_path=root / "anchor" / "anchor.json",
                    runtime_id=runtime,
                    port=port,
                )
                project = conn.execute("SELECT project_ref FROM projects").fetchone()[0]
                granted = services.admin_router.dispatch(
                    {
                        "cmd": "project grant-client",
                        "args": {
                            "project_ref": project,
                            "client_ref": client,
                            "egress_level": "full_text",
                        },
                    }
                )
                assert granted["ok"], granted
                server = uvicorn.Server(
                    uvicorn.Config(
                        services.ingress_app, host="127.0.0.1", port=port, log_level="warning"
                    )
                )
                serving = asyncio.create_task(server.serve())
                out: dict[str, Any] = {}
                try:
                    deadline = time.monotonic() + 30
                    while not server.started:
                        assert time.monotonic() < deadline, "ingress never started"
                        await asyncio.sleep(0.05)

                    async def post(name, arguments, token):
                        params = {
                            "name": name,
                            "arguments": arguments,
                            "_meta": {
                                PROTOCOL_VERSION_META_KEY: "2026-07-28",
                                CLIENT_CAPABILITIES_META_KEY: {},
                            },
                        }
                        headers = {
                            "Mcp-Protocol-Version": "2026-07-28",
                            "Mcp-Method": "tools/call",
                            "Mcp-Name": name,
                            "Accept": "application/json, text/event-stream",
                        }
                        if token is not None:
                            headers["Authorization"] = f"Bearer {token}"
                        async with httpx.AsyncClient(
                            base_url=f"http://127.0.0.1:{port}", timeout=60
                        ) as http:
                            return await http.post(
                                "/mcp",
                                headers=headers,
                                json={
                                    "jsonrpc": "2.0",
                                    "id": 1,
                                    "method": "tools/call",
                                    "params": params,
                                },
                            )

                    def lease():
                        return mint_lease(
                            seed=seed,
                            client=client,
                            epoch=1,
                            now=int(time.time()),
                            runtime_id=runtime,
                        )

                    ok = (await post("telegram_list_projects", {}, lease())).json()
                    body = ok["result"]["structuredContent"]
                    out["success"] = body
                    if body.get("ok"):
                        ref = body["meta"]["disclosure"]["receipt_ref"]
                        public = conn.execute(
                            "SELECT public_key_b64url FROM verification_keys"
                            " WHERE purpose = 'disclosure_proof'"
                        ).fetchone()[0]
                        d = body["meta"]["disclosure"]
                        out["persisted_ok"] = verify_persisted_receipt(conn, ref)
                        out["offline_ok"] = verify_proof(
                            d["proof_payload"],
                            proof_signature=d["proof_signature"],
                            proof_payload_sha256=d["proof_payload_sha256"],
                            public_key_b64url=public,
                        )
                    bad = [
                        await post("telegram_list_projects", {}, token)
                        for token in (None, "not-a-lease")
                    ]
                    out["bad"] = [(r.status_code, r.content) for r in bad]
                    other = await post(
                        "telegram_get_context",
                        {"project_ref": project, "message_ref": "tgm_" + "a" * 26},
                        lease(),
                    )
                    out["other"] = other.json()["result"]["structuredContent"]
                finally:
                    server.should_exit = True
                    await serving
                    conn.close()
                return out

        results.update(asyncio.run(main()))
        return results

    def real_success():
        out = drive()
        body = out["success"]
        assert body["ok"] is True, body
        assert out["persisted_ok"] and out["offline_ok"]
        payload = body["meta"]["disclosure"]["proof_payload"]
        assert payload["schema"] == "tg-mcp-disclosure/v2", payload["schema"]
        assert payload["authorization_mode"] == "owner_direct", payload
        return f"v2 owner-direct receipt {body['meta']['disclosure']['receipt_ref'][:10]}… verifies persisted and offline"

    def bad_bearers():
        out = drive()
        statuses = {status for status, _ in out["bad"]}
        bodies = {content for _, content in out["bad"]}
        assert statuses == {401} and len(bodies) == 1, out["bad"]
        return "missing and garbage bearers: 401, identical bytes"

    def others_refuse():
        out = drive()
        assert out["other"]["error"]["code"] == "AUTH_REQUIRED", out["other"]
        return "telegram_get_context without a session -> AUTH_REQUIRED, nothing retrieved"

    def demo_still_refuses():
        from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY
        from starlette.testclient import TestClient

        from comms.transports.telegram.config import DemoConfig
        from comms.transports.telegram.server import create_app

        params = {
            "name": "telegram_list_projects",
            "arguments": {},
            "_meta": {PROTOCOL_VERSION_META_KEY: "2026-07-28", CLIENT_CAPABILITIES_META_KEY: {}},
        }
        with TestClient(create_app(DemoConfig()), base_url="http://127.0.0.1:8766") as http:
            response = http.post(
                "/mcp",
                headers={
                    "Mcp-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "telegram_list_projects",
                    "Accept": "application/json, text/event-stream",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params},
            )
        code = response.json()["result"]["structuredContent"]["error"]["code"]
        assert code == "POLICY_UNCONFIGURED", code
        return "demo list_projects -> POLICY_UNCONFIGURED"

    ledger.run(area, "catalogue success through ingress, owner-direct", real_success)
    ledger.run(area, "bad bearers are 401, byte-identical", bad_bearers)
    ledger.run(area, "Telegram tools refuse without a session", others_refuse)
    ledger.run(area, "demo server still has no sensitive route", demo_still_refuses)


def phase4b_reads(ledger: Ledger) -> None:
    """The four Telegram tools through real ingress + coordinator, on a fake transport."""
    area = "Phase 4b — Telegram reads (fake transport)"
    results: dict[str, Any] = {}

    def drive() -> dict[str, Any]:
        if results:
            return results
        import asyncio
        import tempfile

        sys.path.insert(0, str(REPO))
        from tests.authority_fixtures import PROJECT_REF
        from tests.integration.test_phase4a_end_to_end import CODEX, call
        from tests.integration.test_phase4b_end_to_end import MARKER, _close, _world

        class _Patch:  # _world only needs chdir from monkeypatch
            def chdir(self, path: Any) -> None:
                os.chdir(path)

        async def main() -> dict[str, Any]:
            here = os.getcwd()
            out: dict[str, Any] = {}
            with tempfile.TemporaryDirectory(dir="/tmp") as short:
                world = await _world(Path(short), _Patch())
                try:
                    out["chats"] = await call(
                        world, CODEX, "telegram_list_chats", {"project_ref": PROJECT_REF}
                    )
                    out["messages"] = await call(
                        world,
                        CODEX,
                        "telegram_get_messages",
                        {"project_ref": PROJECT_REF, "peer_ref": world["refs"]["user:100"]},
                    )
                    out["unread"] = await call(
                        world, CODEX, "telegram_get_unread", {"project_ref": PROJECT_REF}
                    )
                    world["conn"].commit()
                    out["at_rest"] = any(
                        MARKER.encode() in p.read_bytes() for p in Path(short).glob("meta.db*")
                    )
                    out["calls"] = list(world["fake"].calls)
                finally:
                    await _close(world)
                    os.chdir(here)
            return out

        results.update(asyncio.run(main()))
        return results

    def list_and_read():
        out = drive()
        assert out["chats"]["ok"] and out["messages"]["ok"], (out["chats"], out["messages"])
        assert out["messages"]["meta"]["disclosure"]["receipt_ref"].startswith("tdr_")
        return f"{len(out['chats']['data']['chats'])} chats, {len(out['messages']['data']['messages'])} messages, receipts"

    def unread_exact():
        body = drive()["unread"]
        assert body["ok"] and body["data"]["total_is_exact"] is True, body
        return f"total_unread_visible={body['data']['total_unread_visible']}, exact"

    def no_body_at_rest():
        assert drive()["at_rest"] is False
        return "message marker absent from meta.db, -wal, -shm"

    def only_reviewed_calls():
        calls = set(drive()["calls"])
        assert calls <= {"messages.GetPeerDialogsRequest", "messages.GetHistoryRequest"}, calls
        return ", ".join(sorted(calls))

    ledger.run(area, "list_chats + get_messages through ingress with receipts", list_and_read)
    ledger.run(area, "get_unread total is exact over the project", unread_exact)
    ledger.run(area, "no message body at rest", no_body_at_rest)
    ledger.run(area, "only reviewed RPCs reached the transport", only_reviewed_calls)


def phase4c_reads(ledger: Ledger) -> None:
    """get_context and both searches through real ingress + coordinator, fake transport."""
    area = "Phase 4c — context and search (fake transport)"
    results: dict[str, Any] = {}

    def drive() -> dict[str, Any]:
        if results:
            return results
        import asyncio
        import tempfile

        sys.path.insert(0, str(REPO))
        from telethon.tl import types

        from comms.core.canonical import jcs_dumps
        from comms.transports.telegram.storage.refstore import RefStore
        from tests.authority_fixtures import BETA_REF, PROJECT_REF, seed_second_project
        from tests.integration.test_phase4a_end_to_end import CODEX, call
        from tests.integration.test_phase4b_end_to_end import _close, _world
        from tests.integration.test_phase4c_end_to_end import BASE, _hits

        class _Patch:  # _world only needs chdir from monkeypatch
            def chdir(self, path: Any) -> None:
                os.chdir(path)

        async def main() -> dict[str, Any]:
            here = os.getcwd()
            out: dict[str, Any] = {}
            with tempfile.TemporaryDirectory(dir="/tmp") as short:
                world = await _world(Path(short), _Patch())
                try:
                    conn, fake = world["conn"], world["fake"]
                    fake.script["messages.SearchRequest"] = lambda r: _hits(
                        r, {"user:100": [3, 2, 1]}
                    )
                    body = await call(
                        world,
                        CODEX,
                        "telegram_search_messages",
                        {"project_ref": PROJECT_REF, "query": "needle"},
                    )
                    out["search"] = body
                    if body.get("ok"):
                        out["digest_ok"] = (
                            body["meta"]["disclosure"]["proof_payload"]["canonical_coverage_digest"]
                            == hashlib.sha256(jcs_dumps(body["meta"]["coverage"])).hexdigest()
                        )
                    refs = RefStore(conn, account_id=1)
                    anchor = refs.message_ref(refs.peer_by_identity("user:100").row_id, 1)
                    conn.commit()
                    fake.script["messages.GetMessagesRequest"] = types.messages.Messages(
                        messages=[
                            types.Message(
                                id=1, peer_id=types.PeerUser(100), date=BASE, message="anchor"
                            )
                        ],
                        topics=[],
                        chats=[],
                        users=[],
                    )
                    out["context"] = await call(
                        world,
                        CODEX,
                        "telegram_get_context",
                        {
                            "project_ref": PROJECT_REF,
                            "message_ref": anchor,
                            "before": 1,
                            "after": 1,
                        },
                    )
                    seed_second_project(conn)
                    conn.execute("UPDATE policy_state SET include_archived = 1")
                    conn.commit()

                    def search_then_unshare(request):
                        conn.execute(
                            "DELETE FROM project_peers WHERE project_id = 2 AND peer_id ="
                            " (SELECT id FROM peers WHERE telegram_peer_id = 7)"
                        )
                        conn.commit()
                        return _hits(request, {"channel:7": [5]})

                    fake.script["messages.SearchRequest"] = search_then_unshare
                    before = conn.execute("SELECT count(*) FROM disclosure_receipts").fetchone()[0]
                    out["race"] = await call(
                        world,
                        CODEX,
                        "telegram_cross_project_search",
                        {"project_refs": [PROJECT_REF, BETA_REF], "query": "needle"},
                    )
                    out["race_receipts"] = (
                        conn.execute("SELECT count(*) FROM disclosure_receipts").fetchone()[0]
                        - before
                    )
                    out["calls"] = set(fake.calls)
                finally:
                    await _close(world)
                    os.chdir(here)
            return out

        results.update(asyncio.run(main()))
        return results

    def search_with_coverage():
        out = drive()
        body = out["search"]
        assert body["ok"] and body["meta"]["coverage"]["complete"] is True, body
        assert out["digest_ok"] is True
        return f"{len(body['data']['results'])} hits, coverage complete, digest signed"

    def context_window():
        body = drive()["context"]
        assert body["ok"] and body["meta"]["coverage"] is None, body
        return f"anchor + {len(body['data']['messages']) - 1} neighbours, receipt"

    def race_discarded():
        out = drive()
        assert out["race"]["error"]["code"] == "POLICY_CHANGED" and out["race_receipts"] == 0
        return "shared peer removed mid-flight -> POLICY_CHANGED, no receipt"

    def never_global():
        calls = drive()["calls"]
        assert "messages.SearchGlobalRequest" not in calls and "messages.SearchRequest" in calls
        return "per-peer messages.Search only"

    ledger.run(area, "search_messages with signed coverage", search_with_coverage)
    ledger.run(area, "get_context through ingress", context_window)
    ledger.run(area, "cross-project race discarded at step 8", race_discarded)
    ledger.run(area, "never a global search", never_global)


def _raise(detail: Any) -> None:
    raise AssertionError(str(detail))


def phase5a_operator(ledger: Ledger) -> None:
    """Phase 5a over a real admin socket: simulate, diff, commit; lock round-trip; audit."""
    area = "Phase 5a — operator surface"
    import asyncio
    import secrets as _secrets

    sys.path.insert(0, str(REPO))
    from comms.transports.telegram.ipc.admin import LEGACY_ADMIN_SURFACE, AdminRouter, serve_admin
    from comms.transports.telegram.ipc.framing import decode_json_frame, encode_json_frame
    from comms.transports.telegram.keys.store import provision_missing, set_store_dir
    from comms.transports.telegram.runtime.composition import admin_handlers
    from comms.transports.telegram.runtime.legacy_composition import legacy_admin_handlers
    from comms.transports.telegram.storage.db import open_db
    from tests.authority_fixtures import (
        BETA_REF,
        seed_authority_rows,
        seed_project_world,
        seed_second_project,
    )

    results: dict[str, Any] = {}

    async def main() -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as short:
            root = Path(short)
            provision_missing(root / "keys", phases=(2, 3))
            set_store_dir(root / "keys")
            conn = open_db(root / "meta.db")
            seed_authority_rows(conn)
            seed_project_world(conn)
            seed_second_project(conn)
            (root / "anchor").mkdir(mode=0o700)

            wiring = {
                "key_dir": root / "keys",
                "anchor_path": root / "anchor" / "anchor.json",
                "telegram": None,
            }
            production = AdminRouter(admin_handlers(conn, **wiring))
            results["retired"] = {
                cmd: production.dispatch({"cmd": cmd, "args": {}})["code"]
                for cmd in (
                    "policy simulate",
                    "policy diff",
                    "project disable",
                    "exposure status",
                    "auth headers",
                )
            }
            # Simulate/diff/commit are retired in comms v0.3; driven here on the historical surface.
            handlers = legacy_admin_handlers(
                conn,
                **wiring,
                seeds=lambda ref: None,
                runtime_id=_secrets.token_bytes(16),
                clock=time.time,
            )
            router = AdminRouter(handlers, surface=LEGACY_ADMIN_SURFACE)
            sock = root / "admin.sock"
            server = await serve_admin(sock, router)
            try:

                async def call(cmd: str, **args: Any) -> dict[str, Any]:
                    reader, writer = await asyncio.open_unix_connection(str(sock))
                    payload = encode_json_frame({"cmd": cmd, "args": args})
                    writer.write(len(payload).to_bytes(4, "big") + payload)
                    await writer.drain()
                    size = int.from_bytes(await reader.readexactly(4), "big")
                    body = await reader.readexactly(size)
                    writer.close()
                    return decode_json_frame(body)

                simulated = await call(
                    "policy simulate", command="project disable", args={"project_ref": BETA_REF}
                )
                results["simulated"] = simulated["data"]["diff"]
                results["diff"] = (await call("policy diff", staged=simulated["data"]["staged"]))[
                    "data"
                ]["diff"]
                await call("project disable", project_ref=BETA_REF)
                results["stale"] = (await call("policy diff", staged=simulated["data"]["staged"]))[
                    "code"
                ]
                results["lock"] = (await call("lock"))["data"]
                results["status"] = (await call("lock status"))["data"]
                results["unlock"] = (await call("unlock"))["data"]
                results["verify"] = (await call("audit verify"))["data"]
                results["drift"] = (await call("project drift"))["code"]
            finally:
                server.close()
                await server.wait_closed()
                conn.close()

    def drive() -> dict[str, Any]:
        if not results:
            asyncio.run(main())
        return results

    ledger.run(
        area,
        "production retires the policy, project, exposure and tgml1-issuance commands",
        lambda: (
            set(drive()["retired"].values()) == {"RETIRED_IN_V0_3"} or _raise(drive()["retired"])
        ),
    )
    ledger.run(
        area,
        "simulate then diff returns the staged change (historical surface)",
        lambda: drive()["simulated"] == drive()["diff"] or _raise("diff differs"),
    )
    ledger.run(
        area,
        "a committed change makes the staged diff stale",
        lambda: drive()["stale"] == "MALFORMED_REQUEST" or _raise(drive()["stale"]),
    )
    ledger.run(
        area,
        "lock is chained with a refreshed anchor",
        lambda: drive()["lock"]["anchor"] == "refreshed" or _raise(drive()["lock"]),
    )
    ledger.run(
        area,
        "lock status reads locked without writing",
        lambda: drive()["status"]["locked"] is True or _raise(drive()["status"]),
    )
    ledger.run(
        area,
        "unlock is chained with a refreshed anchor",
        lambda: drive()["unlock"]["anchor"] == "refreshed" or _raise(drive()["unlock"]),
    )
    ledger.run(
        area,
        "audit verify reports CLEAN integrity",
        lambda: drive()["verify"]["integrity"] == "CLEAN" or _raise(drive()["verify"]),
    )
    ledger.run(
        area,
        "a 5b command answers NOT_AVAILABLE_IN_PHASE",
        lambda: drive()["drift"] == "NOT_AVAILABLE_IN_PHASE" or _raise(drive()["drift"]),
    )


def phase_v03_cutover(ledger: Ledger) -> None:
    """comms v0.3 Part A: the constitutional cutover on a sandbox legacy DB, then verify --all."""
    area = "comms v0.3 Part A — cutover"
    sys.path.insert(0, str(REPO))
    from comms.core.audit import cutover
    from comms.core.audit.verify_all import verify_all
    from comms.transports.telegram.storage.authority_view import load_security
    from tests.core.audit.legacy_fixtures import comms_world, verify_keys
    from tests.core.campaign_helpers import NOW

    results: dict[str, Any] = {}

    def drive() -> dict[str, Any]:
        if not results:
            with tempfile.TemporaryDirectory(dir="/tmp") as short:
                world = comms_world(Path(short), bearer=True)
                port = world["port"]
                results["phase"] = cutover.run_cutover(
                    world["conn"], port, world["writer"], now=NOW
                )
                results["report"] = verify_all(world["conn"], port.conn, verify_keys(world))
                results["seeds"] = sorted(p.name for p in port.key_dir.glob("lease-seed.*"))
                results["epoch"] = load_security(port.conn)[0]
                results["rerun"] = cutover.run_cutover(
                    world["conn"], port, world["writer"], now=NOW
                )
                world["conn"].close()
                port.conn.close()
        return results

    ledger.run(
        area,
        "run_cutover reaches COMPLETE on a sandbox legacy DB",
        lambda: drive()["phase"] == "COMPLETE" or _raise(drive()["phase"]),
    )
    ledger.run(
        area,
        "verify --all is green: legacy seal, lineage, comms genesis, anchor",
        lambda: drive()["report"].ok or _raise(drive()["report"].problems),
    )
    ledger.run(
        area,
        "tgml1 retired: no seed left, security epoch bumped once",
        lambda: (drive()["seeds"], drive()["epoch"]) == ([], 2) or _raise(drive()),
    )
    ledger.run(
        area,
        "a rerun is a no-op at COMPLETE",
        lambda: drive()["rerun"] == "COMPLETE" or _raise(drive()["rerun"]),
    )


def _free_port() -> int:
    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def phase_v03_comms(ledger: Ledger) -> None:
    """comms v0.3 Part D: comms mcp end to end against fake adapters — the real stdio proxy, the
    real HTTP /mcp (local cml1 and remote OAuth), the real CLI and the real admin socket, over
    the real composition (comms/runtime/comms_runtime.py)."""
    area = "comms v0.3 Part D — comms mcp"
    results: dict[str, Any] = {}

    def drive() -> dict[str, Any]:
        if not results:
            import asyncio

            with tempfile.TemporaryDirectory(dir="/tmp") as short:
                results.update(asyncio.run(_comms_drive(Path(short))))
        return results

    def verdict(key: str) -> Any:
        return drive().get(key) is True or _raise(drive().get(key))

    ledger.run(area, "tools/list over stdio matches the catalog", lambda: verdict("stdio_tools"))
    ledger.run(area, "a read over stdio reaches its service", lambda: verdict("stdio_read"))
    ledger.run(
        area,
        "context read over HTTP carries provenance and a cur_ cursor",
        lambda: verdict("context_read"),
    )
    ledger.run(area, "context page resumes by cursor", lambda: verdict("context_page"))
    ledger.run(area, "a write over HTTP records one operation", lambda: verdict("http_write"))
    ledger.run(
        area,
        "request-id replay returns the first result, no second effect",
        lambda: verdict("replay"),
    )
    ledger.run(
        area,
        "a campaign runs through the real CLI over the admin socket",
        lambda: verdict("campaign_cli"),
    )
    ledger.run(area, "an admin operation reaches the provider once", lambda: verdict("admin_op"))
    ledger.run(area, "WhatsApp mark_read is its own audited write", lambda: verdict("whatsapp"))
    ledger.run(area, "capability for a group names every actor", lambda: verdict("capability"))
    ledger.run(
        area, "OAuth: a local AS issues a token the remote /mcp accepts", lambda: verdict("oauth")
    )
    ledger.run(area, "bad cml1 leases are 401", lambda: verdict("bad_lease"))
    ledger.run(area, "unknown tool is TOOL_NOT_FOUND over HTTP", lambda: verdict("unknown_tool"))
    ledger.run(area, "duplicate JSON keys rejected over HTTP", lambda: verdict("duplicate_keys"))
    ledger.run(
        area, "client add and oauth approve over the admin socket", lambda: verdict("operator")
    )
    ledger.run(
        area, "audit verify --all is clean after every surface wrote", lambda: verdict("verify_all")
    )
    ledger.run(area, "a degraded audit trail refuses new writes", lambda: verdict("degraded"))


async def _comms_drive(root: Path) -> dict[str, Any]:
    """Build the composition over fakes, serve it, and drive every surface; one verdict per key."""
    import asyncio
    import base64
    import hashlib
    from datetime import UTC, datetime
    from urllib.parse import parse_qs, urlparse

    import httpx
    import uvicorn
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    sys.path.insert(0, str(REPO))
    from comms.core import refs
    from comms.core.audit import cutover
    from comms.core.audit.integrity import latch_degraded
    from comms.core.audit.verify_all import verify_all
    from comms.core.auth import clients, lease_format
    from comms.core.keys import rotate as rot
    from comms.core.keys.secrets import FileSecretStore
    from comms.core.objects import message_identity, object_ref
    from comms.core.providers.capability import CapabilityState as S
    from comms.core.security import security_epoch
    from comms.mcp.catalog import TOOL_CATALOG
    from comms.mcp.oauth.server import OAuthSettings
    from comms.runtime.adapters import Adapters
    from comms.runtime.assemble import assemble_runtime
    from comms.runtime.paths import CommsPaths
    from comms.runtime.settings import DaemonSettings, RemoteSettings
    from comms.runtime.state import CommsState
    from comms.transports.telegram.bot.admin import BotAdmin
    from comms.transports.telegram.ipc.admin import AdminRouter, serve_admin
    from comms.transports.telegram.user.admin import UserAdmin
    from comms.transports.whatsapp.cloud.groups import WhatsAppAdmin
    from tests.core import fakes
    from tests.core.audit.legacy_fixtures import verify_keys
    from tests.services.context_fixtures import Source
    from tests.services.group_fixtures import (
        WA_PHONE,
        Admin,
        Provider,
        Tripwire,
        _never,
        group_world,
    )

    out: dict[str, Any] = {}
    now = lambda: datetime.now(UTC)
    w = group_world(root)
    conn = w["conn"]
    cutover.run_cutover(conn, w["port"], w["writer"], now=now())  # so verify --all has a lineage
    for purpose in ("campaign-commit-key", "cursor-key", "oauth-signing-key"):
        rot.rotate(
            w["writer"],
            w["store"],
            purpose,
            material=os.urandom(32),
            prove=lambda m: None,
            now=now(),
        )
    admins = {
        "telegram_bot": Admin(BotAdmin(Tripwire())),
        "telegram_user": Admin(UserAdmin(Tripwire(), run=_never, clock=now)),
        "whatsapp_cloud": Admin(WhatsAppAdmin(Tripwire(), Tripwire())),
    }
    adapters = Adapters(
        delivery={"whatsapp": fakes.FakeWhatsApp(conn=conn)},
        capability={actor: Provider(S.AVAILABLE) for actor in admins},
        admin=admins,
        context={"telegram_user": Source(), "telegram_bot": Source(provenance="telegram_local")},
    )
    ports = [_free_port(), _free_port()]
    remote_client = clients.add_client(
        conn, w["store"], "remote", now=now(), helper_path=root / "remote.seed"
    )  # its row is what the OAuth server checks is enabled
    issuer = f"http://127.0.0.1:{ports[1]}"
    state = CommsState(
        conn=conn,
        secrets=FileSecretStore(root / "secrets"),
        store=w["store"],
        writer=w["writer"],
        paths=CommsPaths(root),
    )
    settings = DaemonSettings(
        local_port=ports[0],
        remote=RemoteSettings(
            oauth=OAuthSettings(
                issuer=issuer,
                resource=f"{issuer}/mcp",
                client_id="remote",
                redirect_uris=("http://127.0.0.1/cb",),
                owner="owner",
            ),
            client=remote_client,
            port=ports[1],
        ),
    )
    runtime = assemble_runtime(  # the daemon's one composition root, fakes injected
        state,
        settings,
        adapters_factory=lambda _state, _settings: adapters,
        clock=now,
        monotonic=time.monotonic,
    ).runtime
    rundir = root / "run"
    rundir.mkdir(mode=0o700)
    router = AdminRouter(runtime.admin_handlers, control_handlers=runtime.control_handlers)
    admin_server = await serve_admin(rundir / "admin.sock", router, allow_uid=os.getuid())
    servers = [
        uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        for app, port in ((runtime.listeners.local, ports[0]), (runtime.listeners.remote, ports[1]))
    ]
    tasks = [asyncio.create_task(s.serve()) for s in servers]
    while not all(s.started for s in servers):
        await asyncio.sleep(0.01)
    env = {**os.environ, "TELEGRAM_MCP_RUNTIME_DIR": str(rundir)}
    comms_bin = str(Path(sys.executable).parent / "comms")

    async def cli(*argv: str) -> dict[str, Any]:
        proc = await asyncio.create_subprocess_exec(
            comms_bin,
            *argv,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"comms {' '.join(argv)}: {stderr.decode()[:200]}")
        return json.loads(stdout)

    def mcp_headers(token: str, method: str, name: str | None = None) -> dict[str, str]:
        headers = {
            "Mcp-Protocol-Version": "2026-07-28",
            "Mcp-Method": method,
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        }
        if name:
            headers["Mcp-Name"] = name
        return headers

    async def http_call(
        port: int, token: str, name: str, arguments: dict[str, Any]
    ) -> httpx.Response:
        meta = {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments, "_meta": meta},
        }
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            return await client.post(
                "/mcp", headers=mcp_headers(token, "tools/call", name), json=body
            )

    try:
        added = await cli(
            "client", "add", "--name", "smoke", "--helper-path", str(root / "run" / "seed")
        )
        cli_ref = added["client"]
        _cid, seed = lease_format.read_helper(root / "run" / "seed")

        def lease() -> str:
            return lease_format.mint(seed, cli_ref, security_epoch(conn), now=now())

        approved = await cli("oauth", "approve")
        out["operator"] = cli_ref.startswith("cli_") and len(approved["owner_code"]) >= 16

        # -- stdio: the real proxy subprocess, driven by an MCP client -------------------
        params = StdioServerParameters(
            command=comms_bin,
            args=[
                "mcp",
                "--stdio",
                "--client-seed",
                str(root / "run" / "seed"),
                "--daemon",
                f"http://127.0.0.1:{ports[0]}",
                "--runtime-dir",
                str(rundir),
            ],
            env=env,
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            out["stdio_tools"] = [t.name for t in listed.tools] == [s.name for s in TOOL_CATALOG]
            got = await session.call_tool("comms_group_get", {"group": w["grp"]})
            out["stdio_read"] = (not got.is_error) and got.structured_content["group"] == w["grp"]

        # -- HTTP: context, writes, replay, admin ops ------------------------------------
        page = (
            await http_call(
                ports[0], lease(), "comms_context_recent", {"group": w["grp"], "limit": 3}
            )
        ).json()["result"]
        items, cursor = page["structuredContent"]["items"], page["structuredContent"]["next_cursor"]
        out["context_read"] = (
            bool(items) and all(i["source"] for i in items) and cursor.startswith("cur_")
        )
        nxt = (await http_call(ports[0], lease(), "comms_context_page", {"cursor": cursor})).json()[
            "result"
        ]
        out["context_page"] = not nxt["isError"] and bool(nxt["structuredContent"]["items"])
        request = refs.mint("request")
        first = (
            await http_call(
                ports[0],
                lease(),
                "comms_location_create",
                {"name": "Parramatta", "request_id": request},
            )
        ).json()["result"]
        again = (
            await http_call(
                ports[0],
                lease(),
                "comms_location_create",
                {"name": "Parramatta", "request_id": request},
            )
        ).json()["result"]
        count = conn.execute("SELECT count(*) FROM locations WHERE name = 'Parramatta'").fetchone()[
            0
        ]
        out["http_write"] = not first["isError"] and first["structuredContent"][
            "location"
        ].startswith("loc_")
        out["replay"] = (
            again["structuredContent"]["location"] == first["structuredContent"]["location"]
            and count == 1
            and again["structuredContent"]["replayed"]
        )
        ban = (
            await http_call(
                ports[0],
                lease(),
                "comms_group_member_ban",
                {"group": w["grp"], "recipient": w["rcp"], "request_id": refs.mint("request")},
            )
        ).json()["result"]
        out["admin_op"] = (
            ban["structuredContent"]["result"] == "SUCCEEDED"
            and len(admins["telegram_bot"].calls) == 1
        )
        capability = (
            await http_call(ports[0], lease(), "comms_capability_for_group", {"group": w["grp"]})
        ).json()["result"]
        out["capability"] = set(capability["structuredContent"]["actors"]) == {
            "telegram_bot",
            "telegram_user",
        }
        wa_message = object_ref(
            conn,
            "message",
            "whatsapp",
            "whatsapp_cloud",
            None,
            message_identity(WA_PHONE, "wamid.SMOKE1"),
            now=now(),
        )
        read = (
            await http_call(
                ports[0],
                lease(),
                "comms_message_mark_read",
                {
                    "conversation": w["rcp"],
                    "message": wa_message,
                    "request_id": refs.mint("request"),
                },
            )
        ).json()["result"]
        tools = [r[0] for r in conn.execute("SELECT tool FROM mutations")]
        out["whatsapp"] = (
            read["structuredContent"]["result"] == "SUCCEEDED"
            and "comms_message_mark_read" in tools
        )

        # -- the real CLI: a campaign over the admin socket --------------------------------
        created = await cli("campaign", "create", "--title", "Nowruz")
        cmp = created["result"]["campaign"]
        await cli(
            "campaign",
            "set-content",
            "--campaign",
            cmp,
            "--content",
            '{"canonical": "Happy Nowruz"}',
        )
        await cli(
            "campaign",
            "set-targets",
            "--campaign",
            cmp,
            "--targets",
            json.dumps({"recipients": [w["rcp"]]}),
            "--transports",
            '["whatsapp"]',
        )
        await cli("campaign", "validate", "--campaign", cmp)
        sent = await cli("campaign", "send", "--campaign", cmp)
        out["campaign_cli"] = (
            sent["error"] is None
            and sent["result"]["generation"].startswith("gen_")
            and sent["request_id"].startswith("req_")
        )

        # -- refusals over HTTP -----------------------------------------------------------
        bad = await http_call(ports[0], "cml1.forged.token", "comms_capability_list", {})
        stale = lease_format.mint(os.urandom(32), cli_ref, security_epoch(conn), now=now())
        out["bad_lease"] = (
            bad.status_code == 401
            and (await http_call(ports[0], stale, "comms_capability_list", {})).status_code == 401
        )
        unknown = (await http_call(ports[0], lease(), "comms_nope", {})).json()["result"]
        out["unknown_tool"] = (
            unknown["isError"] and unknown["structuredContent"]["error"]["code"] == "TOOL_NOT_FOUND"
        )
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{ports[0]}") as client:
            duplicated = await client.post(
                "/mcp",
                headers={**mcp_headers(lease(), "tools/list"), "Content-Type": "application/json"},
                content=b'{"jsonrpc":"2.0","jsonrpc":"2.0","id":1,"method":"tools/list"}',
            )
        out["duplicate_keys"] = duplicated.status_code == 400

        # -- OAuth through the remote listener ---------------------------------------------
        verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        owner = (await cli("oauth", "approve"))["owner_code"]
        async with httpx.AsyncClient(base_url=issuer, follow_redirects=False) as client:
            auth = await client.get(
                "/authorize",
                params={
                    "response_type": "code",
                    "client_id": "remote",
                    "redirect_uri": "http://127.0.0.1/cb",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "resource": f"{issuer}/mcp",
                    "scope": "comms.full_admin",
                    "state": "s",
                    "owner_code": owner,
                },
            )
            code = parse_qs(urlparse(auth.headers["location"]).query)["code"][0]
            token = (
                await client.post(
                    "/token",
                    data={
                        "client_id": "remote",
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": "http://127.0.0.1/cb",
                        "code_verifier": verifier,
                        "resource": f"{issuer}/mcp",
                    },
                )
            ).json()["access_token"]
        remote = await http_call(ports[1], token, "comms_capability_list", {})
        out["oauth"] = remote.status_code == 200 and not remote.json()["result"]["isError"]

        # -- the whole trail verifies after every surface wrote ---------------------------
        report = verify_all(conn, w["port"].conn, verify_keys(w))
        out["verify_all"] = (
            report.ok and (report.legacy, report.lineage, report.comms) == ("ok",) * 3
        ) or report

        # -- last: the degraded latch -----------------------------------------------------
        latch_degraded(conn, reason="ANCHOR_REFRESH_FAILED", now=now())
        refused = (
            await http_call(
                ports[0],
                lease(),
                "comms_location_create",
                {"name": "X", "request_id": refs.mint("request")},
            )
        ).json()["result"]
        out["degraded"] = (
            refused["isError"]
            and refused["structuredContent"]["error"]["code"] == "AUDIT_INTEGRITY_DEGRADED"
        )
    finally:
        for server in servers:
            server.should_exit = True
        await asyncio.gather(*tasks)
        admin_server.close()
        await admin_server.wait_closed()
        conn.close()
    return out


def phase_v03_daemon(ledger: Ledger) -> None:
    """D39-PRE E11 (D39-A): the real daemon in its own process, driven only through the
    installed comms binary, the admin socket, stdio and HTTP (scripts/smoke_daemon.py)."""
    area = "comms v0.3 D39-A — real daemon"
    results: dict[str, Any] = {}

    def drive() -> dict[str, Any]:
        if not results:
            sys.path.insert(0, str(REPO))
            sys.path.insert(0, str(REPO / "scripts"))
            import smoke_daemon

            with tempfile.TemporaryDirectory(dir="/tmp", prefix="sd") as short:
                results.update(smoke_daemon.drive_install_and_surfaces(Path(short)))
            with tempfile.TemporaryDirectory(dir="/tmp", prefix="sd") as short:
                results.update(smoke_daemon.drive_directory_and_campaigns(Path(short)))
        return results

    def verdict(key: str) -> Any:
        return drive().get(key) is True or _raise(drive().get(key))

    ledger.run(
        area,
        "keys provision creates the encrypted state without a daemon",
        lambda: verdict("provision"),
    )
    ledger.run(
        area, "a fresh daemon holds writes until the cutover, reads work", lambda: verdict("held")
    )
    ledger.run(area, "cutover run completes and releases writes", lambda: verdict("cutover"))
    ledger.run(
        area, "the doctor is ok once the daemon has run retention", lambda: verdict("doctor")
    )
    ledger.run(
        area,
        "client add then tools/list over stdio equals the catalog",
        lambda: verdict("stdio_tools"),
    )
    ledger.run(area, "a read over stdio reaches the daemon", lambda: verdict("stdio_read"))
    ledger.run(
        area, "a write over local HTTP with a fresh cml1 lease", lambda: verdict("http_write")
    )
    ledger.run(
        area,
        "a request-id replay returns the first result with no second effect",
        lambda: verdict("replay"),
    )
    ledger.run(area, "audit verify --all through the CLI is clean", lambda: verdict("verify_all"))
    ledger.run(
        area, "a second daemon on the same runtime dir is refused", lambda: verdict("second_daemon")
    )
    ledger.run(
        area, "credential set without a terminal is refused", lambda: verdict("credential_no_tty")
    )
    ledger.run(area, "SIGTERM exits 0 and removes the admin socket", lambda: verdict("sigterm"))
    ledger.run(
        area,
        "a restart after SIGTERM is healthy and verifies clean",
        lambda: verdict("restart_clean"),
    )
    ledger.run(
        area, "kill -9 then restart recovers and verifies clean", lambda: verdict("kill9_restart")
    )
    ledger.run(
        area,
        "a stale anchor starts degraded, reads work, repair restores writes",
        lambda: verdict("stale_anchor"),
    )
    ledger.run(
        area,
        "a database key that does not open comms.db refuses the start",
        lambda: verdict("wrong_key"),
    )
    ledger.run(
        area,
        "a backup restore through the CLI seeds the directory and its group is listed",
        lambda: verdict("backup_seed"),
    )
    ledger.run(
        area,
        "a campaign sent through the CLI is delivered by the daemon",
        lambda: verdict("campaign_delivered"),
    )
    ledger.run(
        area,
        "a scheduled campaign survives kill -9 and runs once",
        lambda: verdict("scheduled_once"),
    )
    ledger.run(
        area,
        "keys rotate cursor-key invalidates an old context cursor",
        lambda: verdict("cursor_rotation"),
    )
    ledger.run(
        area,
        "audit verify --all is clean after the campaigns and the restore",
        lambda: verdict("verify_after"),
    )
    ledger.run(
        area,
        "scripted Bot API updates are retained and served as telegram_local",
        lambda: verdict("bot_updates_local"),
    )
    ledger.run(
        area,
        "a restart polls from the stored offset and ingests nothing twice",
        lambda: verdict("bot_updates_no_duplicate"),
    )
    ledger.run(
        area,
        "the webhook listener verifies the challenge, accepts signed, refuses unsigned",
        lambda: verdict("webhook_served"),
    )
    ledger.run(
        area,
        "kill -9 during webhook delivery, then restart, verifies clean",
        lambda: verdict("webhook_kill9"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 + Phase 2 end-to-end smoke")
    parser.add_argument("--verbose", action="store_true", help="print tracebacks for failures")
    args = parser.parse_args()

    os.chdir(REPO)
    sys.path.insert(0, str(REPO / "src"))
    ledger = Ledger(verbose=args.verbose)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="telegram-mcp-smoke-") as sandbox_name:
        sandbox = Path(sandbox_name)
        phase1_contracts(ledger)
        phase1_live_server(ledger)
        state = phase2a_core(ledger, sandbox)
        phase2a_ipc(ledger, sandbox, state)
        phase2a_runtime(ledger, sandbox)
        phase4a_catalogue(ledger)
        phase4b_reads(ledger)
        phase4c_reads(ledger)
        phase5a_operator(ledger)
        phase_v03_cutover(ledger)
        phase_v03_comms(ledger)
        phase_v03_daemon(ledger)
        conn = state.get("conn")
        if conn is not None:
            conn.close()
    ledger.report()
    print(f"elapsed {time.monotonic() - started:.1f}s")
    return 1 if ledger.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
