"""comms v0.3 Task C12: Bot API update polling with an atomic, persisted offset (A24)."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from comms.transports.telegram.bot.http import BotApi
from comms.transports.telegram.bot.updates import BotPoller, PollingRefused, set_update_mode, state
from tests.core import fakes
from tests.core import schema_fixtures as fx
from tests.transports.telegram_bot.helpers import Secrets, routed

NOW = datetime(2026, 9, 25, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


def _poller(conn, answer, seen=None, on_update=None):
    api = BotApi(Secrets(), version=1, transport=routed({"getUpdates": answer}, seen))
    return BotPoller(api, conn, clock=lambda: NOW, on_update=on_update)


def _stored(conn):
    return [
        tuple(r)
        for r in conn.execute("SELECT update_id, chat_id, kind FROM bot_updates ORDER BY update_id")
    ]


def test_updates_are_ingested_and_the_offset_advances(conn):
    seen = []
    report = _poller(conn, "getUpdates_two", seen).poll_once()
    assert (report.outcome, report.ingested) == ("ok", 2)
    assert _stored(conn) == [
        (500, -1001234567890, "message"),
        (501, -1001234567890, "chat_join_request"),
    ]
    assert state(conn) == ("polling", 502)
    body = json.loads(seen[0].content)
    assert body["offset"] == 0 and body["timeout"] > 0 and "message" in body["allowed_updates"]


def test_the_next_poll_asks_from_the_persisted_offset(conn):
    _poller(conn, "getUpdates_two").poll_once()
    seen = []
    _poller(conn, "getUpdates_empty", seen).poll_once()
    assert json.loads(seen[0].content)["offset"] == 502


def test_offset_persisted_with_ingest_atomically(conn):
    fakes.plant_failure(conn, "bot_updates", "INSERT")
    with pytest.raises(Exception, match="planted"):
        _poller(conn, "getUpdates_two").poll_once()
    assert _stored(conn) == [] and state(conn) == ("polling", 0)
    fakes.clear_planted(conn)
    calls = []
    _poller(conn, "getUpdates_two", on_update=lambda c, u: calls.append(u["update_id"])).poll_once()
    assert _stored(conn)[0][0] == 500 and state(conn) == ("polling", 502) and calls == [500, 501]


def test_a_failing_ingest_hook_rolls_back_its_update_and_the_offset(conn):
    def hook(c, update):
        if update["update_id"] == 501:
            raise RuntimeError("downstream refused")

    with pytest.raises(RuntimeError):
        _poller(conn, "getUpdates_two", on_update=hook).poll_once()
    assert [r[0] for r in _stored(conn)] == [500] and state(conn) == ("polling", 501)


def test_duplicate_update_harmless(conn):
    calls = []
    _poller(conn, "getUpdates_two", on_update=lambda c, u: calls.append(u["update_id"])).poll_once()
    report = _poller(
        conn, "getUpdates_two", on_update=lambda c, u: calls.append(u["update_id"])
    ).poll_once()
    assert report.ingested == 0 and report.duplicates == 2
    assert calls == [500, 501] and len(_stored(conn)) == 2 and state(conn) == ("polling", 502)


def test_the_offset_never_moves_backwards(conn):
    _poller(conn, "getUpdates_two").poll_once()
    with pytest.raises(Exception, match="offset"):
        conn.execute("UPDATE bot_update_offset SET next_offset = 1")
    conn.rollback()


def test_polling_refused_when_webhook_mode_configured(conn):
    set_update_mode(conn, "webhook", now=NOW)
    seen = []
    with pytest.raises(PollingRefused):
        _poller(conn, "getUpdates_two", seen).poll_once()
    assert seen == [] and _stored(conn) == []


def test_telegrams_own_webhook_conflict_refuses_polling(conn):
    with pytest.raises(PollingRefused):
        _poller(conn, "getUpdates_409_webhook_active").poll_once()
    assert state(conn) == ("polling", 0)


@pytest.mark.parametrize(
    "answer", [httpx.ReadTimeout, httpx.ConnectError, "sendMessage_500_envelope", "sendMessage_429"]
)
def test_an_unavailable_provider_changes_nothing(conn, answer):
    report = _poller(conn, answer).poll_once()
    assert (report.outcome, report.ingested) == ("unavailable", 0) and state(conn) == ("polling", 0)


def test_a_batch_stops_at_a_malformed_update(conn):
    body = {
        "ok": True,
        "result": [{"update_id": 700, "message": {"chat": {"id": 1}}}, {"no_id": True}],
    }

    def handler(request):
        return httpx.Response(200, json=body)

    api = BotApi(Secrets(), version=1, transport=httpx.MockTransport(handler))
    report = BotPoller(api, conn, clock=lambda: NOW).poll_once()
    assert (report.outcome, report.ingested) == ("malformed", 1) and state(conn) == ("polling", 701)


def test_switching_mode_is_explicit(conn):
    assert state(conn) == ("polling", 0)
    set_update_mode(conn, "webhook", now=NOW)
    set_update_mode(conn, "polling", now=NOW)
    assert state(conn) == ("polling", 0)
    with pytest.raises(ValueError):
        set_update_mode(conn, "both", now=NOW)
