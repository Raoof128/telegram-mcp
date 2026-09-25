"""comms v0.3 C23 (found defect): a real campaign freezes through each real transport."""

import asyncio
import json

import pytest

from comms.core.delivery import freeze
from comms.transports.telegram.bot.delivery import BotDelivery
from comms.transports.telegram.bot.http import BotApi
from comms.transports.telegram.user.delivery import UserDelivery
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW, person, ready
from tests.transports.telegram_bot.helpers import Secrets, fixture_transport
from tests.transports.telegram_user.helpers import FakeSession


@pytest.mark.parametrize(
    "transport",
    [
        BotDelivery(BotApi(Secrets(), version=1, transport=fixture_transport("sendMessage_ok"))),
        UserDelivery(FakeSession(), run=asyncio.run),
    ],
)
def test_a_real_campaign_is_carried_not_skipped(tmp_path, transport):
    conn = fx.migrated(tmp_path)
    rcp, _ = person(conn, tg="user:42")
    cmp = ready(conn, {"recipients": [rcp]}, frozenset({"telegram"}), body="Happy Nowruz")
    freeze.send(conn, cmp, {"telegram": transport}, now=NOW)
    state, payload = conn.execute("SELECT state, payload FROM delivery_jobs").fetchone()
    assert state == "PENDING" and json.loads(payload) == {"chat_id": 42, "text": "Happy Nowruz"}
