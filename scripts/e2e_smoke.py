#!/usr/bin/env python3
"""End-to-end smoke across every Phase-1 and Phase-2 function.

This is not the test suite. The suite proves each unit; this drives the
*shipped artifacts* end to end in one run — the real demo server over a real
TCP socket, the real SQLite schema on disk, real Unix sockets, the real CLI,
and the real signed agent binary against the real broker — and prints one
ledger with an exit code.

It mutates nothing outside its own sandbox: no service accounts, no
keychain writes, no Telegram, no host paths. Pairing state is read, never
written, and every consent path runs through the headless selftest route so
no biometric prompt appears.

    uv run python scripts/e2e_smoke.py [--verbose]
"""

from __future__ import annotations

import argparse
import base64
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
AGENT_BIN = REPO / "build/consent/TelegramMCPConsent.app/Contents/MacOS/telegram-mcp-consent"
VECTORS = REPO / "tests/fixtures/consent/jcs_vectors.json"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


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
        from telegram_mcp.contract import EXPECTED_TOOLS, load_contracts

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
        from telegram_mcp.contract import load_contracts

        schema = load_contracts()["telegram_get_messages"].output_schema
        text = json.dumps(schema)
        assert "$defs" in schema, "C3: definitions must be hoisted to the assembled root"
        assert "#/$defs/" in text, "C3: references must resolve against the root"
        assert "#/properties/data/$defs" not in text, "C3: definitions still buried under data"
        return f"{len(schema['$defs'])} definitions at the root"

    def validation_bounds():
        from telegram_mcp.contract import load_contracts
        from telegram_mcp.validation import ArgumentError, validate_arguments

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
        from telegram_mcp.config import DemoConfig, validate_environment

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
        from telegram_mcp.dispatch import dispatch

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
    area = "Phase 1 — live MCP server (real TCP)"
    import httpx
    from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY

    port = free_port()
    server = subprocess.Popen(
        [str(CLI), "demo", "--host", "127.0.0.1", "--port", str(port)],
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
        from telegram_mcp.keys.store import FILE_BACKED_KEYS, key_id, provision_missing

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
        from telegram_mcp.storage.db import open_db

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
        from telegram_mcp.authority.policy import (
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
        from telegram_mcp.authority.cursors import (
            CursorError,
            CursorPresenter,
            ProjectScopeEntry,
            check_cursor,
            mint_cursor,
        )
        from telegram_mcp.storage.db import bind_cursor_store

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
        from telegram_mcp.authority.epochs import PresenceRequired, set_locked
        from telegram_mcp.storage.db import bind_epoch_state, save_epoch_state

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
        from telegram_mcp.storage.settings import SETTINGS_REGISTRY, get_setting, set_setting

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
# Phase 2a: IPC — leases, admin socket, rendezvous, tunnel pins
# --------------------------------------------------------------------------


def phase2a_ipc(ledger: Ledger, sandbox: Path, state: dict[str, Any]) -> None:
    area = "Phase 2a — IPC, leases, tunnel"

    def leases():
        from telegram_mcp.ipc.leases import LeaseError, mint_lease, verify_lease

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

        from telegram_mcp.ipc.admin import AdminRouter, serve_admin
        from telegram_mcp.ipc.framing import decode_json_frame, encode_json_frame, read_frame

        async def drive() -> str:
            with tempfile.TemporaryDirectory(dir="/tmp") as short:
                path = Path(short) / "admin.sock"
                seen: list[str] = []
                router = AdminRouter(
                    {
                        "lock status": lambda args: {
                            "locked": False,
                            "security_epoch": state.get("security_epoch", 1),
                        }
                    },
                    presence_verifier=lambda proof: proof == {"method": "smoke"},
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
                    gated = await call(encode_json_frame({"cmd": "lock"}))
                    assert gated["code"] == "PRESENCE_REQUIRED", gated
                    allowed = await call(
                        encode_json_frame(
                            {"cmd": "lock", "args": {"presence": {"method": "smoke"}}}
                        )
                    )
                    assert allowed["code"] == "NOT_AVAILABLE_IN_PHASE", allowed
                    unknown = await call(encode_json_frame({"cmd": "drop everything"}))
                    assert unknown["code"] == "UNKNOWN_COMMAND", unknown
                    duplicate = await call(b'{"cmd": "lock status", "cmd": "lock"}')
                    assert duplicate["code"] == "MALFORMED_REQUEST", duplicate
                    control = await call(encode_json_frame({"control": "stop"}))
                    assert control["ok"] is True and seen == ["stop"], (control, seen)
                    mode = oct(path.stat().st_mode)[-3:]
                    assert mode == "660", mode
                    return "served, presence gate, unknown, duplicate keys, control stop, 0660"
                finally:
                    server.close()
                    await server.wait_closed()

        return asyncio.run(drive())

    def rendezvous():
        import asyncio

        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from telegram_mcp.ipc.framing import (
            decode_json_frame,
            encode_json_frame,
            read_frame,
            write_frame,
        )
        from telegram_mcp.ipc.rendezvous import serve_rendezvous, transcript_digest

        async def drive() -> str:
            transport = Ed25519PrivateKey.from_private_bytes(b"\x05" * 32)
            runtime_id = b"\x02" * 16
            sessions: list[Any] = []
            with tempfile.TemporaryDirectory(dir="/tmp") as short:
                path = Path(short) / "consent.sock"
                server = await serve_rendezvous(
                    path,
                    challenge_key=b"\x01" * 32,
                    runtime_id=runtime_id,
                    daemon_key_id="ed25519:sha256:" + "a" * 64,
                    agent_transport_public=transport.public_key().public_bytes_raw(),
                    on_session=lambda session, reader, writer: sessions.append(session),
                )
                try:
                    reader, writer = await asyncio.open_unix_connection(str(path))
                    nonce = base64.urlsafe_b64encode(b"\x09" * 16).decode().rstrip("=")
                    await write_frame(
                        writer,
                        encode_json_frame(
                            {
                                "type": "HELLO",
                                "version": "rv-1",
                                "agent_key_id": "ed25519:sha256:" + "b" * 64,
                                "agent_nonce": nonce,
                            }
                        ),
                    )
                    challenge = decode_json_frame(await read_frame(reader, idle_s=5))
                    digest = transcript_digest(
                        runtime_id=challenge["runtime_id"],
                        agent_key_id="ed25519:sha256:" + "b" * 64,
                        agent_nonce=nonce,
                        daemon_nonce=challenge["daemon_nonce"],
                        daemon_key_id=challenge["daemon_key_id"],
                    )
                    await write_frame(
                        writer,
                        encode_json_frame(
                            {
                                "type": "READY",
                                "agent_nonce": nonce,
                                "daemon_nonce": challenge["daemon_nonce"],
                                "sig": base64.urlsafe_b64encode(transport.sign(digest))
                                .decode()
                                .rstrip("="),
                            }
                        ),
                    )
                    await asyncio.sleep(0.1)
                    writer.close()
                    assert len(sessions) == 1, sessions
                    assert challenge["runtime_id"] == runtime_id.hex()
                    return "HELLO/CHALLENGE/READY completed, transcript signed"
                finally:
                    server.close()
                    await server.wait_closed()

        return asyncio.run(drive())

    def tunnel_pins():
        from telegram_mcp.ipc.tunnel import (
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
    ledger.run(area, "RV-1 rendezvous handshake", rendezvous)
    ledger.run(area, "tunnel SPKI pins", tunnel_pins)


# --------------------------------------------------------------------------
# Phase 2a: runtime lifecycle, doctor, CLI
# --------------------------------------------------------------------------


def phase2a_runtime(ledger: Ledger, sandbox: Path) -> None:
    area = "Phase 2a — runtime, doctor, CLI"

    def lifecycle():
        import asyncio
        import threading

        from telegram_mcp.runtime.lifecycle import drain, run_lifecycle, startup

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
        from telegram_mcp.runtime.lock import RuntimeActive, acquire_lock

        handle = acquire_lock(sandbox / "single.lock")
        try:
            acquire_lock(sandbox / "single.lock")
        except RuntimeActive:
            return "second start refused while the first holds the lock"
        finally:
            handle.release()
        raise AssertionError("two runtimes acquired the same lock")

    def off_state():
        from telegram_mcp.runtime.bootstrap import bootstrap_status

        os.environ["TELEGRAM_MCP_RUNTIME_DIR"] = str(sandbox / "absent-run")
        try:
            report = bootstrap_status()
        finally:
            del os.environ["TELEGRAM_MCP_RUNTIME_DIR"]
        assert report["state"] == "OFF", report
        assert report["ports"] == (), report
        return "OFF with no ports"

    def doctor_headless():
        from telegram_mcp.doctor import DoctorContext, doctor

        report = doctor(
            context=DoctorContext(store_dir=sandbox / "keys", db_path=sandbox / "db" / "meta.db"),
            off=True,
        )
        statuses = {check["name"]: check["status"] for check in report["checks"]}
        assert statuses["keys.inventory"] == "ok", statuses
        assert statuses["db.integrity"] == "ok", statuses
        assert statuses["consent.selftest"] == "ok", statuses
        assert statuses["off.probe"] == "ok", statuses
        assert report["status"] == "ok", report
        return "keys, db, consent self-test and OFF probe all ok"

    def doctor_production_fails():
        from telegram_mcp.doctor import doctor

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
        return "status/doctor/keys emit JSON; admin reports a stopped runtime"

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
# Phase 2 consent: broker, gate, and the real Swift agent
# --------------------------------------------------------------------------


def phase2_consent(ledger: Ledger) -> None:
    area = "Phase 2 — consent broker, gate, agent"

    def broker_exact_once():
        import asyncio

        from telegram_mcp.consent.broker import ConsentBroker, ConsentError
        from telegram_mcp.consent.challenge import StubSigner, synthetic_exposure_digest
        from telegram_mcp.consent.gate import SyntheticDisclosureGate

        async def drive() -> str:
            stub = StubSigner(seed=0x09)
            broker = ConsentBroker(
                challenge_key=b"\x01" * 32, agent_verify=stub.verify, runtime_id=b"\x02" * 16
            )
            handle = await broker.issue(
                tool="telegram_list_chats",
                request_hmac="ab" * 32,
                principal="prn_" + "a" * 26,
                client="tcl_" + "b" * 26,
                account="tga_" + "c" * 26,
                policy_epoch=1,
                project_scope_digest="1" * 64,
                security_epoch=1,
                display_digest="2" * 64,
                exposure_snapshot_digest=synthetic_exposure_digest(),
            )
            challenge = broker.challenge_bytes(handle)
            envelope = {
                "challenge_sha256": hashlib.sha256(challenge).hexdigest(),
                "sig": stub.sign(challenge),
                "key_id": stub.key_id,
            }
            tampered = dict(envelope, sig=stub.sign(challenge + b"x"))
            try:
                await broker.consume(handle, tampered)
            except ConsentError:
                pass
            else:
                raise AssertionError("a tampered approval was accepted")
            consumed = await broker.consume(handle, envelope)
            try:
                await broker.consume(handle, envelope)
            except ConsentError:
                pass
            else:
                raise AssertionError("an approval was consumed twice")
            decision = await SyntheticDisclosureGate().authorize_disclosure(
                challenge=consumed, snapshot=None
            )
            assert decision.allowed is True
            assert not hasattr(broker, "approve"), "the broker must expose no approve method"
            return "tamper refused, exact-once consume, gate allowed, no approve method"

        return asyncio.run(drive())

    def agent_present():
        if not AGENT_BIN.exists():
            raise _Skip("agent bundle not built; run scripts/package_agent.sh")
        return str(AGENT_BIN.relative_to(REPO))

    def agent_jcs():
        if not AGENT_BIN.exists():
            raise _Skip("agent bundle not built")
        done = subprocess.run(
            [str(AGENT_BIN), "selftest-jcs", str(VECTORS)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert done.returncode == 0 and "JCS-OK" in done.stdout, done.stderr
        cases = len(json.loads(VECTORS.read_text())["cases"])
        return f"{cases} vectors byte-equal"

    def agent_verify():
        if not AGENT_BIN.exists():
            raise _Skip("agent bundle not built")
        done = subprocess.run(
            [str(AGENT_BIN), "selftest-verify", str(VECTORS)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert done.returncode == 0 and "VERIFY-OK" in done.stdout, done.stderr
        return "daemon signatures verified, tamper rejected"

    def agent_display_gate():
        if not AGENT_BIN.exists():
            raise _Skip("agent bundle not built")
        done = subprocess.run(
            [str(AGENT_BIN), "selftest-display-tamper", str(VECTORS)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env={**os.environ, "CONSENT_NO_UI": "1"},
        )
        combined = done.stdout + done.stderr
        assert done.returncode != 0 and "DISPLAY-MISMATCH" in combined, combined
        assert "PROMPT-ATTEMPTED" not in combined, "the gate let a prompt through"
        return "tampered display refused before any prompt"

    def agent_pairing_state():
        if not AGENT_BIN.exists():
            raise _Skip("agent bundle not built")
        done = subprocess.run(
            [str(AGENT_BIN), "pairing-status"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        report = json.loads(done.stdout)
        assert report["identity"]["kind"] == "adhoc", report["identity"]
        assert report["pairable"] is False, report
        refusal = subprocess.run(
            [str(AGENT_BIN), "pairing", "generate"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert refusal.returncode != 0 and "AD-HOC" in (refusal.stdout + refusal.stderr).upper()
        return "ad-hoc bundle reports unpairable and refuses to pair"

    def join_scenarios():
        if not AGENT_BIN.exists():
            raise _Skip("agent bundle not built")
        sys.path.insert(0, str(REPO))
        from tests.agent.stub_broker import run_scenario

        outcomes = {}
        for scenario in (
            "good-approval",
            "duplicate-approval",
            "display-tamper",
            "broker-death-mid-prompt",
        ):
            result = run_scenario(scenario, timeout=90)
            outcomes[scenario] = result
        assert (
            outcomes["good-approval"]["approved"] and outcomes["good-approval"]["signature_valid"]
        )
        assert outcomes["duplicate-approval"]["second_consume"] == "rejected"
        assert outcomes["display-tamper"]["denial_reason"] == "DISPLAY-MISMATCH"
        assert outcomes["broker-death-mid-prompt"]["agent_exited"]
        assert outcomes["broker-death-mid-prompt"]["exit_seconds"] < 5.0
        return "good, replay-rejected, tamper-denied, no orphan on broker death"

    ledger.run(area, "broker exact-once and gate", broker_exact_once)
    ledger.run(area, "agent bundle present", agent_present)
    ledger.run(area, "agent JCS byte equality", agent_jcs)
    ledger.run(area, "agent challenge verification", agent_verify)
    ledger.run(area, "agent display gate", agent_display_gate)
    ledger.run(area, "agent pairing refusal", agent_pairing_state)
    ledger.run(area, "real broker <-> real agent", join_scenarios)


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
        phase2_consent(ledger)
        conn = state.get("conn")
        if conn is not None:
            conn.close()
    ledger.report()
    print(f"elapsed {time.monotonic() - started:.1f}s")
    return 1 if ledger.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
