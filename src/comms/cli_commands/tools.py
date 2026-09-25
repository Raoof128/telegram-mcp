"""CLI commands generated from the catalog (comms v0.3 Task D30).

``comms <family> <verb> --<arg> …`` for the campaign, location, audience, group and message
families: one command per catalog tool, one flag per input property (objects and arrays as
JSON). A write gets a fresh ``req_`` id here, printed with the result's ``op_`` ref. The
command becomes one ``tool call`` admin request; the daemon runs it through the same
dispatcher MCP uses, so the CLI never touches storage or a service itself.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from typing import Any

from comms.core import refs
from comms.mcp.catalog import TOOL_CATALOG
from comms.mcp.spec import ToolSpec

__all__ = ["FAMILIES", "add_tool_parsers", "command_request", "tool_commands"]

FAMILIES = ("campaign", "location", "audience", "group", "message")


def _split(spec: ToolSpec) -> tuple[str, str]:
    family, _sep, verb = spec.name.removeprefix("comms_").partition("_")
    return family, verb.replace("_", "-")


def tool_commands() -> dict[tuple[str, str], ToolSpec]:
    return {_split(spec): spec for spec in TOOL_CATALOG if _split(spec)[0] in FAMILIES}


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise argparse.ArgumentTypeError("expected JSON") from None


def _flag_type(schema: Mapping[str, Any]) -> Any:
    kind = schema.get("type")
    if kind == "integer":
        return int
    if kind in ("object", "array"):
        return _json
    return str


def add_tool_parsers(sub: Any) -> None:
    families: dict[str, Any] = {}
    for (family, verb), spec in tool_commands().items():
        if family not in families:
            parser = sub.add_parser(family, help=f"{family} commands", allow_abbrev=False)
            families[family] = parser.add_subparsers(dest="tool_verb", required=True)
        command = families[family].add_parser(verb, help=spec.title, allow_abbrev=False)
        command.set_defaults(tool=spec.name)
        required = set(spec.input_schema.get("required", ()))
        for name, schema in spec.input_schema.get("properties", {}).items():
            if name == "request_id":
                continue  # the CLI mints it
            flag = "--" + name.replace("_", "-")
            if schema.get("type") == "boolean":
                command.add_argument(flag, dest=name, action=argparse.BooleanOptionalAction)
            else:
                command.add_argument(
                    flag, dest=name, type=_flag_type(schema), required=name in required
                )


def command_request(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    """``(tool, arguments)`` for a parsed command; a write gets a fresh request id."""
    spec = next(s for s in TOOL_CATALOG if s.name == args.tool)
    arguments = {
        name: getattr(args, name)
        for name in spec.input_schema.get("properties", {})
        if name != "request_id" and getattr(args, name, None) is not None
    }
    if spec.requires_request_id:
        arguments["request_id"] = refs.mint("request")
    return spec.name, arguments
