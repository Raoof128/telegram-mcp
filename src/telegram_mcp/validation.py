"""Bounded input validation and defaults, without authority decisions."""

from copy import deepcopy
from datetime import datetime
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from telegram_mcp.contract import ToolContract


class ArgumentError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _parse_offset(value: str) -> datetime:
    # Already format-validated date-time with offset. Python cannot represent
    # an RFC 3339 leap second; that yields INVALID_TIME by contract decision.
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ArgumentError("INVALID_TIME") from None
    if parsed.tzinfo is None:
        raise ArgumentError("INVALID_TIME")
    return parsed


def validate_arguments(contract: ToolContract, arguments: object) -> dict[str, Any]:
    validator = Draft202012Validator(contract.input_schema, format_checker=FormatChecker())
    try:
        validator.validate(arguments)
    except ValidationError as exc:
        code = "INVALID_TIME" if exc.validator == "format" else "INVALID_ARGUMENT"
        raise ArgumentError(code) from None
    value = deepcopy(arguments)
    if not isinstance(value, dict):
        raise ArgumentError("INVALID_ARGUMENT")
    for name, property_schema in contract.input_schema.get("properties", {}).items():
        if name not in value and "default" in property_schema:
            value[name] = deepcopy(property_schema["default"])
    if isinstance(value, dict) and "since" in value and "until" in value:
        since, until = value["since"], value["until"]
        if since is not None and until is not None and _parse_offset(since) >= _parse_offset(until):
            raise ArgumentError("INVALID_TIME")
    return value
