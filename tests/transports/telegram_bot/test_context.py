"""comms v0.3 Task C13: the Bot API context source (P §19–21; A32)."""

from datetime import UTC, datetime

import pytest

from comms.core import timeutil
from comms.core.providers.protocols import (
    ADAPTER_CONTRACTS,
    ContextQuery,
    ContextRefused,
    ProviderTarget,
)
from comms.transports.telegram.bot.context import BotContext
from comms.transports.telegram.bot.http import BotApi
from comms.transports.telegram.bot.updates import BotPoller
from tests.conformance.registry import REGISTRY
from tests.conformance.runner import Registry, run_suite
from tests.core import schema_fixtures as fx
from tests.transports.telegram_bot.helpers import Secrets, routed

NOW = datetime(2026, 9, 25, tzinfo=UTC)
GROUP = ProviderTarget("telegram", "telegram_bot", "dst_" + "g" * 26, "-1001234567890")
LIVE = {
    "getChat": "getChat_supergroup_forum",
    "getChatAdministrators": "getChatAdministrators_ok",
    "getChatMemberCount": "getChatMemberCount_ok",
}


@pytest.fixture
def conn(tmp_path):
    conn = fx.migrated(tmp_path)
    api = BotApi(Secrets(), version=1, transport=routed({"getUpdates": "getUpdates_two"}))
    BotPoller(api, conn, clock=lambda: NOW).poll_once()
    return conn


def _context(conn, routes=LIVE, seen=None):
    return BotContext(
        BotApi(Secrets(), version=1, transport=routed(routes, seen)), conn, clock=lambda: NOW
    )


def test_provenance_labels(conn):
    live = _context(conn).read(ContextQuery(GROUP, "info"))
    local = _context(conn).read(ContextQuery(GROUP, "recent"))
    assert live.provenance == "telegram_live" and local.provenance == "telegram_local"
    for page in (live, local):
        assert page.items and all(i["source"] == page.provenance for i in page.items)
        assert all(i["observed_at"] for i in page.items)


def test_live_info_is_chat_admins_and_member_count(conn):
    items = _context(conn).read(ContextQuery(GROUP, "info")).items
    chat = next(i for i in items if i["item"] == "chat")
    assert (chat["type"], chat["is_forum"], chat["member_count"]) == ("supergroup", True, 128)
    assert chat["untrusted"] == {"title": "Fixture forum"}  # provider text is never trusted
    admins = [(i["user_id"], i["status"]) for i in items if i["item"] == "admin"]
    assert admins == [(42, "creator"), (7000000001, "administrator")]
    assert all(i["observed_at"] == timeutil.iso(NOW) for i in items)


def test_recent_is_the_locally_retained_updates_newest_first(conn):
    items = _context(conn).read(ContextQuery(GROUP, "recent")).items
    assert [(i["update_id"], i["kind"]) for i in items] == [
        (501, "chat_join_request"),
        (500, "message"),
    ]
    message = items[1]
    assert (message["message_id"], message["from_id"]) == (10, 42)
    assert message["untrusted"] == {"text": "hello"}
    assert message["observed_at"]  # when this installation received it


def test_recent_pages_by_update_id(conn):
    first = _context(conn).read(ContextQuery(GROUP, "recent", {"limit": 1}))
    assert [i["update_id"] for i in first.items] == [501] and first.next_cursor == "501"
    second = _context(conn).read(
        ContextQuery(GROUP, "recent", {"limit": 1, "cursor": first.next_cursor})
    )
    assert [i["update_id"] for i in second.items] == [500] and second.next_cursor is None


def test_recent_reads_only_this_chat(conn):
    other = ProviderTarget("telegram", "telegram_bot", "dst_o", "-1009")
    assert _context(conn).read(ContextQuery(other, "recent")).items == ()


def test_bot_history_is_local_only(conn):
    seen = []
    for kind in ("history", "search", "around", "members"):
        with pytest.raises(ContextRefused) as refused:
            _context(conn, seen=seen).read(ContextQuery(GROUP, kind, {"query": "x"}))
        assert refused.value.code == "PROVIDER_UNSUPPORTED"
    assert seen == []  # the bot never asks Telegram for history it cannot have


@pytest.mark.parametrize(
    "routes,code",
    [
        ({**LIVE, "getChat": "getChat_403_kicked"}, "NOT_AUTHORIZED"),
        ({**LIVE, "getChatMemberCount": "sendMessage_500_envelope"}, "UNAVAILABLE"),
    ],
)
def test_a_failed_live_lookup_is_refused_not_partial(conn, routes, code):
    with pytest.raises(ContextRefused) as refused:
        _context(conn, routes).read(ContextQuery(GROUP, "info"))
    assert refused.value.code == code


@pytest.mark.parametrize(
    "args", [{"limit": 0}, {"limit": 101}, {"cursor": "x"}, {"limit": 5, "extra": 1}]
)
def test_malformed_arguments_are_refused(conn, args):
    with pytest.raises(ValueError):
        _context(conn).read(ContextQuery(GROUP, "recent", args))


def test_the_whole_telegram_bot_conformance_suite_passes():
    contracts = {"telegram_bot": ADAPTER_CONTRACTS["telegram_bot"]}
    registry = Registry({k: v for k, v in REGISTRY.cases.items() if k[0] == "telegram_bot"})
    report = run_suite(registry, contracts)
    assert report.ok and report.skipped == {}, report.failures
