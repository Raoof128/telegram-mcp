"""Prefixed ULID identifiers (spec §3.2). Ordering is a stable tie-breaker
only, never message chronology."""

import re

from ulid import ULID

PREFIXES = frozenset(
    {
        "acc",  # accounts
        "cnt",  # contacts
        "cnv",  # conversations
        "src",  # conversation_sources
        "msg",  # messages
        "rev",  # message_revisions
        "att",  # attachments
        "evt",  # ingest_events / message_status_events
        "drf",  # drafts
        "apv",  # approvals
        "atm",  # send_attempts
        "cap",  # capability_grants
        "dev",  # approval_devices
        "bat",  # import_batches
        "imp",  # import_participants
        "dlq",  # ingest_dlq
        "tpl",  # templates
        "job",  # scheduled_jobs
        "run",  # job_runs
        "rcn",  # reconciliation_candidates
        "aud",  # audit_log
    }
)

_ULID_RE = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")


class IdError(ValueError):
    pass


def new_id(prefix: str) -> str:
    if prefix not in PREFIXES:
        raise IdError(f"unknown id prefix: {prefix!r}")
    return f"{prefix}_{ULID()!s}"


def validate(prefix: str, value: str) -> str:
    if prefix not in PREFIXES:
        raise IdError(f"unknown id prefix: {prefix!r}")
    marker = f"{prefix}_"
    if not value.startswith(marker):
        raise IdError(f"id {value!r} does not carry prefix {prefix!r}")
    if not _ULID_RE.fullmatch(value[len(marker) :]):
        raise IdError(f"id {value!r} has malformed ULID body")
    return value


def parse_id(value: str) -> str:
    """Return the prefix of a well-formed id, or raise. The inverse of new_id:
    used where a caller has an identifier and needs to know what it refers to."""
    prefix, _, body = str(value).partition("_")
    if prefix not in PREFIXES:
        raise IdError(f"unknown id prefix: {prefix!r}")
    if not _ULID_RE.fullmatch(body):
        raise IdError(f"id {value!r} has malformed ULID body")
    return prefix
