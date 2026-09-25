"""comms v0.3 Task D38: P §81 — ambiguity is refused deterministically, and nothing mutates.

The three P §81 fixtures (two people named Ali, two MQ groups, several matching messages) each
resolve to ``AMBIGUOUS_TARGET`` and leave no mutation record. And a name can never stand in for
a target: every write tool's target arguments are ref patterns, so the dispatcher refuses
``"Ali"`` or ``"MQ"`` before any service runs.
"""

import pytest

from comms.core.campaigns import directory as d
from comms.core.errors import CommsError
from comms.mcp.catalog import TOOL_CATALOG
from comms.mcp.dispatch import AuthenticatedClient, Dispatcher
from comms.services.registry import ServiceRegistry
from comms.services.resolve import resolve_group, resolve_message, resolve_person
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW

CLIENT = AuthenticatedClient(client_ref="cli_" + "e" * 26, auth_kind="cml1")
TARGETS = ("group", "recipient", "message", "member", "to_group", "conversation", "audience",
           "location", "campaign", "invite", "topic", "template", "media", "job")  # fmt: skip


@pytest.fixture
def conn(tmp_path):
    conn = fx.migrated(tmp_path)
    for name in ("Ali Rezaei", "Ali Karimi"):
        d.add_recipient(conn, now=NOW, display_name=name)
    for location, name, identity in (("MQ", "MQ Persian Society", "group:55"),
                                     ("MQ Campus", "MQ Nowruz 2026", "channel:1234567890")):  # fmt: skip
        loc = d.add_location(conn, location, now=NOW)
        d.add_destination(conn, loc, "telegram", identity, name, normalize=fx.tg, now=NOW)
    return conn


def _mutations(conn):
    return conn.execute("SELECT count(*) FROM mutations").fetchone()[0]


MESSAGES = [
    {
        "message_ref": "cmg_" + c * 26,
        "sent_at": f"2026-09-24T1{i}:00:00Z",
        "untrusted": {"text": "Friday"},
    }
    for i, c in enumerate("ab")
]


@pytest.mark.parametrize(
    "fixture",
    [
        pytest.param(lambda conn: resolve_person(conn, "Ali"), id="two people named Ali"),
        pytest.param(lambda conn: resolve_group(conn, "MQ", now=NOW), id="two MQ groups"),
        pytest.param(lambda conn: resolve_message(MESSAGES), id="multiple matching messages"),
    ],
)
def test_each_p81_fixture_is_ambiguous_and_nothing_mutates(conn, fixture):
    before = _mutations(conn)
    with pytest.raises(CommsError) as refused:
        fixture(conn)
    assert refused.value.code == "AMBIGUOUS_TARGET"
    assert _mutations(conn) == before == 0


def _recording():
    seen = []
    services = ServiceRegistry()
    for spec in TOOL_CATALOG:
        services.register(
            spec.service, lambda client, arguments, name=spec.service: seen.append(name) or {}
        )
    return Dispatcher(services), seen


def test_every_write_target_is_a_ref_pattern():
    for spec in TOOL_CATALOG:
        if spec.requires_request_id:
            for name, schema in spec.input_schema["properties"].items():
                if name in TARGETS:
                    assert schema.get("pattern", "").startswith("^(?:"), (spec.name, name)


@pytest.mark.parametrize("name", ["Ali", "MQ", "the Friday message"])
def test_a_name_never_reaches_a_write_service(name):
    dispatcher, seen = _recording()
    for spec in TOOL_CATALOG:
        properties = spec.input_schema["properties"]
        target = next((k for k in TARGETS if k in properties), None)
        if not spec.requires_request_id or target is None:
            continue
        result = dispatcher.call(CLIENT, spec.name, {target: name, "request_id": "req_" + "a" * 26})
        assert result.error_code == "INVALID_ARGUMENT", spec.name
    assert seen == []
