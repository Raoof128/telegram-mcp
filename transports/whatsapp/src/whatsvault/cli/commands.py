"""whatsvault CLI command handlers (ledger #56). Every verb is an OPERATIONS verb —
inspect, recover, administer. NONE creates approval authority: there is no approve/send/
sign/mint verb, and no handler drives the sender write path or the capability grant store.
Approval authority is the phone's Secure Enclave signature (Phase 4)."""

import hashlib
import time
from pathlib import Path

from .. import doctor
from ..approval import devices, reconcile
from ..crypto import keystore
from ..ids import new_id
from ..importers import whatsapp_export
from ..mcp import acl, audit, auth, oauth
from ..ops import bootstrap, health, paths

FORBIDDEN_VERBS = frozenset(
    {
        "approve",
        "send",
        "sign",
        "dispatch",
        "mint_capability",
        "create_capability",
        "send_message",
        "get_credentials",
        "export_vault",
        "prepare",
    }
)


class Ctx:
    def __init__(self, vault_conn, control_conn, ks=None, paths=None):
        self.vault = vault_conn
        self.control = control_conn
        self.ks = ks  # optional: only the provisioning/doctor paths use it
        # The vault layout travels with the context. cmd_init read it from the
        # environment instead, so a test that injected a temporary home still
        # created a real vault under $HOME (see tests/conftest.py's guard).
        self.paths = paths


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _audit_key(ctx):
    """The audit HMAC key, when this context has a keystore. A grant recorded
    without it is a grant nobody can account for afterwards."""
    if ctx.ks is None:
        return None
    try:
        return ctx.ks.require(audit.AUDIT_KEY_NAME, 32)
    except keystore.KeyStoreError:
        return None


def cmd_doctor(ctx, args):
    return {
        "ok": True,
        "vault": doctor.check_vault(ctx.vault),
        "search": doctor.check_search(ctx.vault),
        "ingest": doctor.check_ingest(ctx.vault),
        "mcp": doctor.check_mcp(ctx.vault, ctx.control, ks=getattr(ctx, "ks", None)),
    }


def cmd_mcp_provision(ctx, args):
    """Create the MCP daemon's Keychain keys if absent. Never rotates: replacing
    the token would silently break an already-configured connector, and replacing
    the audit key would orphan every existing audit HMAC.

    The token is withheld unless --reveal is passed, because cli.main prints
    results to stdout and the launchd units capture stdout to a log file.
    """
    if ctx.ks is None:
        return {"ok": False, "error": "no keystore available on this context"}
    provisioned, already = [], []
    for name in (auth.TOKEN_KEY_NAME, audit.AUDIT_KEY_NAME):
        try:
            ctx.ks.require(name, 32)
            already.append(name)
        except keystore.KeyMissing:
            ctx.ks.provision(name, 32)
            provisioned.append(name)
        except Exception as exc:  # noqa: BLE001 - report ANY keystore fault, never crash
            # e.g. a present-but-wrong-length key. Report it; never overwrite —
            # provisioning over a corrupt key destroys evidence of the corruption.
            return {"ok": False, "error": f"{name}: {type(exc).__name__}: {exc}"}
    out = {
        "ok": True,
        "provisioned": provisioned,
        "already_present": already,
        "endpoint": "http://127.0.0.1:8765/mcp",
    }
    if args.get("reveal"):
        out["token"] = ctx.ks.require(auth.TOKEN_KEY_NAME, 32).hex()
    else:
        out["note"] = (
            "token withheld; re-run with --reveal to print it "
            "(avoid doing so where stdout is captured to a log)"
        )
    return out


def cmd_init(ctx, args):
    """Create the vault: runtime directories, both encrypted databases, and keys.

    Takes no database connections — it runs before any exist — so it reads the
    vault layout from the context instead.
    """
    if ctx.ks is None:
        return {"ok": False, "error": "no keystore available on this context"}
    layout = ctx.paths if ctx.paths is not None else paths.from_env()
    return bootstrap.init_vault(layout, ctx.ks, reveal=bool(args.get("reveal")))


# A vault built only from manual exports has no Meta account behind it, so there
# is no real phone_number_id to record. The column is NOT NULL, so it carries this
# sentinel rather than a fabricated number — nothing here may invent one.
LOCAL_PHONE_NUMBER_ID = "local"

_CONVERSATION_TYPES = ("dm", "group")


def cmd_accounts_add(ctx, args):
    """Create a local account to hang conversations from.

    `import` refuses to guess its target, which left a fresh vault with no way to
    obtain the --account-id it demands. This verb is that missing source.
    """
    account_id = new_id("acc")
    ctx.vault.execute(
        "INSERT INTO accounts(id, waba_id, phone_number_id, display_phone) VALUES(?,NULL,?,NULL)",
        (account_id, LOCAL_PHONE_NUMBER_ID),
    )
    ctx.vault.commit()
    return {"ok": True, "account_id": account_id}


