"""``TOOL_CATALOG``: the one source of the MCP surface (comms v0.3 A29; P §36–39).

``tools/list``, the dispatch allowlist, the schemas, the annotations and the
documentation table are all generated from this tuple. It is static and ordered:
capability state is answered at call time, never by hiding a tool. Part A ships only
the seed tool; Part D adds P §22–35 and ``comms_admin_identity_inspect``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from comms.core.canonical import jcs_dumps

__all__ = ["TOOL_CATALOG", "ToolSpec", "catalog_digest", "tool_schema_digest", "tools_list_payload"]

_NAME = re.compile(r"comms_[a-z][a-z0-9_]*\Z")
_SERVICE = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\Z")
# P §37: no untyped provider tunnel. No input schema may name one of these properties.
_RAW_PROPERTIES = frozenset({"method", "path", "endpoint", "rpc", "raw"})


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool
    requires_request_id: bool
    failure_modes: tuple[str, ...]
    service: str  # "<service>.<method>" in the ServiceRegistry


_TRANSPORT = {"type": "string", "enum": ["telegram", "whatsapp"]}

TOOL_CATALOG: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="comms_capability_list",
        title="List capabilities",
        description=(
            "List the semantic capabilities Comms supports, optionally for one transport, "
            "with their current state. Advisory: the provider's answer to a mutation is final."
        ),
        input_schema={
            "type": "object",
            "properties": {"transport": _TRANSPORT},
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "capabilities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "capability": {"type": "string"},
                            "transport": _TRANSPORT,
                            "state": {"type": "string"},
                        },
                        "required": ["capability", "transport", "state"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["capabilities"],
            "additionalProperties": False,
        },
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=False,
        requires_request_id=False,
        failure_modes=("INVALID_ARGUMENT", "AUDIT_INTEGRITY_DEGRADED"),
        service="capability.list",
    ),
)


def _properties(schema: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(schema, Mapping):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, Mapping):
                found.update(value)
            found |= _properties(value)
    elif isinstance(schema, list):
        for item in schema:
            found |= _properties(item)
    return found


def _check(catalog: tuple[ToolSpec, ...]) -> None:
    """Fail closed at import: a malformed catalog never serves."""
    names = [spec.name for spec in catalog]
    services = [spec.service for spec in catalog]
    if len(set(names)) != len(names) or len(set(services)) != len(services):
        raise ValueError("catalog names and services must be unique")
    for spec in catalog:
        if not _NAME.fullmatch(spec.name) or not _SERVICE.fullmatch(spec.service):
            raise ValueError("catalog name or service is malformed")
        if spec.read_only and (spec.destructive or spec.requires_request_id):
            raise ValueError("a read-only tool is neither destructive nor a mutation")
        if _properties(spec.input_schema) & _RAW_PROPERTIES:
            raise ValueError("a tool takes a raw provider argument")
        Draft202012Validator.check_schema(dict(spec.input_schema))
        Draft202012Validator.check_schema(dict(spec.output_schema))


_check(TOOL_CATALOG)


def _entry(spec: ToolSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "title": spec.title,
        "description": spec.description,
        "inputSchema": dict(spec.input_schema),
        "outputSchema": dict(spec.output_schema),
        "annotations": {
            "readOnlyHint": spec.read_only,
            "destructiveHint": spec.destructive,
            "idempotentHint": spec.idempotent,
            "openWorldHint": spec.open_world,
        },
    }


def tools_list_payload() -> list[dict[str, Any]]:
    """The ``tools/list`` result, in catalog order."""
    return [_entry(spec) for spec in TOOL_CATALOG]


def _canonical(spec: ToolSpec) -> dict[str, Any]:
    return {
        **_entry(spec),
        "failure_modes": list(spec.failure_modes),
        "requires_request_id": spec.requires_request_id,
        "service": spec.service,
    }


def tool_schema_digest(spec: ToolSpec) -> str:
    """SHA-256 over the JCS of one tool's canonical entry."""
    return hashlib.sha256(jcs_dumps(_canonical(spec))).hexdigest()


def catalog_digest() -> str:
    """SHA-256 over the JCS of the whole ordered catalog."""
    return hashlib.sha256(jcs_dumps([_canonical(s) for s in TOOL_CATALOG])).hexdigest()
