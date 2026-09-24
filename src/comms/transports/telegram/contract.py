"""Duplicate-safe JSON, local $ref checks, output-schema composition and validators.

Phase 1: contracts are packaged data extracted offline from the supplied
spec. Validators have no network resolver.
"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from comms.core.strict_json import strict_json_loads  # the one copy; re-exported for callers

EXPECTED_TOOLS = (
    "telegram_status",
    "telegram_list_projects",
    "telegram_resolve_project",
    "telegram_list_chats",
    "telegram_resolve_peer",
    "telegram_get_messages",
    "telegram_get_context",
    "telegram_search_messages",
    "telegram_cross_project_search",
    "telegram_get_unread",
)


def assemble_output(data: dict, meta: dict, error: dict) -> dict:
    data, meta, error = deepcopy((data, meta, error))
    definitions: dict[str, Any] = {}
    for fragment in (data, meta, error):
        for name, definition in fragment.pop("$defs", {}).items():
            if name in definitions and definitions[name] != definition:
                raise ValueError("conflicting schema definition")
            definitions[name] = definition
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        # "type": "object" is implied by both oneOf branches and keeps the
        # legacy handshake-era wire Tool model (which requires outputSchema.type)
        # able to serve the same descriptors. Semantically equivalent to E.10.
        "type": "object",
        "$defs": definitions,
        "oneOf": [
            {
                "type": "object",
                "required": ["ok", "data", "meta"],
                "properties": {
                    "ok": {"const": True},
                    "data": data,
                    "meta": meta,
                },
                "additionalProperties": False,
            },
            error,
        ],
    }


@dataclass(frozen=True)
class ToolContract:
    name: str
    input_schema: dict
    output_schema: dict


_contracts_cache: dict[str, ToolContract] | None = None


def _resolve_pointer(root: dict, pointer: str) -> Any:
    # Local JSON Pointer only, e.g. #/$defs/message, with ~0/~1 decoding.
    if not pointer.startswith("#/"):
        raise ValueError(f"non-local $ref: {pointer}")
    node: Any = root
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"unresolved $ref: {pointer}")
        node = node[part]
    return node


def _check_refs(node: Any, root: dict) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                if not isinstance(value, str):
                    raise ValueError("invalid $ref")
                _resolve_pointer(root, value)
            else:
                _check_refs(value, root)
    elif isinstance(node, list):
        for item in node:
            _check_refs(item, root)


def _load_and_check(name: str, schema: dict, *, is_output: bool) -> dict:
    # Deep-copy so callers can never mutate the cached trusted schema.
    schema = deepcopy(schema)
    _check_refs(schema, schema)
    Draft202012Validator.check_schema(schema)
    return schema


def load_contracts() -> dict[str, ToolContract]:
    global _contracts_cache
    if _contracts_cache is not None:
        return deepcopy(_contracts_cache)
    from importlib import resources

    base = resources.files("comms.transports.telegram") / "contracts"
    manifest = strict_json_loads((base / "manifest.json").read_text(encoding="utf-8"))
    names = manifest["tools"] if isinstance(manifest, dict) and "tools" in manifest else manifest
    if isinstance(names, dict):
        names = list(names.keys())
    if set(names) != set(EXPECTED_TOOLS):
        raise ValueError("contract manifest tool set mismatch")
    contracts: dict[str, ToolContract] = {}
    for name in EXPECTED_TOOLS:
        input_schema = strict_json_loads((base / f"{name}.input.json").read_text(encoding="utf-8"))
        data_schema = strict_json_loads((base / f"{name}.data.json").read_text(encoding="utf-8"))
        meta_schema = strict_json_loads((base / "meta.json").read_text(encoding="utf-8"))
        error_schema = strict_json_loads((base / "error.json").read_text(encoding="utf-8"))
        output_schema = assemble_output(data_schema, meta_schema, error_schema)
        _load_and_check(name, input_schema, is_output=False)
        _load_and_check(name, output_schema, is_output=True)
        # Validate the input schema itself too.
        Draft202012Validator.check_schema(input_schema)
        contracts[name] = ToolContract(
            name=name,
            input_schema=deepcopy(input_schema),
            output_schema=deepcopy(output_schema),
        )
    _contracts_cache = contracts
    return deepcopy(contracts)


def validate_output(tool: str, value: dict) -> None:
    contracts = load_contracts()
    if tool not in contracts:
        raise ValueError("unknown tool")
    contract = contracts[tool]
    validator = Draft202012Validator(contract.output_schema, format_checker=FormatChecker())
    validator.validate(value)
