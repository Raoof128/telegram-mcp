"""comms v0.3 Task C8: Bot API capability discovery from actual rights (P §9–12; A25)."""

from datetime import UTC, datetime

import httpx
import pytest

from comms.core import timeutil
from comms.core.keys.secrets import SecretStoreError
from comms.core.providers.capability import Capability as C
from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import ProviderTarget
from comms.core.providers.semantics import SUPPORT
from comms.transports.telegram.bot.capability import TELEGRAM_CAPABILITIES, BotCapability
from comms.transports.telegram.bot.http import BotApi
from tests.conformance.registry import REGISTRY
from tests.conformance.runner import run_suite
from tests.transports.telegram_bot.helpers import Secrets, routed

NOW = datetime(2026, 9, 25, tzinfo=UTC)
GROUP = ProviderTarget("telegram", "telegram_bot", "dst_" + "g" * 26, "-1001234567890")
USER_ONLY = [c for c in TELEGRAM_CAPABILITIES if "telegram_bot" not in SUPPORT[c]]


def _snapshot(member="getChatMember_admin_subset", chat="getChat_supergroup_forum", seen=None):
    routes = {"getMe": "getMe_ok", "getChat": chat, "getChatMember": member}
    api = BotApi(Secrets(), version=1, transport=routed(routes, seen))
    return BotCapability.from_api(api, clock=lambda: NOW).snapshot("telegram_bot", GROUP).states


def test_the_snapshot_covers_exactly_the_telegram_capabilities():
    assert set(_snapshot()) == set(TELEGRAM_CAPABILITIES)
    assert C.MESSAGE_SEND_TEMPLATE not in TELEGRAM_CAPABILITIES


def test_admin_with_a_subset_of_rights():
    states = _snapshot()
    for cap in (C.MESSAGE_SEND, C.MESSAGE_DELETE, C.MEMBER_BAN, C.MEMBER_REMOVE, C.INVITE_CREATE):
        assert states[cap] is S.AVAILABLE, cap
    for cap in (C.ADMIN_PROMOTE, C.CHAT_SET_TITLE, C.MESSAGE_PIN):
        assert states[cap] is S.NOT_AUTHORIZED, cap
    assert states[C.TOPIC_CREATE] is S.AVAILABLE  # a forum, and can_manage_topics


def test_topics_are_unavailable_outside_a_forum():
    states = _snapshot(chat="getChat_supergroup_muted", member="getChatMember_creator")
    assert states[C.TOPIC_CREATE] is S.UNAVAILABLE


def test_creator_holds_every_bot_right():
    states = _snapshot(member="getChatMember_creator")
    assert all(states[c] is S.AVAILABLE for c in TELEGRAM_CAPABILITIES if c not in USER_ONLY)


def test_plain_member():
    states = _snapshot(member="getChatMember_member")
    assert states[C.MESSAGE_SEND] is S.AVAILABLE and states[C.MEMBER_GET] is S.AVAILABLE
    assert states[C.MEMBER_BAN] is S.NOT_AUTHORIZED and states[C.MESSAGE_DELETE] is S.NOT_AUTHORIZED


def test_member_in_a_chat_that_forbids_sending():
    assert (
        _snapshot(member="getChatMember_member", chat="getChat_supergroup_muted")[C.MESSAGE_SEND]
        is S.NOT_AUTHORIZED
    )


def test_restricted_without_send():
    assert _snapshot(member="getChatMember_restricted_no_send")[C.MESSAGE_SEND] is S.NOT_AUTHORIZED


@pytest.mark.parametrize("member", ["getChatMember_left"])
def test_left(member):
    states = _snapshot(member=member)
    assert all(states[c] is S.NOT_AUTHORIZED for c in TELEGRAM_CAPABILITIES if c not in USER_ONLY)


def test_bot_never_claims_history_search():
    for member in ("getChatMember_creator", "getChatMember_admin_subset"):
        states = _snapshot(member=member)
        for cap in (
            C.HISTORY_SEARCH,
            C.HISTORY_READ,
            C.MEMBER_LIST,
            C.GROUP_DELETE,
            C.ADMIN_LOG_READ,
        ):
            assert states[cap] is S.PROVIDER_UNSUPPORTED, cap


def test_a_private_chat_offers_messages_only():
    target = ProviderTarget("telegram", "telegram_bot", "dst_" + "p" * 26, "42")
    routes = {
        "getMe": "getMe_ok",
        "getChat": "getChat_private",
        "getChatMember": "getChatMember_member",
    }
    api = BotApi(Secrets(), version=1, transport=routed(routes))
    states = BotCapability.from_api(api, clock=lambda: NOW).snapshot("telegram_bot", target).states
    assert states[C.MESSAGE_SEND] is S.AVAILABLE
    assert states[C.MEMBER_BAN] is S.UNAVAILABLE and states[C.INVITE_CREATE] is S.UNAVAILABLE


def test_not_configured_without_token():
    class Empty:
        def get(self, item, version):
            raise SecretStoreError("no such secret")

    for secrets in (Empty(), Secrets(token="not-a-token")):
        provider = BotCapability.from_secrets(secrets, version=1, clock=lambda: NOW)
        states = provider.snapshot("telegram_bot", GROUP).states
        assert {states[c] for c in TELEGRAM_CAPABILITIES if c not in USER_ONLY} == {
            S.NOT_CONFIGURED
        }
        assert all(states[c] is S.PROVIDER_UNSUPPORTED for c in USER_ONLY)


@pytest.mark.parametrize(
    "chat,expected",
    [
        ("getChat_403_kicked", S.NOT_AUTHORIZED),
        ("sendMessage_429", S.TEMPORARILY_UNAVAILABLE),
        ("sendMessage_500_envelope", S.UNKNOWN),
        (httpx.ReadTimeout, S.UNKNOWN),
        (httpx.ConnectError, S.TEMPORARILY_UNAVAILABLE),
    ],
)
def test_a_failed_lookup_is_never_available(chat, expected):
    states = _snapshot(chat=chat)
    assert {states[c] for c in TELEGRAM_CAPABILITIES if c not in USER_ONLY} == {expected}
    assert all(states[c] is S.PROVIDER_UNSUPPORTED for c in USER_ONLY)


def test_the_snapshot_names_actor_destination_and_time():
    routes = {
        "getMe": "getMe_ok",
        "getChat": "getChat_supergroup_forum",
        "getChatMember": "getChatMember_member",
    }
    seen = []
    api = BotApi(Secrets(), version=1, transport=routed(routes, seen))
    provider = BotCapability.from_api(api, clock=lambda: NOW)
    snap = provider.snapshot("telegram_bot", GROUP)
    assert (snap.actor, snap.destination_ref, snap.observed_at) == (
        "telegram_bot",
        GROUP.destination_ref,
        timeutil.iso(NOW),
    )
    provider.snapshot("telegram_bot", GROUP)
    assert [r.url.path.rsplit("/", 1)[1] for r in seen].count("getMe") == 1  # the bot id is cached
    with pytest.raises(ValueError):
        provider.snapshot("telegram_user", GROUP)


def test_the_conformance_capability_contract_passes():
    report = run_suite(
        REGISTRY.subset("telegram_bot", "capability"), {"telegram_bot": frozenset({"capability"})}
    )
    assert report.ok and report.passed == 2, report.failures
