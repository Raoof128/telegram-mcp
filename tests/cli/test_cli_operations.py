"""comms v0.3 Task D30: the comms CLI — campaign, location, audience, group and message commands.

They call the same services as MCP, over the admin socket: a command becomes one ``tool call``
admin request, which the daemon runs through the one dispatcher.
"""

import ast
import json
from pathlib import Path

import pytest

from comms.cli import build_parser
from comms.cli_commands.tools import FAMILIES, command_request, tool_commands
from comms.mcp.catalog import TOOL_CATALOG
from comms.mcp.dispatch import AuthenticatedClient, Dispatcher
from comms.runtime.tool_calls import OWNER_CLIENT, tool_call_handler
from comms.services.registry import ServiceRegistry

ROOT = Path(__file__).resolve().parents[2]


def _recording():
    seen = []
    services = ServiceRegistry()
    for spec in TOOL_CATALOG:
        services.register(
            spec.service,
            lambda client, arguments, name=spec.service: (
                seen.append((name, client.client_ref, arguments))
                or {"campaign": "cmp_" + "c" * 26, "op_ref": "op_" + "e" * 26, "replayed": False}
            ),
        )
    return Dispatcher(services), seen


def test_every_family_tool_has_a_command():
    commands = tool_commands()
    for spec in TOOL_CATALOG:
        family = spec.name.removeprefix("comms_").split("_")[0]
        if family in FAMILIES:
            assert spec.name in {s.name for s in commands.values()}, spec.name


def test_cli_write_prints_request_and_operation_refs(capsys):
    dispatcher, seen = _recording()
    args = build_parser().parse_args(["campaign", "create", "--title", "Nowruz"])
    tool, arguments = command_request(args)
    assert tool == "comms_campaign_create" and arguments["title"] == "Nowruz"
    assert arguments["request_id"].startswith("req_")
    printed = tool_call_handler(dispatcher)({"tool": tool, "arguments": arguments})
    assert printed["request_id"] == arguments["request_id"]
    assert printed["op_ref"] == "op_" + "e" * 26 and printed["error"] is None
    assert seen[0][0] == "campaign.create"


def test_cli_and_mcp_reach_the_same_service():
    dispatcher, seen = _recording()
    args = build_parser().parse_args(["location", "create", "--name", "Parramatta"])
    tool, arguments = command_request(args)
    tool_call_handler(dispatcher)({"tool": tool, "arguments": arguments})
    mcp_client = AuthenticatedClient(client_ref="cli_" + "m" * 26, auth_kind="cml1")
    dispatcher.call(mcp_client, tool, {**arguments})
    (cli_name, cli_client, _a), (mcp_name, mcp_client_ref, _b) = seen
    assert cli_name == mcp_name == "location.create"
    assert cli_client == OWNER_CLIENT and mcp_client_ref == "cli_" + "m" * 26


def test_complex_arguments_are_json_and_reads_take_no_request_id():
    args = build_parser().parse_args(
        [
            "campaign",
            "set-targets",
            "--campaign",
            "cmp_" + "c" * 26,
            "--targets",
            '{"recipients": []}',
            "--transports",
            '["whatsapp"]',
        ]
    )
    tool, arguments = command_request(args)
    assert tool == "comms_campaign_set_targets" and arguments["targets"] == {"recipients": []}
    read = build_parser().parse_args(["group", "get", "--group", "grp_" + "g" * 26])
    assert "request_id" not in command_request(read)[1]


def test_a_refused_call_prints_its_code_not_a_trace():
    dispatcher, _seen = _recording()
    printed = tool_call_handler(dispatcher)(
        {"tool": "comms_group_get", "arguments": {"group": "nope"}}
    )
    assert printed["error"] == "INVALID_ARGUMENT" and printed["result"] is None


def test_the_handler_refuses_malformed_requests():
    dispatcher, _seen = _recording()
    for bad in (
        {},
        {"tool": 7, "arguments": {}},
        {"tool": "comms_group_get", "arguments": []},
        {"tool": "x", "arguments": {}, "extra": 1},
    ):
        with pytest.raises(ValueError):
            tool_call_handler(dispatcher)(bad)


def test_cli_never_writes_sqlite_directly():
    paths = [
        ROOT / "src" / "comms" / "cli.py",
        *(ROOT / "src" / "comms" / "cli_commands").rglob("*.py"),
    ]
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith(
                    (
                        "sqlite3",
                        "sqlcipher3",
                        "comms.core.storage",
                        "comms.services",
                        "comms.core.campaigns",
                    )
                ), (path, name)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert (
                    not node.value.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE "))
                ), path


def test_output_is_json_serialisable():
    dispatcher, _seen = _recording()
    args = build_parser().parse_args(["campaign", "create", "--title", "T"])
    tool, arguments = command_request(args)
    json.dumps(tool_call_handler(dispatcher)({"tool": tool, "arguments": arguments}))
