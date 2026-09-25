"""comms v0.3 Task D18: the context tools (P §19–22, §71)."""

from datetime import timedelta

import pytest

from comms.core.campaigns import directory as d
from comms.core.groups import group_ref
from comms.core.providers.capability import Capability as C
from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import CapabilitySnapshot, ProviderTarget
from comms.mcp.tools.context import CONTEXT_TOOLS
from comms.services.capability import CapabilityService
from comms.services.context import ContextEngine
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW
from tests.mcp import family
from tests.services.context_fixtures import Clock, Source

TOKEN = "cur_" + "a" * 26 + "." + "0" * 32
BY_NAME = {spec.name: spec for spec in CONTEXT_TOOLS}


class Provider:
    def snapshot(self, actor, target):
        return CapabilitySnapshot(actor, target.destination_ref, dict.fromkeys(C, S.AVAILABLE), "t")


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    """Each tool's result, produced by the real context engine (cursors as cur_ tokens)."""
    conn = fx.migrated(tmp_path_factory.mktemp("ctx"))
    loc = d.add_location(conn, "L", now=NOW)
    grp_targets = []
    for n in (1, 2):
        dst = d.add_destination(
            conn, loc, "telegram", f"group:{n}0", f"G{n}", normalize=fx.tg, now=NOW
        )
        grp_targets.append(
            (
                group_ref(conn, dst, now=NOW),
                ProviderTarget("telegram", "telegram_user", dst, f"-{n}0"),
            )
        )
    (grp, user), (grp2, user2) = grp_targets
    capability = CapabilityService(
        {"telegram_user": Provider()}, clock=lambda: NOW, max_age=timedelta(minutes=5)
    )
    engine = ContextEngine(
        conn,
        {"telegram_user": Source()},
        clock=lambda: NOW,
        monotonic=Clock(),
        capability=capability,
    )

    def tokened(page):
        return {**page, "next_cursor": TOKEN if page["next_cursor"] else None}

    recent = tokened(engine.recent(grp, user, limit=3))
    message = recent["items"][0]["message_ref"]
    got = engine.get(grp, user, include=("messages", "members", "admins"), message_limit=3)
    got = {k: tokened(v) if isinstance(v, dict) else v for k, v in got.items()}
    return {
        "examples": {
            "comms_context_get": {"group": grp, "include": ["messages", "members", "admins"]},
            "comms_context_recent": {"group": grp, "limit": 3},
            "comms_context_around_message": {"group": grp, "message": message, "before": 1},
            "comms_context_thread": {"group": grp, "message": message},
            "comms_context_search": {"groups": [grp, grp2], "query": "hello", "limit": 5},
            "comms_context_summarize_source": {"group": grp},
            "comms_context_page": {"cursor": TOKEN},
        },
        "results": {
            "comms_context_get": got,
            "comms_context_recent": recent,
            "comms_context_around_message": tokened(
                engine.around_message(grp, user, 998, before=1, after=1)
            ),
            "comms_context_thread": tokened(engine.thread(grp, user, 998, limit=3)),
            "comms_context_search": engine.search([(grp, user), (grp2, user2)], "hello", limit=5),
            "comms_context_summarize_source": engine.summarize_source(grp, {"telegram_user": user}),
            "comms_context_page": recent,
        },
    }


NAMES = sorted(BY_NAME)


def test_the_family_is_p22_plus_page():
    assert NAMES == sorted(
        f"comms_context_{t}"
        for t in ("get", "recent", "around_message", "thread", "search", "summarize_source", "page")
    )


@pytest.mark.parametrize("name", NAMES)
def test_schema_valid_json_schema_2020_12(name):
    family.schema_valid(BY_NAME[name])


@pytest.mark.parametrize("name", NAMES)
def test_output_schema_matches_service_result(name, results):
    family.output_matches(BY_NAME[name], results["results"][name])


@pytest.mark.parametrize("name", NAMES)
def test_annotations(name):
    spec = BY_NAME[name]
    family.annotations(spec)
    assert spec.read_only  # every context tool is a read


@pytest.mark.parametrize("name", NAMES)
def test_dispatch_reaches_its_service(name, results):
    family.dispatch_reaches_its_service(
        BY_NAME[name], results["examples"][name], results["results"][name]
    )


def test_an_item_carrying_a_provider_identity_fails_the_schema(results):
    page = results["results"]["comms_context_recent"]
    leaked = {**page, "items": [{**page["items"][0], "chat_id": "-10"}]}
    with pytest.raises(AssertionError):
        family.output_matches(BY_NAME["comms_context_recent"], leaked)


def test_a_raw_provider_cursor_fails_the_schema(results):
    page = {**results["results"]["comms_context_recent"], "next_cursor": "997"}
    with pytest.raises(AssertionError):
        family.output_matches(BY_NAME["comms_context_recent"], page)
