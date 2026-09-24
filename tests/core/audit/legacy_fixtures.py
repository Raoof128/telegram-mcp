"""A real legacy Telegram DB with a verified chain and anchor, and a comms DB with its audit
writer, for cutover tests (tests only)."""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from comms.core.audit.verify_all import VerifyKeys
from comms.core.audit.writer import AuditWriter, SlotChainKeys
from comms.core.keys import ids
from comms.core.keys.slots import KeySlotStore, bootstrap_comms_audit_keys, registry_public_for
from comms.core.storage.db import write_tx
from comms.transports.telegram.disclosure.audit.anchor import write_anchor
from comms.transports.telegram.disclosure.audit.chain import append_event, head, mint_event_id
from comms.transports.telegram.runtime.cutover_barrier import (
    CutoverGate,
    TelegramLegacyPort,
    legacy_verifier,
)
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.storage.migrations import migrate
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW

CHAIN_KEY = bytes(range(32))
CHECKPOINT_SEED = b"\x07" * 32
CLIENT = "tcl_" + "a" * 26
_PUBLIC = Ed25519PrivateKey.from_private_bytes(CHECKPOINT_SEED).public_key().public_bytes_raw()


def legacy_event(tool: str = "admin.lock") -> dict:
    return {
        "event_id": mint_event_id(),
        "ts": "2026-09-24T00:00:00Z",
        "tool_name": tool,
        "principal_ref": None,
        "client_ref": None,
        "account_ref": None,
        "peer_ref": None,
        "project_ref": None,
        "project_count": None,
        "policy_epoch": None,
        "result_count": None,
        "duration_ms": None,
        "telegram_rpc_count": None,
        "status": "ok",
        "error_code": None,
        "disclosure_ref": None,
    }


def legacy_port(
    tmp_path: Path, *, events: int = 3, name: str = "legacy.db", build=None
) -> TelegramLegacyPort:
    """A legacy DB with ``events`` plain events, or whatever ``build(conn)`` writes, anchored."""
    conn = open_db(tmp_path / name)
    migrate(conn)
    if build is not None:
        build(conn)
    for _ in range(events):
        with write_tx(conn):
            append_event(conn, CHAIN_KEY, legacy_event())
    adir = tmp_path / f"{name}.anchor"
    adir.mkdir(mode=0o700)
    os.chmod(adir, 0o700)
    h = head(conn)
    if h is not None:
        write_anchor(adir / "anchor.json", CHAIN_KEY, now="2026-09-24T00:00:00Z", **h)
    key_dir = tmp_path / f"{name}.keys"
    key_dir.mkdir(mode=0o700)
    return TelegramLegacyPort(
        conn, CHAIN_KEY, CHECKPOINT_SEED, adir / "anchor.json", CutoverGate(), key_dir
    )


def add_bearer(port: TelegramLegacyPort, client: str = CLIENT) -> None:
    """One enabled `tgml1` bearer client with its 0600 seed file."""
    stamp = "2026-09-24T00:00:00Z"
    if port.conn.execute("SELECT count(*) FROM principals").fetchone()[0] == 0:
        port.conn.execute(
            "INSERT INTO principals (principal_ref, principal_key, auth_mode, created_at)"
            " VALUES (?, 'key', 'bearer', ?)",
            ("prn_" + "a" * 26, stamp),
        )
    port.conn.execute(
        "INSERT INTO mcp_clients (principal_id, client_ref, auth_kind, auth_binding,"
        " client_kind, created_at) VALUES (1, ?, 'bearer', ?, 'codex_local', ?)",
        (client, f"lease-seed:{client}", stamp),
    )
    port.conn.commit()
    seed = port.key_dir / f"lease-seed.{client}"
    seed.write_bytes(bytes(32))
    os.chmod(seed, 0o600)


def comms_world(tmp_path: Path, *, bearer: bool = False) -> dict:
    """A migrated comms DB with bootstrapped audit keys and writer, plus a legacy port."""
    conn = fx.migrated(tmp_path)
    store = KeySlotStore(tmp_path / "slots")
    bootstrap_comms_audit_keys(conn, store, now=NOW)
    adir = tmp_path / "comms-anchor"
    adir.mkdir(mode=0o700)
    os.chmod(adir, 0o700)
    keys = SlotChainKeys(conn, store)
    writer = AuditWriter(conn, keys, adir / "head.anchor", clock=lambda: NOW)
    port = legacy_port(tmp_path)
    if bearer:
        add_bearer(port)
    return {
        "conn": conn,
        "store": store,
        "keys": keys,
        "writer": writer,
        "anchor": adir / "head.anchor",
        "port": port,
        "tmp": tmp_path,
    }


def public_for(key_id: str) -> bytes | None:
    return _PUBLIC if key_id == ids.ed25519_key_id(_PUBLIC) else None


def verify_keys(world: dict) -> VerifyKeys:
    return VerifyKeys(
        legacy=legacy_verifier(CHAIN_KEY, public_for),
        comms_key_for_epoch=world["keys"].for_epoch,
        comms_anchor_path=world["anchor"],
        comms_public_for=registry_public_for(world["conn"]),
    )
