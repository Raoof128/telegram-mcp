"""Phone-signed capability grants (spec §5.7, ledger #7/#8). A grant is signed ON THE
IPHONE (Face ID -> Secure Enclave signing key); the Mac VERIFIES and stores it, and
never mints one — there is no mint_grant here. verify_and_consume re-verifies the stored
signature against the CURRENT active device key, so a revoked device's grants stop
working. The MCP may use a grant but can never create/extend/re-scope one."""

from . import devices, verify

DOMAIN = b"WHATSVAULT-CAPABILITY-V1\n"
VERSION = 1
_FIELDS = [
    ("capability_id", "str"),
    ("device_id", "str"),
    ("account_id", "str"),
    ("conversation_id", "str"),
    ("action", "str"),
    ("created_at_ms", "u64"),
    ("expires_at_ms", "u64"),
    ("max_actions", "u64"),
    ("nonce", "bytes"),
]


class GrantRejected(Exception):
    pass


def _fb(typ, v) -> bytes:
    if v is None:
        return b""
    if typ == "str":
        return v.encode("utf-8")
    if typ == "bytes":
        return bytes(v)
    return int(v).to_bytes(8, "big")


def encode_grant(fields: dict) -> bytes:
    out = bytearray(DOMAIN)
    out += VERSION.to_bytes(2, "big")
    for name, typ in _FIELDS:
        b = _fb(typ, fields.get(name))
        out += len(b).to_bytes(4, "big")
        out += b
    return bytes(out)


def store_grant(control_conn, fields: dict, signature: bytes) -> str:
    key = devices.active_signing_key(control_conn, fields["device_id"])
    if key is None:
        raise GrantRejected("device_inactive_or_unknown")
    if not verify.verify(encode_grant(fields), signature, key):
        raise GrantRejected("bad_signature")
    control_conn.execute(
        "INSERT INTO capability_grants(capability_id, device_id, account_id, conversation_id, action, "
        "created_at_ms, expires_at_ms, max_actions, used_count, nonce, signature, status) "
        "VALUES(?,?,?,?,?,?,?,?,0,?,?, 'ACTIVE')",
        (
            fields["capability_id"],
            fields["device_id"],
            fields.get("account_id"),
            fields.get("conversation_id"),
            fields["action"],
            fields.get("created_at_ms"),
            fields.get("expires_at_ms"),
            fields.get("max_actions"),
            fields.get("nonce"),
            signature,
        ),
    )
    control_conn.commit()
    return fields["capability_id"]


def verify_and_consume(control_conn, action, conversation_id, now_ms) -> bool:
    rows = control_conn.execute(
        "SELECT capability_id, device_id, account_id, created_at_ms, expires_at_ms, max_actions, "
        "used_count, nonce, signature FROM capability_grants "
        "WHERE action=? AND conversation_id=? AND status='ACTIVE'",
        (action, conversation_id),
    ).fetchall()
    for r in rows:
        key = devices.active_signing_key(control_conn, r["device_id"])
        if key is None:
            continue
        if r["expires_at_ms"] is not None and now_ms >= r["expires_at_ms"]:
            continue
        if r["max_actions"] is not None and r["used_count"] >= r["max_actions"]:
            continue
        fields = {
            "capability_id": r["capability_id"],
            "device_id": r["device_id"],
            "account_id": r["account_id"],
            "conversation_id": conversation_id,
            "action": action,
            "created_at_ms": r["created_at_ms"],
            "expires_at_ms": r["expires_at_ms"],
            "max_actions": r["max_actions"],
            "nonce": bytes(r["nonce"]) if r["nonce"] is not None else None,
        }
        if not verify.verify(encode_grant(fields), bytes(r["signature"]), key):
            continue
        cur = control_conn.execute(
            "UPDATE capability_grants SET used_count=used_count+1 WHERE capability_id=? AND used_count=?",
            (r["capability_id"], r["used_count"]),
        )
        if cur.rowcount == 1:
            control_conn.commit()
            return True
    return False
