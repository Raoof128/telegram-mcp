"""Export importer write path (spec §8) — refuse, don't guess.

Gates (in order): self-participant present (#25, direction is NOT NULL), timezone
present, declared date-format agrees with whole-file validation, every DST
fold/nonexistent instant explicitly resolved (#27). On success, one transaction
writes the batch, provisional participants (UNLINKED), evidence messages
(origin='manual_export', window_eligible=0, never window/control), per-batch
observations linking each message to its provisional sender (#26), and a
non-write-capable conversation_sources row (SR-1). Messages dedupe by
import_fingerprint; a re-import attaches a new observation instead of duplicating.
Imports NEVER touch control.db or a messaging window (INV-IMPORT)."""

import datetime as dt
import hashlib
import os
import tempfile
import time
from zoneinfo import ZoneInfo

from .. import ids
from ..crypto import atrest
from ..search.index import stage_message
from ..timemodel import from_local_minute
from . import fingerprint as F
from . import parse as P
from .grammar import suggest_families

FINGERPRINT_VERSION = 1
PARSER_VERSION = 1


class ImportRefused(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _messages(records: list[dict]) -> list[dict]:
    return [r for r in records if r["kind"] == "message"]


def _minute_epoch_ms(rec: dict, tz_name: str, dst_resolutions: dict) -> int:
    fold = 0
    if rec["dst_class"] in ("fold", "nonexistent"):
        fold = int(dst_resolutions[rec["source_ordinal"]])
    local = dt.datetime(
        rec["year"],
        rec["month"],
        rec["day"],
        rec["hour"],
        rec["minute"],
        0,
        tzinfo=ZoneInfo(tz_name),
        fold=fold,
    )
    return int(local.timestamp() * 1000)


def dry_run(text: str, date_format: str, tz_name: str, *, self_participant_label) -> dict:
    records = P.parse_transcript(text, date_format, tz_name) if (date_format and tz_name) else []
    msgs = _messages(records)
    senders = []
    for r in msgs:
        if r["sender"] not in senders:
            senders.append(r["sender"])
    dst_cases = [
        {"source_ordinal": r["source_ordinal"], "dst_class": r["dst_class"]}
        for r in msgs
        if r["dst_class"] in ("fold", "nonexistent")
    ]
    boundaries = [
        {"source_start_offset": r["source_start_offset"], "source_end_offset": r["source_end_offset"]}
        for r in records
        if r["kind"] == "ambiguous_boundary"
    ]
    self_norm = (self_participant_label or "").strip()
    return {
        "would_add": len(msgs),
        "suggested_families": suggest_families(text) if text else [],
        "format_declared_ok": bool(date_format) and date_format in (suggest_families(text) if text else []),
        "dst_cases": dst_cases,
        "unlinked_participants": senders,
        "system_lines": sum(1 for r in records if r["kind"] in ("system", "system_unknown")),
        "ambiguous_boundaries": boundaries,
        "self_resolved": bool(self_norm) and any(s.strip() == self_norm for s in senders),
    }


def import_batch(
    vault_conn,
    text,
    *,
    source_sha256,
    date_format,
    tz_name,
    conversation_id,
    account_id,
    self_participant_label,
    dst_resolutions=None,
    source_artifact=None,
) -> dict:
    dst_resolutions = dst_resolutions or {}
    # --- refusal gates (before any write) ---
    if not self_participant_label:
        raise ImportRefused(
            "MISSING_SELF", "self_participant_label is required (direction cannot be guessed)"
        )
    if not date_format:
        raise ImportRefused("MISSING_FORMAT", "date_format is required")
    if not tz_name:
        raise ImportRefused("MISSING_TZ", "tz_name is required")
    if date_format not in suggest_families(text):
        raise ImportRefused(
            "FORMAT_MISMATCH", f"{date_format} not among validating families {suggest_families(text)}"
        )

    records = P.parse_transcript(text, date_format, tz_name)
    msgs = _messages(records)
    for r in msgs:
        if r["dst_class"] in ("fold", "nonexistent") and r["source_ordinal"] not in dst_resolutions:
            raise ImportRefused(
                "DST_UNRESOLVED", f"source_ordinal {r['source_ordinal']} is a {r['dst_class']} instant"
            )

    self_norm = self_participant_label.strip()
    bat_id = ids.new_id("bat")
    art_path, art_sha = source_artifact if source_artifact else (None, None)
    added = 0
    obs_count = 0
    try:
        vault_conn.execute(
            "INSERT INTO import_batches(id, source_kind, source_sha256, source_artifact_path, "
            "source_artifact_sha256, declared_date_format, declared_timezone, self_participant_label, "
            "parser_family, parser_version, imported_at_ms, message_count, system_event_count) "
            "VALUES(?, 'manual_export', ?, ?, ?, ?, ?, ?, 'whatsapp_txt', ?, NULL, ?, ?)",
            (
                bat_id,
                source_sha256,
                art_path,
                art_sha,
                date_format,
                tz_name,
                self_participant_label,
                PARSER_VERSION,
                len(msgs),
                sum(1 for r in records if r["kind"] in ("system", "system_unknown")),
            ),
        )
        # provisional participants (one per distinct sender), UNLINKED
        participant_id: dict[str, str] = {}
        for r in msgs:
            s = r["sender"]
            if s not in participant_id:
                pid = ids.new_id("imp")
                participant_id[s] = pid
                vault_conn.execute(
                    "INSERT INTO import_participants(id, import_batch_id, raw_display_name) VALUES(?,?,?)",
                    (pid, bat_id, s),
                )
        # messages + observations
        occ: dict[tuple, int] = {}
        for r in msgs:
            minute_ms = _minute_epoch_ms(r, tz_name, dst_resolutions)
            iv = from_local_minute(minute_ms)
            body = r.get("body", "")
            mtype = "media" if r.get("media_state") else "text"
            content_fp = F.content_fingerprint(mtype, body)
            bucket = (conversation_id, minute_ms, r["sender"], mtype, content_fp)
            idx = occ.get(bucket, 0)
            occ[bucket] = idx + 1
            import_fp = F.import_fingerprint(
                FINGERPRINT_VERSION, conversation_id, minute_ms, r["sender"], mtype, content_fp, idx
            )
            existing = vault_conn.execute(
                "SELECT id FROM messages WHERE import_fingerprint=?", (import_fp,)
            ).fetchone()
            if existing:
                msg_id = existing[0]
            else:
                msg_id = ids.new_id("msg")
                direction = "out" if r["sender"].strip() == self_norm else "in"
                vault_conn.execute(
                    "INSERT INTO messages(id, account_id, conversation_id, sender_contact_id, direction, "
                    "ts_lower_ms, ts_upper_ms_exclusive, ts_precision, tz_name, tz_basis, type, "
                    "text_original, origin, window_eligible, import_fingerprint) "
                    "VALUES(?,?,?,NULL,?,?,?,?,?,?,?,?,'manual_export',0,?)",
                    (
                        msg_id,
                        account_id,
                        conversation_id,
                        direction,
                        iv.ts_lower_ms,
                        iv.ts_upper_ms_exclusive,
                        iv.ts_precision,
                        tz_name,
                        "explicit_import_setting",
                        mtype,
                        body,
                        import_fp,
                    ),
                )
                # Index inside the import (INV-SEARCH: the index is derived and
                # disposable). Without this an import leaves every message
                # invisible to search — and while live ingest is Phase-0 gated,
                # import is the ONLY way data enters the vault. doctor's
                # `search_missing` check already asserted this; nothing satisfied
                # it. Only new messages are indexed, so a re-import stays
                # idempotent with the fingerprint dedupe above.
                stage_message(vault_conn, msg_id, body)
                added += 1
            vault_conn.execute(
                "INSERT INTO message_import_observations(batch_id, message_id, sender_import_participant_id, "
                "source_ordinal, source_start_offset, source_end_offset, source_fingerprint, "
                "fingerprint_version, parser_version) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    bat_id,
                    msg_id,
                    participant_id[r["sender"]],
                    r["source_ordinal"],
                    r.get("source_start_offset"),
                    r.get("source_end_offset"),
                    content_fp,
                    FINGERPRINT_VERSION,
                    PARSER_VERSION,
                ),
            )
            obs_count += 1
        # conversation_sources: this conversation is (partly) sourced from this import, never write-capable
        vault_conn.execute(
            "INSERT INTO conversation_sources(id, conversation_id, source_kind, write_capable, "
            "account_id, import_batch_id) VALUES(?,?, 'manual_export', 0, ?, ?)",
            (ids.new_id("src"), conversation_id, account_id, bat_id),
        )
        vault_conn.commit()
    except Exception:
        vault_conn.rollback()
        raise

    return {
        "batch_id": bat_id,
        "added": added,
        "observations": obs_count,
        "participants": len({r["sender"] for r in msgs}),
    }


