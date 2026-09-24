"""comms v0.3 Task D6: the capability service — refreshed snapshots, actor choice, provider
authority (P §9, §35, §68; A25)."""

from datetime import UTC, datetime, timedelta

import pytest

from comms.core.errors import CommsError
from comms.core.providers.capability import Capability as C
from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import CapabilitySnapshot, ProviderResult, ProviderTarget
from comms.services.capability import CapabilityService

NOW = datetime(2026, 9, 25, tzinfo=UTC)
BOT = ProviderTarget("telegram", "telegram_bot", "dst_g", "-1000000000077")
USER = ProviderTarget("telegram", "telegram_user", "dst_g", "-1000000000077")
TARGETS = {"telegram_bot": BOT, "telegram_user": USER}


class Provider:
    def __init__(self, actor, states):
        self.actor, self.states, self.calls = actor, dict(states), 0

    def snapshot(self, actor, destination):
        self.calls += 1
        return CapabilitySnapshot(actor, destination.destination_ref, dict(self.states), "t")


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


def _service(bot_states, user_states, clock=None):
    providers = {
        "telegram_bot": Provider("telegram_bot", bot_states),
        "telegram_user": Provider("telegram_user", user_states),
    }
    return CapabilityService(
        providers, clock=clock or Clock(), max_age=timedelta(minutes=5)
    ), providers


def test_snapshots_are_cached_until_stale(tmp_path):
    clock = Clock()
    service, providers = _service({C.MESSAGE_SEND: S.AVAILABLE}, {}, clock)
    service.snapshot("telegram_bot", BOT)
    service.snapshot("telegram_bot", BOT)
    assert providers["telegram_bot"].calls == 1
    clock.now += timedelta(minutes=6)
    service.snapshot("telegram_bot", BOT)
    assert providers["telegram_bot"].calls == 2


def test_refresh_before_write():
    service, providers = _service({C.MEMBER_BAN: S.AVAILABLE}, {})
    service.snapshot("telegram_bot", BOT)
    providers["telegram_bot"].states[C.MEMBER_BAN] = S.NOT_AUTHORIZED  # rights were taken away
    with pytest.raises(CommsError) as refused:
        service.require_for_write("telegram_bot", BOT, C.MEMBER_BAN)
    assert refused.value.code == "NOT_AUTHORIZED" and providers["telegram_bot"].calls == 2


def test_auto_actor_order():
    both = {C.MESSAGE_SEND: S.AVAILABLE, C.HISTORY_SEARCH: S.PROVIDER_UNSUPPORTED}
    service, _ = _service(both, {C.MESSAGE_SEND: S.AVAILABLE, C.HISTORY_SEARCH: S.AVAILABLE})
    assert (
        service.choose_actor(TARGETS, C.MESSAGE_SEND) == "telegram_bot"
    )  # 4: least extra authority
    assert (
        service.choose_actor(TARGETS, C.HISTORY_SEARCH) == "telegram_user"
    )  # 2: required capability
    assert (
        service.choose_actor(TARGETS, C.MESSAGE_SEND, preferred="telegram_user") == "telegram_user"
    )  # 3
    assert (
        service.choose_actor(TARGETS, C.MESSAGE_SEND, configured="telegram_user") == "telegram_user"
    )  # 1
    with pytest.raises(
        CommsError
    ) as refused:  # "send it as me" when the user cannot: refused, not swapped
        _service({C.MEMBER_BAN: S.AVAILABLE}, {C.MEMBER_BAN: S.NOT_AUTHORIZED})[0].choose_actor(
            TARGETS, C.MEMBER_BAN, preferred="telegram_user"
        )
    assert refused.value.code == "NOT_AUTHORIZED"
    with pytest.raises(CommsError) as refused:  # the destination is configured for the bot only
        service.choose_actor(TARGETS, C.HISTORY_SEARCH, configured="telegram_bot")
    assert refused.value.code == "PROVIDER_UNSUPPORTED"


def test_no_capable_actor_names_the_best_reason():
    service, _ = _service({C.MEMBER_BAN: S.NOT_CONFIGURED}, {C.MEMBER_BAN: S.NOT_AUTHORIZED})
    with pytest.raises(CommsError) as refused:
        service.choose_actor(TARGETS, C.MEMBER_BAN)
    assert refused.value.code == "NOT_AUTHORIZED"


def test_stale_snapshot_is_not_authority():
    service, providers = _service({C.MEMBER_BAN: S.AVAILABLE}, {})
    service.require_for_write("telegram_bot", BOT, C.MEMBER_BAN)
    verdict = service.authoritative("telegram_bot", BOT, ProviderResult("FAILED", "NOT_AUTHORIZED"))
    assert verdict.code == "NOT_AUTHORIZED"
    service.snapshot("telegram_bot", BOT)
    assert providers["telegram_bot"].calls == 2  # the refusal dropped the cached snapshot
    assert service.authoritative("telegram_bot", BOT, ProviderResult("SUCCEEDED", None)) is None


@pytest.mark.parametrize("state", [S.UNKNOWN, S.UNAVAILABLE, S.TEMPORARILY_UNAVAILABLE])
def test_unknown_never_available(state):
    service, _ = _service({C.MEMBER_BAN: state}, {C.MEMBER_BAN: state})
    with pytest.raises(CommsError) as refused:
        service.require_for_write("telegram_bot", BOT, C.MEMBER_BAN)
    assert refused.value.code == (
        "PROVIDER_UNAVAILABLE" if state is S.TEMPORARILY_UNAVAILABLE else "CAPABILITY_UNAVAILABLE"
    )
    with pytest.raises(CommsError):
        service.choose_actor(TARGETS, C.MEMBER_BAN)


def test_for_group_is_the_p35_shape():
    service, _ = _service(
        {C.MESSAGE_SEND: S.AVAILABLE}, {C.MESSAGE_SEND: S.AVAILABLE, C.HISTORY_SEARCH: S.AVAILABLE}
    )
    shape = service.for_group("grp_" + "a" * 26, TARGETS)
    assert shape == {
        "group_ref": "grp_" + "a" * 26,
        "actors": {
            "telegram_bot": {"message.send": "AVAILABLE"},
            "telegram_user": {"message.send": "AVAILABLE", "history.search": "AVAILABLE"},
        },
    }
