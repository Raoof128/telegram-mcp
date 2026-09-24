import hashlib
import json
import pathlib

from whatsvault.approval import canonical as CN

_BASE = {
    "decision": "APPROVE",
    "draft_id": "drf_1",
    "account_id": "acc_1",
    "phone_number_id": "PN1",
    "recipient_wa_id": "61999",
    "body_sha256": hashlib.sha256(b"hi").digest(),
    "kind": "text",
    "nonce": bytes(range(32)),
    "created_at_ms": 1700000000000,
    "expires_at_ms": 1700000600000,
    "device_id": "dev_1",
    "attachments_digest": CN.attachments_digest([]),
}


def test_matches_golden_vector():
    v = json.loads((pathlib.Path(__file__).parent / "golden" / "decision_vectors.json").read_text())
    assert CN.encode(_BASE).hex() == v["encoded_hex"]


def test_absent_optional_is_zero_length_slot_not_omitted():
    a = CN.encode({**_BASE, "template_id": None})
    b = CN.encode({**_BASE, "template_id": "t"})
    assert len(b) > len(a)  # present adds bytes; absent still occupies a (zero-length) slot


def test_decision_flip_changes_bytes():
    assert CN.encode({**_BASE, "decision": "APPROVE"}) != CN.encode({**_BASE, "decision": "REJECT"})


def test_target_and_reply_are_independent_slots():
    base = CN.encode(_BASE)
    only_reply = CN.encode({**_BASE, "reply_to_wamid": "wamid.R"})
    only_target = CN.encode({**_BASE, "target_message_wamid": "wamid.T"})
    assert base not in (only_reply, only_target) and only_reply != only_target
