"""Closed settings registry behind the ``settings`` table (spec §12.2).

``settings`` is not a persistence escape hatch. Spec §12.2 requires a
compile-time allowlist of keys with a typed validator for each value, and
rejects unknown keys. Nothing that could carry Telegram content — message
bodies, captions, search queries, URLs from messages, usernames, phone
numbers, raw tool arguments — has a key here, so no such value can be
written through this module.

Exposure-budget rows carry the spec §23C.1 normative baseline as defaults.
Phase 2 stores and validates them but enforces nothing: budget enforcement
is Phase 3, behind the ``DisclosureGate``. Rows whose default the spec does
not state are marked ``origin="impl"``, in the same spirit as the key
registry.

Writes go through the operator admin path with user presence (spec §12.2);
this module is the validator and the row writer, never the authorizer.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "SETTINGS_REGISTRY",
    "SettingSpec",
    "all_settings",
    "get_setting",
    "put_setting",
    "set_setting",
    "validate_setting",
]

_DEGRADED_REASONS = ("anchor_refresh_failure", "chain_verify_failure", "anchor_read_failure")
_ANCHOR_PROVIDERS = ("file_0600", "macos_system_keychain")
# A bare filename under the daemon's own anchor directory: no separators, no
# traversal, and nothing that could carry prose.
_ANCHOR_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_TDR_OR_EMPTY = re.compile(r"(tdr_[a-z2-7]{26})?\Z")
_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
_RELEASE_PROFILES = ("safe_demo", "local_dev", "production")


@dataclass(frozen=True)
class SettingSpec:
    """One allowlisted key: type, bounds, default, and where it comes from."""

    kind: str  # "int" | "str"
    default: Any
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] | None = None
    pattern: re.Pattern[str] | None = None
    origin: str = "spec"  # "spec" | "impl"
    phase: int = 2

    def __post_init__(self) -> None:
        if self.kind not in ("int", "str"):
            raise ValueError("invalid setting kind")
        if self.origin not in ("spec", "impl"):
            raise ValueError("invalid setting origin")


def _budget(default: int) -> SettingSpec:
    return SettingSpec("int", default, minimum=0, maximum=2**53 - 1, phase=3)


SETTINGS_REGISTRY: dict[str, SettingSpec] = {
    # spec §23C.1 normative baseline; inert until Phase 3 enforces budgets.
    "exposure_budget.rolling_window_minutes": SettingSpec(
        "int", 30, minimum=1, maximum=1440, phase=3
    ),
    "exposure_budget.soft_records_per_client_project": _budget(100),
    "exposure_budget.hard_records_per_client_project": _budget(500),
    "exposure_budget.soft_bytes_per_client_project": _budget(500_000),
    "exposure_budget.hard_bytes_per_client_project": _budget(5_000_000),
    "exposure_budget.soft_records_per_client_global": _budget(300),
    "exposure_budget.hard_records_per_client_global": _budget(1_500),
    "exposure_budget.soft_bytes_per_client_global": _budget(1_500_000),
    "exposure_budget.hard_bytes_per_client_global": _budget(15_000_000),
    # audit-checkpoint cadence. Spec §26.5 is normative and explicit: a
    # signed checkpoint "at least every 500 events or 60 minutes, whichever
    # comes first". The maxima are therefore that bound, not a comfort
    # limit, so no operator write can configure a non-compliant cadence.
    # The defaults are tighter than the bound, which §26.5 permits.
    "audit.checkpoint_cadence_events": SettingSpec(
        "int", 100, minimum=1, maximum=500, origin="impl", phase=3
    ),
    "audit.checkpoint_cadence_seconds": SettingSpec(
        "int", 3_600, minimum=60, maximum=3_600, phase=3
    ),
    # --- Phase 3: audit integrity (design §6.3, §6.8) ----------------------
    # A tdr_ ref and a fixed reason code are not content, so the registry's
    # prohibition is respected: neither can carry a message body or a query.
    "audit.integrity_degraded": SettingSpec("int", 0, minimum=0, maximum=1, origin="spec", phase=3),
    "audit.degraded_disclosure_ref": SettingSpec(
        "str", "", pattern=_TDR_OR_EMPTY, origin="spec", phase=3
    ),
    "audit.degraded_reason": SettingSpec(
        "str", "", choices=("", *_DEGRADED_REASONS), origin="spec", phase=3
    ),
    # The spec's reference config declares macos_system_keychain; this build
    # uses the reviewed 0600-file fallback, so the declaration follows it.
    # A manifest that claims a provider the build does not use is a lie in
    # the artifact that exists to prevent lies.
    "audit.external_anchor_provider": SettingSpec(
        "str", "file_0600", choices=_ANCHOR_PROVIDERS, origin="impl", phase=3
    ),
    "audit.external_anchor_ref": SettingSpec(
        "str", "audit-head-anchor.json", pattern=_ANCHOR_REF_RE, origin="impl", phase=3
    ),
    # --- Phase 3: retention (spec §32 privacy block) -----------------------
    # Phase 3 sets the values; Phase 5 enforces the purge schedules and must
    # not be handed values it cannot satisfy.
    "retention.disclosure_receipt_days": SettingSpec("int", 180, minimum=1, maximum=3_650, phase=3),
    "retention.exposure_ledger_days": SettingSpec("int", 30, minimum=1, maximum=3_650, phase=3),
    "retention.audit_events_days": SettingSpec("int", 30, minimum=1, maximum=3_650, phase=3),
    "retention.audit_checkpoint_days": SettingSpec("int", 180, minimum=1, maximum=3_650, phase=3),
    "retention.verification_key_grace_days": SettingSpec(
        "int", 30, minimum=1, maximum=3_650, phase=3
    ),
    "retention.message_ref_days": SettingSpec("int", 180, minimum=1, maximum=3_650, phase=3),
    # comms v0.3 cutover (A6, G3): once "sealed", legacy audit appends are refused by a trigger.
    "audit.append_state": SettingSpec(
        "str", "open", choices=("open", "sealed"), origin="impl", phase=3
    ),
    # comms v0.3 B14: the session revoke's two-transaction state and the remote outcome.
    "telegram.session_state": SettingSpec(
        "str", "active", choices=("active", "revoking", "logged_out"), origin="impl", phase=3
    ),
    # comms v0.3 B16: every successful login is a new session generation; the account the
    # owner currently uses (an explicit --new-account switch moves it; old rows stay).
    "telegram.session_generation": SettingSpec(
        "int", 1, minimum=1, maximum=1_000_000_000, origin="impl", phase=3
    ),
    "telegram.active_account": SettingSpec(
        "str", "", pattern=re.compile(r"(tga_[a-z0-9]{26})?"), origin="impl", phase=3
    ),
    "telegram.remote_revoke": SettingSpec(
        "str", "none", choices=("none", "confirmed", "failed", "unknown"), origin="impl", phase=3
    ),
    # comms v0.3 (A33, G3): once "revoked", no bearer client can be enabled or added.
    "auth.tgml1_state": SettingSpec(
        "str", "open", choices=("open", "revoked"), origin="impl", phase=3
    ),
    # non-secret release metadata.
    "release.profile": SettingSpec("str", "safe_demo", choices=_RELEASE_PROFILES),
    "release.version": SettingSpec("str", "0.1.10", origin="impl"),
}


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_setting(key: str, value: Any) -> Any:
    """Return the validated value, or raise ``ValueError``."""
    spec = SETTINGS_REGISTRY.get(key)
    if spec is None:
        raise ValueError("unknown setting key")
    if spec.kind == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"setting {key} must be an integer")
        if spec.minimum is not None and value < spec.minimum:
            raise ValueError(f"setting {key} is below its minimum")
        if spec.maximum is not None and value > spec.maximum:
            raise ValueError(f"setting {key} is above its maximum")
        return value
    if not isinstance(value, str):
        raise ValueError(f"setting {key} must be a string")  # noqa: TRY004 -- uniform ValueError on settings validation
    if spec.choices is not None and value not in spec.choices:
        raise ValueError(f"setting {key} is not an allowed value")
    if spec.pattern is not None and spec.pattern.fullmatch(value) is None:
        raise ValueError(f"setting {key} does not match its allowed shape")
    if key == "release.version" and _VERSION_RE.fullmatch(value) is None:
        raise ValueError("setting release.version must be semantic")
    if len(value) > 128:
        raise ValueError(f"setting {key} is too long")
    return value


def put_setting(conn: sqlite3.Connection, key: str, value: Any, *, now: str | None = None) -> Any:
    """Validate and upsert one allowlisted setting inside the caller's transaction."""
    validated = validate_setting(key, value)
    conn.execute(
        "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json,"
        " updated_at = excluded.updated_at",
        (key, json.dumps(validated, separators=(",", ":")), now or _now_iso()),
    )
    return validated


def set_setting(conn: sqlite3.Connection, key: str, value: Any, *, now: str | None = None) -> Any:
    """Validate, upsert and commit one allowlisted setting."""
    validated = put_setting(conn, key, value, now=now)
    conn.commit()
    return validated


def get_setting(conn: sqlite3.Connection, key: str) -> Any:
    """Stored value, else the registry default. Unknown keys raise."""
    spec = SETTINGS_REGISTRY.get(key)
    if spec is None:
        raise ValueError("unknown setting key")
    row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return spec.default
    return validate_setting(key, json.loads(row[0]))


def all_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every allowlisted key with its effective value."""
    return {key: get_setting(conn, key) for key in SETTINGS_REGISTRY}
