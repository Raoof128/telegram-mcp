"""comms v0.3 Task A19: one TOOL_CATALOG, closed comms_* dispatch (A4, A29, A37; P §37–39)."""

import ast
import json
import random
import string
import subprocess
import sys
from pathlib import Path

import pytest

from comms.core.strict_json import strict_json_loads
from comms.mcp.catalog import (
    TOOL_CATALOG,
    ToolSpec,
    catalog_digest,
    tool_schema_digest,
    tools_list_payload,
)
from comms.mcp.dispatch import AuthenticatedClient, Dispatcher
from comms.services.registry import ServiceRegistry
from tests.core import schema_fixtures as fx

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs" / "comms-v0.3-supersession.json"
CLIENT = AuthenticatedClient(client_ref="cli_" + "a" * 26, auth_kind="cml1")
FORBIDDEN_PROPERTIES = {"method", "path", "endpoint", "rpc", "raw"}


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, client, arguments):
        self.calls.append((client, dict(arguments)))
        return {"capabilities": []}


@pytest.fixture
def world(tmp_path):
    conn = fx.migrated(tmp_path)
    recorder = Recorder()
    services = ServiceRegistry()
    for spec in TOOL_CATALOG:
        services.register(spec.service, recorder)
    return {"conn": conn, "tmp": tmp_path, "recorder": recorder, "dispatcher": Dispatcher(services)}


def _db_bytes(world):
    """The database and any WAL/SHM side files, byte for byte."""
    return {p.name: p.read_bytes() for p in sorted(world["tmp"].glob("comms.db*"))}


def _retired_names():
    tombstones = strict_json_loads(MANIFEST.read_text(encoding="utf-8"))["tombstoned_identifiers"]
    names = [n for n in tombstones if "/" not in n and n != "tgml1"]
    assert len(names) == 16
    return names


def test_part_a_catalog_is_exactly_the_seed_tool():
    assert [spec.name for spec in TOOL_CATALOG] == ["comms_capability_list"]
    assert all(isinstance(spec, ToolSpec) for spec in TOOL_CATALOG)


def test_every_tool_is_comms_prefixed_and_unique():
    names = [spec.name for spec in TOOL_CATALOG]
    assert all(n.startswith("comms_") for n in names)
    assert len(names) == len(set(names))
    services = [spec.service for spec in TOOL_CATALOG]
    assert len(services) == len(set(services))


def _assert_zero_effects(world, before, names):
    for name in names:
        result = world["dispatcher"].call(CLIENT, name, {})
        payload = result.to_mcp()
        assert payload["isError"] is True
        assert payload["structuredContent"]["error"]["code"] == "TOOL_NOT_FOUND"
        assert "TOOL_NOT_FOUND" not in payload["content"][0]["text"]  # the code is structured only
    world["conn"].commit()
    assert _db_bytes(world) == before
    assert world["recorder"].calls == []


def test_unknown_name_is_tool_not_found_with_zero_effects(world):
    before = _db_bytes(world)
    rng = random.Random(1603)
    alphabet = string.ascii_letters + string.digits + "_-./ "
    names = ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40))) for _ in range(97)]
    names += ["comms_", "comms_capability_list ", "Comms_capability_list"]
    _assert_zero_effects(world, before, names)


def test_each_retired_name_is_tool_not_found_with_zero_effects(world):
    before = _db_bytes(world)
    _assert_zero_effects(world, before, _retired_names())
    listed = {tool["name"] for tool in tools_list_payload()}
    assert listed.isdisjoint(_retired_names())


def test_a_known_tool_reaches_exactly_its_service(world):
    result = world["dispatcher"].call(CLIENT, "comms_capability_list", {})
    assert result.to_mcp()["isError"] is False
    assert result.to_mcp()["structuredContent"] == {"capabilities": []}
    assert world["recorder"].calls == [(CLIENT, {})]


def test_arguments_outside_the_schema_are_refused_before_the_service(world):
    for bad in ({"transport": "sms"}, {"unexpected": 1}, {"method": "messages.send"}):
        payload = world["dispatcher"].call(CLIENT, "comms_capability_list", bad).to_mcp()
        assert payload["structuredContent"]["error"]["code"] == "INVALID_ARGUMENT", bad
    assert world["recorder"].calls == []


def test_annotations_are_derived_from_read_only_and_destructive():
    by_name = {spec.name: spec for spec in TOOL_CATALOG}
    for tool in tools_list_payload():
        spec = by_name[tool["name"]]
        assert tool["annotations"] == {
            "readOnlyHint": spec.read_only,
            "destructiveHint": spec.destructive,
            "idempotentHint": spec.idempotent,
            "openWorldHint": spec.open_world,
        }
        assert not (spec.read_only and spec.destructive)
        assert tool["inputSchema"] == dict(spec.input_schema)
        assert tool["outputSchema"] == dict(spec.output_schema)


def test_catalog_digest_is_stable_in_process_and_subprocess():
    first = catalog_digest()
    assert first == catalog_digest() and len(first) == 64
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            "from comms.mcp.catalog import catalog_digest; print(catalog_digest())",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    assert done.stdout.strip() == first
    assert all(len(tool_schema_digest(spec)) == 64 for spec in TOOL_CATALOG)


def _properties(schema):
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                yield from value
            yield from _properties(value)
    elif isinstance(schema, list):
        for item in schema:
            yield from _properties(item)


def test_no_tool_takes_a_raw_method_or_path_argument():
    for spec in TOOL_CATALOG:
        assert not (set(_properties(spec.input_schema)) & FORBIDDEN_PROPERTIES), spec.name


def test_the_tools_list_payload_is_json_and_ordered():
    payload = tools_list_payload()
    assert json.loads(json.dumps(payload)) == payload
    assert [t["name"] for t in payload] == [s.name for s in TOOL_CATALOG]


def test_the_mcp_package_never_reaches_a_transport_or_storage():
    for path in (ROOT / "src" / "comms" / "mcp").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith(
                    (
                        "comms.transports",
                        "comms.core.storage",
                        "whatsvault",
                        "telegram_mcp",
                        "telethon",
                    )
                ), (path, name)