def cmd_accounts_list(ctx, args):
    # display_phone is never selected: an operations listing is not a reason to
    # put a full number on stdout or into a launchd log (INV-DISPLAY).
    return {
        "ok": True,
        "accounts": _rows(ctx.vault.execute("SELECT id, waba_id FROM accounts ORDER BY id")),
    }


def cmd_conversations_add(ctx, args):
    """Create a conversation, the target an import writes into."""
    account_id = args.get("account_id")
    if not account_id:
        return {"ok": False, "error": "--account-id is required (see `whatsvault accounts-add`)"}
    known = ctx.vault.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone()
    if not known:
        # Reported here rather than left to the foreign key, whose error names a
        # constraint instead of the thing the operator has to fix.
        return {"ok": False, "error": f"no such account: {account_id}"}
    kind = args.get("type") or "dm"
    if kind not in _CONVERSATION_TYPES:
        return {"ok": False, "error": f"unknown conversation type: {kind} (expected dm or group)"}
    conversation_id = new_id("cnv")
    ctx.vault.execute(
        "INSERT INTO conversations(id, account_id, type, subject) VALUES(?,?,?,?)",
        (conversation_id, account_id, kind, args.get("subject")),
    )
    ctx.vault.commit()
    return {"ok": True, "conversation_id": conversation_id, "account_id": account_id}


def cmd_conversations_list(ctx, args):
    return {
        "ok": True,
        "conversations": _rows(
            ctx.vault.execute(
                "SELECT id, account_id, type, subject, mcp_visibility FROM conversations ORDER BY id"
            )
        ),
    }


def cmd_oauth_pending(ctx, args):
    """Authorization requests waiting for approval.

    Only meaningful on a deployment behind a public URL. The consent page has
    nothing to submit, so this and cmd_oauth_approve are the only path by which
    an authorization is ever granted.
    """
    return {"ok": True, "pending": oauth.pending_requests(ctx.control, _now_ms())}


def cmd_oauth_approve(ctx, args):
    """Grant one pending read authorization.

    Running this at all means terminal access to the machine holding the vault,
    which is the authority being asserted -- the same shape as the phone holding
    the Secure Enclave key for sending. It grants READ only: there is no argument
    that widens scope, because the server issues exactly one.
    """
    code = args.get("code")
    if not code:
        return {"ok": False, "error": "--code is required (see `whatsvault oauth-pending`)"}
    try:
        granted = oauth.approve(ctx.control, user_code=code, now_ms=_now_ms(), audit_key=_audit_key(ctx))
    except oauth.OAuthError as exc:
        return {"ok": False, "error": f"{exc.code}: {exc.description}"}
    return {"ok": True, "approved": granted["request_id"], "scope": oauth.READ_ONLY_SCOPE}


def cmd_oauth_revoke(ctx, args):
    """Revoke every OAuth token. The connector must re-authorise, which needs a
    fresh approval here. This is the off switch for a public deployment."""
    return {"ok": True, "revoked": oauth.revoke_all(ctx.control, _now_ms(), _audit_key(ctx))}


def cmd_import(ctx, args):
    """Import a WhatsApp text export into the vault.

    Evidence-only: an import writes to vault.db and can never touch control.db,
    so an imported timestamp cannot reopen the 24-hour send window (INV-IMPORT).
    A dry run is performed first so the caller sees what would be written.
    """
    path = args.get("path")
    if not path:
        return {"ok": False, "error": "--path is required"}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"cannot read {path}: {exc}"}

    tz_name = args.get("timezone") or "UTC"
    date_format = args.get("date_format") or "DMY"
    label = args.get("self_label") or "Me"
    preview = whatsapp_export.dry_run(text, date_format, tz_name, self_participant_label=label)
    if args.get("dry_run"):
        return {"ok": True, "dry_run": True, **preview}

    conversation_id = args.get("conversation_id")
    account_id = args.get("account_id")
    if not conversation_id or not account_id:
        return {
            "ok": False,
            "error": "--conversation-id and --account-id are required (use --dry-run to preview)",
        }
    result = whatsapp_export.import_batch(
        ctx.vault,
        text,
        source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        date_format=date_format,
        tz_name=tz_name,
        conversation_id=conversation_id,
        account_id=account_id,
        self_participant_label=label,
    )
    return {"ok": True, **result}


def cmd_import_undo(ctx, args):
    """Reverse one import batch using its recorded provenance."""
    batch_id = args.get("job_id")
    if not batch_id:
        return {"ok": False, "error": "--job-id is required"}
    return {"ok": True, **whatsapp_export.undo_batch(ctx.vault, batch_id)}


