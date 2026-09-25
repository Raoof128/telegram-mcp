"""JSON Schema building blocks and the read/write tool builders (comms v0.3 D18–D24).

Every family module builds its ``ToolSpec`` entries with ``read`` and ``write``, so the rules
live once: a write's schema requires ``request_id`` (``^req_[a-z2-7]{26}$``, G16); its
annotations come from ``SEMANTICS`` (destructive for a destructive, non-idempotent operation;
idempotent for a set-state one; open-world for a provider write); a read is read-only,
idempotent and never destructive. Refs are typed by prefix; a context item may carry any
field except a provider identity.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from comms.core.providers.capability import Capability
from comms.core.providers.semantics import SEMANTICS
from comms.core.refs import CORE_PREFIXES
from comms.mcp.spec import ToolSpec

__all__ = [
    "ACTOR",
    "ANY_OBJECT",
    "BOOL",
    "OUTCOMES",
    "READ_FAILURES",
    "WRITE_FAILURES",
    "array",
    "enum",
    "integer",
    "nullable",
    "obj",
    "read",
    "ref",
    "string",
    "write",
    "write_result",
]

BOOL: Mapping[str, Any] = {"type": "boolean"}
ANY_OBJECT: Mapping[str, Any] = {"type": "object"}
READ_FAILURES = ("INVALID_ARGUMENT", "NOT_FOUND", "AUDIT_INTEGRITY_DEGRADED")
WRITE_FAILURES = (
    "INVALID_ARGUMENT",
    "NOT_FOUND",
    "REQUEST_ID_REUSE",
    "AUDIT_INTEGRITY_DEGRADED",
)
PROVIDER_FAILURES = (
    "CAPABILITY_UNAVAILABLE",
    "NOT_AUTHORIZED",
    "ACCOUNT_INELIGIBLE",
    "PROVIDER_UNSUPPORTED",
    "NOT_CONFIGURED",
    "RATE_LIMITED",
    "PROVIDER_UNAVAILABLE",
    "OUTCOME_UNKNOWN",
)


def ref(*kinds: str) -> dict[str, Any]:
    """A ref of one of ``kinds``: its prefix and 26 base32 characters."""
    prefixes = "|".join(sorted(CORE_PREFIXES[k].removesuffix("_") for k in kinds))
    return {"type": "string", "pattern": f"^(?:{prefixes})_[a-z2-7]{{26}}$"}


def string(low: int = 0, high: int = 4096) -> dict[str, Any]:
    return {"type": "string", "minLength": low, "maxLength": high}


def integer(low: int | None = None, high: int | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer"}
    if low is not None:
        schema["minimum"] = low
    if high is not None:
        schema["maximum"] = high
    return schema


def enum(values: Iterable[str]) -> dict[str, Any]:
    return {"type": "string", "enum": sorted(values)}


def nullable(schema: Mapping[str, Any]) -> dict[str, Any]:
    return {"anyOf": [dict(schema), {"type": "null"}]}


def array(items: Mapping[str, Any], *, low: int = 0, high: int | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "array", "items": dict(items), "minItems": low}
    if high is not None:
        schema["maxItems"] = high
    return schema


def obj(
    properties: Mapping[str, Any], required: Sequence[str] = (), *, closed: bool = True
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {k: dict(v) for k, v in properties.items()},
        "required": list(required),
    }
    if closed:
        schema["additionalProperties"] = False
    return schema


REQUEST_ID = ref("request")
ACTORS = ("telegram_bot", "telegram_user", "whatsapp_cloud")
ACTOR: Mapping[str, Any] = enum(ACTORS)
OUTCOMES = ("SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN", "IN_FLIGHT", "INVITE_REQUIRED")


def write_result(**extra: Mapping[str, Any]) -> dict[str, Any]:
    """A provider write's structured truth (P §73): what ran, as whom, and how it ended."""
    fields = {
        "group": ref("group", "recipient"),
        "operation": string(1, 64),
        "result": enum(OUTCOMES),
        "code": nullable(string(1, 64)),
        "actor": nullable(ACTOR),
        "op_ref": nullable(ref("operation")),
        "replayed": BOOL,
        **extra,
    }
    return obj(fields, list(fields))


def read(
    name: str,
    title: str,
    description: str,
    service: str,
    inputs: Mapping[str, Any],
    required: Sequence[str],
    output: Mapping[str, Any],
    *,
    failures: Sequence[str] = READ_FAILURES,
    open_world: bool = False,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        title=title,
        description=description,
        input_schema=obj(inputs, required),
        output_schema=output,
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=open_world,
        requires_request_id=False,
        failure_modes=tuple(failures),
        service=service,
    )


def _semantics(capability: Capability) -> tuple[bool, bool]:
    classes = {s.retry_class for (c, _actor), s in SEMANTICS.items() if c is capability}
    if not classes:
        raise ValueError("a write tool names a capability no adapter supports")
    return "DESTRUCTIVE_NONIDEMPOTENT" in classes, classes == {"SET_STATE"}


def write(
    name: str,
    title: str,
    description: str,
    service: str,
    inputs: Mapping[str, Any],
    required: Sequence[str],
    output: Mapping[str, Any],
    *,
    capability: Capability | None = None,
    destructive: bool = False,
    idempotent: bool = False,
    open_world: bool = False,
    failures: Sequence[str] = (),
) -> ToolSpec:
    """A write. With ``capability`` it is a provider write whose annotations come from
    ``SEMANTICS``; without, a local write whose annotations are given. ``destructive=True``
    also marks a provider write that overwrites what cannot be restored automatically (an
    edit, a title, a restriction), even when retrying it is safe (A-list, test_ai_boundary)."""
    if capability is not None:
        semantic_destructive, idempotent = _semantics(capability)
        destructive = destructive or semantic_destructive
        failures = (*WRITE_FAILURES, *PROVIDER_FAILURES, *failures)
    else:
        failures = (*WRITE_FAILURES, *failures)
    return ToolSpec(
        name=name,
        title=title,
        description=description,
        input_schema=obj({**inputs, "request_id": REQUEST_ID}, [*required, "request_id"]),
        output_schema=output,
        read_only=False,
        destructive=destructive,
        idempotent=idempotent,
        open_world=open_world or capability is not None,
        requires_request_id=True,
        failure_modes=tuple(dict.fromkeys(failures)),
        service=service,
    )
