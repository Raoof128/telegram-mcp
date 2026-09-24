"""A real legacy Telegram DB with a verified chain and anchor, for cutover tests (tests only)."""

from __future__ import annotations

import os
from pathlib import Path

from comms.core.storage.db import write_tx
from comms.transports.telegram.disclosure.audit.anchor import write_anchor
from comms.transports.telegram.disclosure.audit.chain import append_event, head, mint_event_id
from comms.transports.telegram.runtime.cutover_barrier import CutoverGate, TelegramLegacyPort
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.storage.migrations import migrate

CHAIN_KEY = bytes(range(32))
CHECKPOINT_SEED = b"\x07" * 32


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


def legacy_port(tmp_path: Path, *, events: int = 3, name: str = "legacy.db") -> TelegramLegacyPort:
    conn = open_db(tmp_path / name)
    migrate(conn)
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
