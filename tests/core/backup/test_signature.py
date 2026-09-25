"""comms v0.3 Task B24: comms-backup-signature/v1 detached Ed25519 signatures."""

import json

import pytest

from comms.core.backup.signature import SignatureError, sign, signing_input, verify
from comms.core.canonical import jcs_dumps
from comms.core.keys import ids

SEED = bytes(range(32))
CIPHERTEXT = b"comms backup golden ciphertext v1"
GOLDEN_INPUT = (
    "636f6d6d732d6261636b75702d7369676e61747572652f763100"
    "b2f924db085cfed2d500de5b8986c5952c9afd1604c95478043f1345d396ea95"
)
GOLDEN_SIGNATURE = (
    "af012d21dc0dc41c92c8206e95aa6e0e44b49c67400cce264ceaf8658b20ff8e"
    "2ce425bb20619c48cd45e0f173b822b8b86661216d3accd8961a3050d8f3140a"
)
PUBLIC = "03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8"


def test_golden_vector():
    assert signing_input(CIPHERTEXT).hex() == GOLDEN_INPUT
    sidecar = json.loads(sign(CIPHERTEXT, SEED))
    assert sidecar == {
        "alg": "ed25519",
        "ciphertext_sha256": GOLDEN_INPUT[52:],
        "key_id": ids.ed25519_key_id(bytes.fromhex(PUBLIC)),
        "public_key": PUBLIC,
        "schema": "comms-backup-signature/v1",
        "signature": GOLDEN_SIGNATURE,
    }


def test_sidecar_is_strict_jcs():
    sidecar = sign(CIPHERTEXT, SEED)
    assert sidecar == jcs_dumps(json.loads(sidecar))
    assert verify(CIPHERTEXT, sidecar) == ids.ed25519_key_id(bytes.fromhex(PUBLIC))
    body = json.loads(sidecar)
    for variant in (
        json.dumps(body, indent=1).encode(),  # not canonical
        jcs_dumps({**body, "extra": 1}),  # an unknown key
        sidecar.replace(b'"alg":"ed25519"', b'"alg":"ed25519","alg":"ed25519"'),  # a duplicate key
        jcs_dumps({**body, "schema": "comms-backup-signature/v2"}),
    ):
        with pytest.raises(SignatureError):
            verify(CIPHERTEXT, variant)


def test_key_id_recomputes_from_public_key():
    body = json.loads(sign(CIPHERTEXT, SEED))
    forged = jcs_dumps({**body, "key_id": "ed25519:sha256:" + "0" * 64})
    with pytest.raises(SignatureError, match="key id"):
        verify(CIPHERTEXT, forged)


def test_tampered_ciphertext_fails():
    sidecar = sign(CIPHERTEXT, SEED)
    with pytest.raises(SignatureError):
        verify(CIPHERTEXT + b"!", sidecar)
    body = json.loads(sidecar)
    other = jcs_dumps({**body, "signature": "00" * 64})
    with pytest.raises(SignatureError):
        verify(CIPHERTEXT, other)
