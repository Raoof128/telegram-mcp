import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from whatsvault.approval import devices as D
from whatsvault.approval import verify as V
from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _sec1(p):
    return p.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def _control(tmp_path):
    conn = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(conn, "control")
    return conn


def test_reaches_control_version_2(tmp_path):
    assert M.user_version(_control(tmp_path)) >= 2


def test_enrolment_binds_both_keys_and_substitution_fails():
    sign = ec.generate_private_key(ec.SECP256R1())
    agree = ec.generate_private_key(ec.SECP256R1())
    sp, ap = _sec1(sign), _sec1(agree)
    challenge = os.urandom(32)
    sig = V.sign_for_test(D._binding("pair1", challenge, sp, ap), sign)
    assert (
        D.verify_enrolment(
            pairing_id="pair1", challenge=challenge, signing_pub=sp, agreement_pub=ap, signature=sig
        )
        is True
    )
    other_ap = _sec1(ec.generate_private_key(ec.SECP256R1()))
    assert (
        D.verify_enrolment(
            pairing_id="pair1", challenge=challenge, signing_pub=sp, agreement_pub=other_ap, signature=sig
        )
        is False
    )  # #6 MITM
    assert (
        D.verify_enrolment(
            pairing_id="pair1", challenge=os.urandom(32), signing_pub=sp, agreement_pub=ap, signature=sig
        )
        is False
    )  # wrong challenge


def test_enroll_pins_both_keys_and_revoke(tmp_path):
    conn = _control(tmp_path)
    sign = ec.generate_private_key(ec.SECP256R1())
    agree = ec.generate_private_key(ec.SECP256R1())
    sp, ap = _sec1(sign), _sec1(agree)
    did = D.enroll(conn, "iphone", signing_pub=sp, agreement_pub=ap)
    assert D.active_signing_key(conn, did) == sp and D.active_agreement_key(conn, did) == ap
    D.revoke(conn, did)
    assert D.active_signing_key(conn, did) is None and D.active_agreement_key(conn, did) is None
