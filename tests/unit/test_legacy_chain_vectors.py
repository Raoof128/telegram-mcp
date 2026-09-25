"""comms v0.3 Task A3: the legacy chain and anchor reproduce their captured byte vectors.

This is the oracle for the core engine extraction (A4, A6): the LEGACY_TELEGRAM
profile must reproduce these values exactly.
"""

import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from comms.transports.telegram.disclosure.audit import anchor, chain

VECTORS = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures" / "audit" / "legacy_chain_vectors.json"
    ).read_text()
)


def test_vectors_file_records_its_source_commit():
    assert VECTORS["source"] == "c830d3f"
    assert len(VECTORS["events"]) == 7


def test_the_legacy_modules_reproduce_the_vectors():
    key = bytes.fromhex(VECTORS["chain_key_hex"])
    assert chain.genesis_mac(1) == VECTORS["genesis_mac"]["1"]
    assert chain.genesis_mac(2) == VECTORS["genesis_mac"]["2"]
    for v in VECTORS["events"]:
        mac = chain.event_mac(
            key,
            chain_epoch=v["chain_epoch"],
            chain_seq=v["chain_seq"],
            prev_event_mac=v["prev_event_mac"],
            event=v["event"],
        )
        assert mac == v["event_mac"]
    cp = VECTORS["checkpoint"]
    assert chain._checkpoint_message(cp["row"]).hex() == cp["message_hex"]
    seed = bytes.fromhex(VECTORS["checkpoint_seed_hex"])
    assert (
        Ed25519PrivateKey.from_private_bytes(seed).sign(bytes.fromhex(cp["message_hex"])).hex()
        == cp["signature_hex"]
    )
    assert anchor._anchor_mac(key, VECTORS["anchor"]["body"]) == VECTORS["anchor"]["anchor_mac"]
