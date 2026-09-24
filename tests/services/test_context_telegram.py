"""comms v0.3 Task D10: Telegram context sources chosen by capability (P §20, §12)."""

from datetime import timedelta

import pytest

from comms.core.errors import CommsError
from comms.core.providers.capability import Capability as C
from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import CapabilitySnapshot, ProviderTarget
from comms.services.capability import CapabilityService
from comms.services.context import ContextEngine
from tests.core.campaign_helpers import NOW
from tests.services.context_fixtures import Clock, Source


class Provider:
    def __init__(self, states):
        self.states = states

    def snapshot(self, actor, destination):
        return CapabilitySnapshot(actor, destination.destination_ref, dict(self.states), "t")


def _engine(world, user_states, *, bot=True, user=True):
    sources = {}
    if user:
        sources["telegram_user"] = Source(provenance="telegram_live")
    if bot:
        sources["telegram_bot"] = Source(provenance="telegram_local")
    providers = {
        "telegram_user": Provider(user_states),
        "telegram_bot": Provider({C.HISTORY_READ: S.PROVIDER_UNSUPPORTED}),
    }
    capability = CapabilityService(providers, clock=lambda: NOW, max_age=timedelta(minutes=5))
    return ContextEngine(
        world["conn"], sources, clock=lambda: NOW, monotonic=Clock(), capability=capability
    ), sources


def _targets(world):
    grp, user = world["targets"][0]
    bot = ProviderTarget("telegram", "telegram_bot", user.destination_ref, user.identity)
    return grp, {"telegram_user": user, "telegram_bot": bot}


def test_live_preferred_when_user_can_read(world):
    engine, sources = _engine(world, {C.HISTORY_READ: S.AVAILABLE})
    grp, targets = _targets(world)
    page = engine.recent_for(grp, targets, limit=3)
    assert page["source"] == "telegram_live" and sources["telegram_bot"].queries == []


@pytest.mark.parametrize("state", [S.NOT_AUTHORIZED, S.UNKNOWN, S.NOT_CONFIGURED])
def test_bot_only_group_labelled_local(world, state):
    engine, sources = _engine(world, {C.HISTORY_READ: state})
    grp, targets = _targets(world)
    page = engine.recent_for(grp, targets, limit=3)
    assert page["source"] == "telegram_local" and all(
        i["source"] == "telegram_local" for i in page["items"]
    )
    assert sources["telegram_user"].queries == []  # the user is never asked when it cannot read
    bot_only = {"telegram_bot": targets["telegram_bot"]}
    assert engine.recent_for(grp, bot_only, limit=3)["source"] == "telegram_local"


def test_search_is_live_only(world):
    engine, _ = _engine(world, {C.HISTORY_SEARCH: S.NOT_AUTHORIZED})
    grp, targets = _targets(world)
    with pytest.raises(CommsError) as refused:
        engine.search_for([(grp, targets)], "hello")
    assert refused.value.code == "NOT_AUTHORIZED"  # never quietly downgraded to local updates
    engine, _ = _engine(world, {C.HISTORY_SEARCH: S.AVAILABLE})
    assert engine.search_for([(grp, targets)], "hello")["items"]


def test_no_readable_source_is_refused(world):
    engine, _ = _engine(world, {C.HISTORY_READ: S.NOT_AUTHORIZED}, bot=False)
    grp, targets = _targets(world)
    with pytest.raises(CommsError) as refused:
        engine.recent_for(grp, {"telegram_user": targets["telegram_user"]})
    assert refused.value.code == "NOT_AUTHORIZED"
