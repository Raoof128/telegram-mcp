"""comms v0.3 Task C33: the adapter registry built from secret-store credentials."""

import asyncio

import pytest

from comms.core.credentials import rotate_credential
from comms.core.keys.secrets import FileSecretStore
from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import ProviderTarget
from comms.runtime.adapters import AdapterSettings, build_adapters
from comms.transports.whatsapp.webhooks.ingress import WebhookIngress
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW
from tests.transports.telegram_bot.helpers import CANARY as BOT_TOKEN
from tests.transports.telegram_user.helpers import FakeSession
from tests.transports.whatsapp_cloud.helpers import CANARY as META_TOKEN

SETTINGS = AdapterSettings(meta_phone_number_id="106540352242922", meta_waba_id="102290129340398")


class Archive:
    def ingest(self, raw):
        return None


@pytest.fixture
def world(tmp_path):
    world = comms_world(tmp_path)
    world["secrets"] = FileSecretStore(tmp_path / "secrets")
    return world


def _configure(world, *purposes):
    values = {
        "telegram-bot-token": BOT_TOKEN.encode(),
        "meta-access-token": META_TOKEN.encode(),
        "meta-app-secret": b"fixture-app-secret-0123456789",
        "meta-webhook-secret": b"fixture-verify-token-0123456789",
    }
    for purpose in purposes:
        rotate_credential(
            world["writer"],
            world["secrets"],
            purpose,
            values[purpose],
            prove=lambda v: None,
            now=NOW,
        )


def _build(world, **kw):
    return build_adapters(
        world["conn"],
        world["secrets"],
        kw.pop("settings", SETTINGS),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        archive=Archive(),
        **kw,
    )


def _states(provider, actor, identity):
    return provider.snapshot(actor, ProviderTarget("x", actor, "dst_x", identity)).states


def test_missing_credentials_not_configured(world):
    adapters = _build(world)
    assert adapters.delivery == {} and adapters.admin == {} and adapters.context == {}
    assert adapters.webhook is None and adapters.listeners == {}
    bot = set(
        _states(adapters.capability["telegram_bot"], "telegram_bot", "-1000000000077").values()
    )
    assert bot == {S.NOT_CONFIGURED, S.PROVIDER_UNSUPPORTED}  # user-only ones stay unsupported (C8)
    assert set(
        _states(adapters.capability["whatsapp_cloud"], "whatsapp_cloud", "+61400000001").values()
    ) == {S.NOT_CONFIGURED}
    user = _states(adapters.capability["telegram_user"], "telegram_user", "-1000000000077")
    assert set(user.values()) == {S.NOT_CONFIGURED}


def test_configured_credentials_build_every_contract(world):
    _configure(
        world, "telegram-bot-token", "meta-access-token", "meta-app-secret", "meta-webhook-secret"
    )
    adapters = _build(world, telegram_session=FakeSession(), run=asyncio.run)
    assert set(adapters.delivery) == {"telegram", "whatsapp"}
    assert adapters.delivery["telegram"].actor == "telegram_bot"  # the configured preference
    assert set(adapters.capability) == {"telegram_bot", "telegram_user", "whatsapp_cloud"}
    assert set(adapters.admin) == {"telegram_bot", "telegram_user", "whatsapp_cloud"}
    assert set(adapters.context) == {"telegram_bot", "telegram_user"}
    user_first = _build(
        world,
        telegram_session=FakeSession(),
        run=asyncio.run,
        settings=AdapterSettings(telegram_delivery_actor="telegram_user"),
    )
    assert user_first.delivery["telegram"].actor == "telegram_user"


def test_single_telethon_client_shared_by_read_write_and_updates(world):
    session = FakeSession()
    adapters = _build(
        world,
        telegram_session=session,
        run=asyncio.run,
        settings=AdapterSettings(telegram_delivery_actor="telegram_user"),
    )
    holders = [
        adapters.delivery["telegram"]._session,
        adapters.capability["telegram_user"]._session,
        adapters.admin["telegram_user"]._session,
        adapters.context["telegram_user"]._session,
    ]
    assert all(holder is session for holder in holders)
    assert session.update_owner == "update-consumer" and adapters.updates is not None


def test_webhook_ingress_is_its_own_listener(world):
    _configure(world, "meta-app-secret", "meta-webhook-secret")
    adapters = _build(world)
    assert isinstance(adapters.webhook, WebhookIngress)
    assert adapters.listeners == {
        "webhook": adapters.webhook
    }  # its own listener, nothing else mounted
    assert adapters.inbox is not None and adapters.worker is not None


def test_a_half_configured_webhook_is_not_served(world):
    _configure(world, "meta-app-secret")
    assert _build(world).webhook is None


def test_credentials_never_reach_a_repr(world):
    _configure(
        world, "telegram-bot-token", "meta-access-token", "meta-app-secret", "meta-webhook-secret"
    )
    text = repr(_build(world, telegram_session=FakeSession(), run=asyncio.run))
    assert BOT_TOKEN not in text and META_TOKEN not in text and "fixture-app-secret" not in text


def test_the_bot_poller_exists_exactly_when_the_bot_token_is_configured(world):
    """D39-PRE E5: the poller fills bot_updates, which the bot's local context reads."""
    assert _build(world).poller is None
    _configure(world, "telegram-bot-token")
    assert _build(world).poller is not None


def test_the_webhook_is_not_served_without_an_archive(world):
    """D39-PRE: an event the inbox cannot archive is never accepted."""
    _configure(world, "meta-app-secret", "meta-webhook-secret")
    adapters = build_adapters(world["conn"], world["secrets"], SETTINGS, clock=lambda: NOW,
                              monotonic=lambda: 0.0, archive=None)  # fmt: skip
    assert (
        adapters.webhook is None and adapters.worker is None and "webhook" not in adapters.listeners
    )