# ---- Task 6: atomic source-artefact store + observation-aware undo + reparse ----
_SRC_AAD_PREFIX = b"WHATSVAULT-IMPORT-SRC-V1\n"


def store_source_artifact(dest_dir: str, source_sha256: str, raw_bytes: bytes, key: bytes) -> tuple[str, str]:
    """#30 atomic contract: seal -> fsync -> atomic rename -> verify hash. The batch row
    is only written afterwards with the returned (path, sealed_sha256), so a crash before
    the artefact durably lands never yields a 'successful' import."""
    aad = _SRC_AAD_PREFIX + source_sha256.encode("ascii")
    sealed = atrest.seal_blob(key, raw_bytes, 0, aad)
    sealed_sha = hashlib.sha256(sealed).hexdigest()
    final = os.path.join(dest_dir, source_sha256 + ".wvblob")
    fd, tmp = tempfile.mkstemp(dir=dest_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(sealed)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    with open(final, "rb") as f:
        on_disk = f.read()
    if hashlib.sha256(on_disk).hexdigest() != sealed_sha:
        raise OSError("source artefact hash mismatch after write")
    return final, sealed_sha


def undo_batch(vault_conn, batch_id: str) -> dict:
    """Delete this batch's observations; delete a canonical message only when it now has
    zero observations and no provider provenance (wamid IS NULL); drop the batch's
    participants + conversation_sources row; stamp undone_at_ms. Never deletes the artefact."""
    msg_ids = [
        r[0]
        for r in vault_conn.execute(
            "SELECT message_id FROM message_import_observations WHERE batch_id=?", (batch_id,)
        ).fetchall()
    ]
    try:
        vault_conn.execute("DELETE FROM message_import_observations WHERE batch_id=?", (batch_id,))
        deleted = 0
        for mid in msg_ids:
            remaining = vault_conn.execute(
                "SELECT COUNT(*) FROM message_import_observations WHERE message_id=?", (mid,)
            ).fetchone()[0]
            wamid = vault_conn.execute("SELECT wamid FROM messages WHERE id=?", (mid,)).fetchone()
            if remaining == 0 and wamid is not None and wamid[0] is None:
                vault_conn.execute("DELETE FROM messages WHERE id=?", (mid,))
                deleted += 1
        vault_conn.execute("DELETE FROM import_participants WHERE import_batch_id=?", (batch_id,))
        vault_conn.execute("DELETE FROM conversation_sources WHERE import_batch_id=?", (batch_id,))
        vault_conn.execute(
            "UPDATE import_batches SET undone_at_ms=? WHERE id=?", (int(time.time() * 1000), batch_id)
        )
        vault_conn.commit()
    except Exception:
        vault_conn.rollback()
        raise
    return {"batch_id": batch_id, "observations_deleted": len(msg_ids), "messages_deleted": deleted}


def reparse(vault_conn, batch_id: str, key: bytes, dest_dir: str) -> dict:
    row = vault_conn.execute(
        "SELECT source_artifact_path, source_sha256, declared_date_format, declared_timezone, "
        "self_participant_label FROM import_batches WHERE id=?",
        (batch_id,),
    ).fetchone()
    if not row:
        raise ValueError("unknown batch")
    path, ssha, fmt, tz, self_label = row
    if not path:
        raise ValueError("batch has no stored source artefact")
    with open(path, "rb") as f:
        sealed = f.read()
    raw = atrest.open_blob(key, sealed, _SRC_AAD_PREFIX + ssha.encode("ascii"))
    return dry_run(raw.decode("utf-8"), fmt, tz, self_participant_label=self_label)
