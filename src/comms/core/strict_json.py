"""The one strict JSON decoder: duplicate keys and non-finite numbers are refused.

It lived in the Telegram contract module until comms v0.3; it moved here so the admin
framing and the lease codec can use it without importing the retired tool contracts.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["strict_json_loads"]


def strict_json_loads(text: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> Any:
        raise ValueError("non-finite JSON number")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
