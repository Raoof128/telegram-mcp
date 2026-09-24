"""P-256 signature verification (spec §6, ledger #16).

Signatures are transported as raw r||s (64B) and reconstructed to DER; the payload
is verified directly under ECDSA(SHA256) (CryptoKit hashes internally). ECDSA is
randomized, so signature bytes are NEVER a replay key — replay identity is
device_id + nonce (enforced by the sender via approval_nonces UNIQUE)."""

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.asymmetric.ec import ECDSA


def verify(payload: bytes, signature_rs: bytes, public_key_sec1: bytes) -> bool:
    if not signature_rs or len(signature_rs) != 64:
        return False
    try:
        r = int.from_bytes(signature_rs[:32], "big")
        s = int.from_bytes(signature_rs[32:], "big")
        der = utils.encode_dss_signature(r, s)
        pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key_sec1)
        pub.verify(der, payload, ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError):
        return False


def sign_for_test(payload: bytes, private_key) -> bytes:
    """Software P-256 signer — TESTS ONLY. Production keys live in the Secure Enclave."""
    der = private_key.sign(payload, ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")
