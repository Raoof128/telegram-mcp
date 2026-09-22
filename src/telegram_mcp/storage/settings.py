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
    "set_setting",
    "validate_setting",
]

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
    # audit-checkpoint cadence: spec §12.2 allows the key and states no
    # number, so these defaults are implementation choices.
    "audit.checkpoint_cadence_events": SettingSpec(
        "int", 100, minimum=1, maximum=100_000, origin="impl", phase=3
    ),
    "audit.checkpoint_cadence_seconds": SettingSpec(
        "int", 3_600, minimum=60, maximum=604_800, origin="impl", phase=3
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
    if key == "release.version" and _VERSION_RE.fullmatch(value) is None:
        raise ValueError("setting release.version must be semantic")
    if len(value) > 128:
        raise ValueError(f"setting {key} is too long")
    return value


def set_setting(conn: sqlite3.Connection, key: str, value: Any, *, now: str | None = None) -> Any:
    """Validate and upsert one allowlisted setting."""
    validated = validate_setting(key, value)
    conn.execute(
        "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json,"
        " updated_at = excluded.updated_at",
        (key, json.dumps(validated, separators=(",", ":")), now or _now_iso()),
    )
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
