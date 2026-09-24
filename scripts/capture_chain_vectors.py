"""Capture byte vectors of the legacy Telegram audit chain and anchor (comms v0.3 Task A3).

Pure functions only, fixed inputs: the vectors are the oracle the core chain
engine's LEGACY_TELEGRAM profile must reproduce byte for byte.
Run: uv run python scripts/capture_chain_vectors.py > tests/fixtures/audit/legacy_chain_vectors.json
"""

from __future__ import annotations

import json
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from comms.transports.telegram.disclosure.audit import anchor, chain

CHAIN_KEY = b"\x11" * 32
CHECKPOINT_SEED = b"\x22" * 32
SOURCE = "c830d3f"


def _event(n: int) -> dict:
    return {
        "event_id": f"evt_{n:026d}",
        "ts": f"2026-09-24T00:00:0{n}.000000Z",
        "tool_name": "telegram_status" if n % 2 else "admin.lock",
        "principal_ref": "prn_" + "a" * 26,
        "client_ref": "tcl_" + "b" * 26,
        "account_ref": None,
        "peer_ref": None,
        "project_ref": None,
        "project_count": n,
        "policy_epoch": 1,
        "result_count": 0,
        "duration_ms": 10 + n,
        "telegram_rpc_count": 0,
        "status": "ok",
        "error_code": None,
        "disclosure_ref": None,
    }


def capture() -> dict:
    events, prev = [], chain.genesis_mac(1)
    for seq in range(1, 8):
        ev = _event(seq)
        mac = chain.event_mac(
            CHAIN_KEY, chain_epoch=1, chain_seq=seq, prev_event_mac=prev, event=ev
        )
        events.append(
            {
                "event": ev,
                "chain_epoch": 1,
                "chain_seq": seq,
                "prev_event_mac": prev,
                "event_mac": mac,
            }
        )
        prev = mac
    cp_row = {
        "chain_epoch": 1,
        "chain_seq": 7,
        "last_event_id": events[-1]["event"]["event_id"],
        "last_event_mac": prev,
        "created_at": "2026-09-24T00:00:08.000000Z",
    }
    message = chain._checkpoint_message(cp_row)
    signature = Ed25519PrivateKey.from_private_bytes(CHECKPOINT_SEED).sign(message)
    anchor_body = {
        "version": anchor.ANCHOR_VERSION,
        "chain_epoch": 1,
        "chain_seq": 7,
        "event_id": cp_row["last_event_id"],
        "event_mac": prev,
        "updated_at": "2026-09-24T00:00:09Z",
    }
    return {
        "source": SOURCE,
        "chain_key_hex": CHAIN_KEY.hex(),
        "checkpoint_seed_hex": CHECKPOINT_SEED.hex(),
        "genesis_mac": {"1": chain.genesis_mac(1), "2": chain.genesis_mac(2)},
        "events": events,
        "checkpoint": {
            "row": cp_row,
            "message_hex": message.hex(),
            "signature_hex": signature.hex(),
        },
        "anchor": {"body": anchor_body, "anchor_mac": anchor._anchor_mac(CHAIN_KEY, anchor_body)},
    }


if __name__ == "__main__":
    json.dump(capture(), sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
