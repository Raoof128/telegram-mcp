"""Two-key device identity + enrolment (spec §6, ledger #5/#6). Each enrolled device
pins a P-256 SIGNING key (ECDSA approvals) and a P-256 KEY-AGREEMENT key (ECDH device
seal). Enrolment is a mutual challenge: the device signs DOMAIN||pairing_id||challenge||
signing_pub||agreement_pub, binding BOTH keys — substituting either fails verification.
CLI-only; no MCP path reaches enroll()."""

from .. import ids
from . import verify as _verify

_ENROL_DOMAIN = b"WHATSVAULT-ENROL-V1\n"


def _binding(pairing_id, challenge, signing_pub, agreement_pub) -> bytes:
    out = bytearray(_ENROL_DOMAIN)
    for part in (pairing_id.encode("utf-8"), challenge, signing_pub, agreement_pub):
        out += len(part).to_bytes(4, "big")
        out += part
    return bytes(out)


def verify_enrolment(*, pairing_id, challenge, signing_pub, agreement_pub, signature) -> bool:
    return _verify.verify(_binding(pairing_id, challenge, signing_pub, agreement_pub), signature, signing_pub)


def enroll(control_conn, name, *, signing_pub, agreement_pub, now_ms=None) -> str:
    did = ids.new_id("dev")
    control_conn.execute(
        "INSERT INTO approval_devices(id, name, public_key, key_algorithm, key_encoding, "
        "agreement_public_key, agreement_key_algorithm, created_at_ms, status) "
        "VALUES(?,?,?,'P-256','sec1-uncompressed',?, 'P-256', ?, 'ACTIVE')",
        (did, name, signing_pub, agreement_pub, now_ms),
    )
    control_conn.commit()
    return did


def revoke(control_conn, device_id) -> None:
    control_conn.execute("UPDATE approval_devices SET status='REVOKED' WHERE id=?", (device_id,))
    control_conn.commit()


# SQL identifiers cannot be bound as parameters, so the only safe construction is
# an explicit allowlist. Enforced at runtime, not left to caller discipline.
_KEY_COLUMNS = frozenset({"public_key", "agreement_public_key"})


def _active_key(control_conn, device_id, column):
    if column not in _KEY_COLUMNS:
        raise ValueError(f"column {column!r} is not an allowed key column")
    row = control_conn.execute(
        f"SELECT {column} FROM approval_devices WHERE id=? AND status='ACTIVE'",
        (device_id,),
    ).fetchone()
    return bytes(row[0]) if row and row[0] is not None else None


def active_signing_key(control_conn, device_id):
    return _active_key(control_conn, device_id, "public_key")


def active_agreement_key(control_conn, device_id):
    return _active_key(control_conn, device_id, "agreement_public_key")
