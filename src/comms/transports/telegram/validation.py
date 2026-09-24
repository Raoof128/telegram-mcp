"""Bounded input validation and defaults, without authority decisions."""

from copy import deepcopy
from datetime import datetime
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from comms.transports.telegram.contract import ToolContract


class ArgumentError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def parse_time(value: str) -> datetime:
    """The one parser for tool date-times: RFC 3339 with an offset, any case.

    RFC 3339 §5.6 allows a lowercase ``t`` and ``z`` and jsonschema accepts
    them, but ``datetime.fromisoformat`` does not, so the text is upper-cased
    first (digits, signs and separators are unaffected). Python cannot
    represent an RFC 3339 leap second; that yields INVALID_TIME by contract
    decision.
    """
    text = value.upper().replace("Z", "+00:00")
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
        if exc.validator == "format" and exc.validator_value == "date-time":
            raise ArgumentError("INVALID_TIME") from None
        raise ArgumentError("INVALID_ARGUMENT") from None
    value = deepcopy(arguments)
    if not isinstance(value, dict):
        raise ArgumentError("INVALID_ARGUMENT")
    for name, property_schema in contract.input_schema.get("properties", {}).items():
        if name not in value and "default" in property_schema:
            value[name] = deepcopy(property_schema["default"])
    if isinstance(value, dict) and "since" in value and "until" in value:
        since, until = value["since"], value["until"]
        if since is not None and until is not None and parse_time(since) >= parse_time(until):
            raise ArgumentError("INVALID_TIME")
    return value
