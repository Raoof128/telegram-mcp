"""Template catalogue + WHATSVAULT-TEMPLATE-PARAMS-V1 canonicalisation (ledger #17).

The params digest binds template name + language + definition_version + the ordered
params, so a user who approved params against definition X cannot have Meta receive
definition Y (a definition bump changes the digest). Only APPROVED templates prepare a
draft; the catalogue is CLI-synced with a management credential kept out of the runtime."""

import hashlib
import json

from . import ids

_TPL_DOMAIN = b"WHATSVAULT-TEMPLATE-PARAMS-V1\n"
DEFAULT_TTL_MS = 15 * 60 * 1000


class TemplateRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def params_digest(template_name, language, definition_version, params) -> bytes:
    h = hashlib.sha256()
    h.update(_TPL_DOMAIN)
    for part in (template_name, language, str(definition_version)):
        pb = part.encode("utf-8")
        h.update(len(pb).to_bytes(4, "big"))
        h.update(pb)
    for i, raw_param in enumerate(params or []):
        p = raw_param if isinstance(raw_param, dict) else {"value": raw_param}
        for field in (
            str(i),
            p.get("component_type", "body"),
            p.get("param_type", "text"),
            str(p.get("value", "")),
        ):
            fb = field.encode("utf-8")
            h.update(len(fb).to_bytes(4, "big"))
            h.update(fb)
    return h.digest()


def upsert_from_sync(control_conn, rows) -> int:
    for r in rows:
        control_conn.execute(
            "INSERT OR REPLACE INTO templates(template_id, meta_template_id, name, language, category, "
            "status, definition_version, schema, synced_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                r["template_id"],
                r.get("meta_template_id"),
                r["name"],
                r["language"],
                r.get("category"),
                r["status"],
                r.get("definition_version", 1),
                json.dumps(r.get("schema")),
                r.get("synced_at"),
            ),
        )
    control_conn.commit()
    return len(rows)


def prepare_template(
    control_conn, *, conversation_id, account_id, phone_number_id, template_id, params, now_ms
) -> dict:
    row = control_conn.execute(
        "SELECT name, language, status, definition_version, schema FROM templates WHERE template_id=?",
        (template_id,),
    ).fetchone()
    if not row:
        raise TemplateRefused("UNKNOWN_TEMPLATE")
    if row["status"] != "APPROVED":
        raise TemplateRefused("NOT_APPROVED")
    schema = json.loads(row["schema"]) if row["schema"] else {}
    if schema.get("params") is not None and len(params or []) != schema["params"]:
        raise TemplateRefused("PARAM_MISMATCH")
    digest = params_digest(row["name"], row["language"], row["definition_version"], params)
    draft_id = ids.new_id("drf")
    control_conn.execute(
        "INSERT INTO drafts(id, conversation_id, account_id, phone_number_id, kind, template_id, "
        "template_params_sha256, nonce, created_at_ms, expires_at_ms, created_by, state) "
        "VALUES(?,?,?,?, 'template', ?, ?, ?, ?, ?, 'mcp', 'PENDING_APPROVAL')",
        (
            draft_id,
            conversation_id,
            account_id,
            phone_number_id,
            template_id,
            digest,
            __import__("os").urandom(32),
            now_ms,
            now_ms + DEFAULT_TTL_MS,
        ),
    )
    control_conn.commit()
    return {"draft_id": draft_id, "template_params_sha256": digest.hex()}
