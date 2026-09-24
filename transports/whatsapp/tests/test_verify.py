from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from whatsvault.approval import verify as V


def _kp():
    p = ec.generate_private_key(ec.SECP256R1())
    sec1 = p.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return p, sec1


def test_roundtrip():
    p, sec1 = _kp()
    assert V.verify(b"payload", V.sign_for_test(b"payload", p), sec1) is True


def test_mutations_and_wrong_key_fail():
    p, sec1 = _kp()
    sig = V.sign_for_test(b"payload", p)
    assert V.verify(b"payloaX", sig, sec1) is False
    _, other_sec1 = _kp()
    assert V.verify(b"payload", sig, other_sec1) is False


def test_both_sigs_verify_without_asserting_difference():
    p, sec1 = _kp()
    a, b = V.sign_for_test(b"x", p), V.sign_for_test(b"x", p)
    assert V.verify(b"x", a, sec1) and V.verify(b"x", b, sec1)  # never assert a != b (#16)


def test_replay_identity_is_device_plus_nonce_not_signature_bytes():
    seen = set()

    def submit(device_id, nonce, _sig):
        key = (device_id, nonce)
        if key in seen:
            return "REPLAY"
        seen.add(key)
        return "OK"

    p, _ = _kp()
    nonce = bytes(range(32))
    s1, s2 = V.sign_for_test(b"x", p), V.sign_for_test(b"x", p)  # differ, yet irrelevant to replay
    assert submit("dev", nonce, s1) == "OK"
    assert submit("dev", nonce, s2) == "REPLAY"


def test_bad_length_rejected():
    _, sec1 = _kp()
    assert V.verify(b"x", b"short", sec1) is False