def cmd_mcp_visibility(ctx, args):
    """Fence a conversation from the MCP surface, or unfence it.

    CLI/phone only by design: `set_mcp_visibility` is in the MCP forbidden set,
    so a model can never widen its own visibility (ledger #23).
    """
    conversation_id = args.get("conversation_id")
    visibility = args.get("visibility")
    if not conversation_id or not visibility:
        return {"ok": False, "error": "--conversation-id and --visibility are required"}
    try:
        acl.set_visibility(ctx.vault, conversation_id, visibility)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "conversation_id": conversation_id, "visibility": visibility}


def cmd_health(ctx, args):
    return health.status(ctx.vault, ctx.control)


def cmd_devices_list(ctx, args):
    return {
        "ok": True,
        "devices": _rows(ctx.control.execute("SELECT id, name, status, key_algorithm FROM approval_devices")),
    }


def cmd_devices_revoke(ctx, args):
    devices.revoke(ctx.control, args["device_id"])
    return {"ok": True, "revoked": args["device_id"]}


def cmd_dlq_list(ctx, args):
    return {
        "ok": True,
        "dlq": _rows(
            ctx.vault.execute("SELECT id, failure_class, recipient_key_id, first_seen_ms FROM ingest_dlq")
        ),
    }


def cmd_dlq_show(ctx, args):
    row = ctx.vault.execute(
        "SELECT id, failure_class, failure_code, pipeline_stage, recipient_key_id, crypto_version, "
        "ciphertext_sha256, sanitised_detail FROM ingest_dlq WHERE id=?",
        (args["dlq_id"],),
    ).fetchone()
    return {"ok": row is not None, "row": dict(row) if row else None}


def cmd_keys_list(ctx, args):
    return {
        "ok": True,
        "keys_referenced_by_dlq": [
            r[0]
            for r in ctx.vault.execute(
                "SELECT DISTINCT recipient_key_id FROM ingest_dlq WHERE recipient_key_id IS NOT NULL"
            )
        ],
    }


def cmd_templates_list(ctx, args):
    return {
        "ok": True,
        "templates": _rows(ctx.control.execute("SELECT template_id, name, language, status FROM templates")),
    }


def cmd_reconcile_list(ctx, args):
    return {
        "ok": True,
        "candidates": _rows(
            ctx.control.execute(
                "SELECT id, wamid, status, state FROM reconciliation_candidates WHERE state='POSSIBLE_MATCH'"
            )
        ),
    }


def cmd_reconcile_resolve(ctx, args):
    r = reconcile.resolve(ctx.control, args["candidate_id"], decision=args.get("decision") or "resolve")
    return {"ok": True, **r}


def cmd_scheduler_list(ctx, args):
    return {
        "ok": True,
        "jobs": _rows(
            ctx.control.execute(
                "SELECT job_id, conversation_id, generation_mode, enabled FROM scheduled_jobs"
            )
        ),
    }


def _set_enabled(ctx, job_id, enabled):
    ctx.control.execute("UPDATE scheduled_jobs SET enabled=? WHERE job_id=?", (enabled, job_id))
    ctx.control.commit()
    return {"ok": True, "job_id": job_id, "enabled": enabled}


def cmd_scheduler_enable(ctx, args):
    return _set_enabled(ctx, args["job_id"], 1)


def cmd_scheduler_disable(ctx, args):
    return _set_enabled(ctx, args["job_id"], 0)


COMMANDS = {
    "doctor": cmd_doctor,
    "init": cmd_init,
    "mcp-provision": cmd_mcp_provision,
    "mcp-visibility": cmd_mcp_visibility,
    "accounts-add": cmd_accounts_add,
    "accounts-list": cmd_accounts_list,
    "conversations-add": cmd_conversations_add,
    "conversations-list": cmd_conversations_list,
    "oauth-pending": cmd_oauth_pending,
    "oauth-approve": cmd_oauth_approve,
    "oauth-revoke": cmd_oauth_revoke,
    "import": cmd_import,
    "import-undo": cmd_import_undo,
    "health": cmd_health,
    "devices-list": cmd_devices_list,
    "devices-revoke": cmd_devices_revoke,
    "dlq-list": cmd_dlq_list,
    "dlq-show": cmd_dlq_show,
    "keys-list": cmd_keys_list,
    "templates-list": cmd_templates_list,
    "reconcile-list": cmd_reconcile_list,
    "reconcile-resolve": cmd_reconcile_resolve,
    "scheduler-list": cmd_scheduler_list,
    "scheduler-enable": cmd_scheduler_enable,
    "scheduler-disable": cmd_scheduler_disable,
}

# Verbs that run before the vault exists, so cli.main must NOT open the databases
# before dispatching them. Both touch only the Keychain; opening a database first
# raised KeyMissing and made `init` — the documented first command — unreachable.
BOOTSTRAP_VERBS = frozenset({"init", "mcp-provision"})
