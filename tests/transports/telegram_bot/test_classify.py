"""comms v0.3 Task C6: the Bot API client and named-case outcome classification (A19, A26).

Envelopes are the fixtures under tests/fixtures/providers/telegram_bot (provenance-pinned).
"""

import json
import logging

import httpx
import pytest

from comms.core.delivery.transport import ResultKind
from comms.transports.telegram.bot.classify import PERMANENT_DESCRIPTIONS, classify_send
from comms.transports.telegram.bot.http import (
    BOT_METHODS,
    BotApi,
    BotRefused,
    BotResponse,
    BotTransportError,
)
from tests.transports.telegram_bot.helpers import CANARY, Secrets, fixture_transport, raising


def _api(transport, token=CANARY):
    return BotApi(Secrets(token), version=1, transport=transport)


def _classify(name):
    return classify_send(
        _api(fixture_transport(name)).call("sendMessage", {"chat_id": 1, "text": "hi"})
    )


def _outcome(api):
    try:
        return api.call("sendMessage", {"chat_id": 1, "text": "hi"})
    except BotTransportError as exc:
        return exc


# ---- the A19 table, one row each -------------------------------------------------------------


def test_ok_true_is_accepted_with_the_message_id_as_ref():
    result = _classify("sendMessage_ok")
    assert (result.kind, result.provider_message_ref) == (ResultKind.ACCEPTED, "4711")


def test_429_with_retry_after_is_failed_transient():
    result = _classify("sendMessage_429")
    assert (result.kind, result.retry_after) == (ResultKind.FAILED_TRANSIENT, 17)


def test_429_without_retry_after_is_outcome_unknown():
    assert _classify("sendMessage_429_no_retry_after").kind is ResultKind.OUTCOME_UNKNOWN


@pytest.mark.parametrize("name", ["sendMessage_403_blocked", "sendMessage_400_chat_not_found"])
def test_documented_permanent_description_is_failed_permanent(name):
    assert _classify(name).kind is ResultKind.FAILED_PERMANENT


@pytest.mark.parametrize("name", ["sendMessage_400_undocumented", "sendMessage_401"])
def test_any_other_error_code_or_description_is_outcome_unknown(name):
    assert _classify(name).kind is ResultKind.OUTCOME_UNKNOWN


@pytest.mark.parametrize(
    "name",
    [
        "sendMessage_502",
        "sendMessage_500_envelope",
        "sendMessage_malformed",
        "sendMessage_no_ok_field",
    ],
)
def test_5xx_and_malformed_are_outcome_unknown(name):
    assert _classify(name).kind is ResultKind.OUTCOME_UNKNOWN


@pytest.mark.parametrize("exc_type", [httpx.ConnectError, httpx.ConnectTimeout])
def test_connect_failure_before_any_byte_is_failed_transient(exc_type):
    assert classify_send(_outcome(_api(raising(exc_type)))).kind is ResultKind.FAILED_TRANSIENT


@pytest.mark.parametrize(
    "exc_type",
    [
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.ReadError,
        httpx.WriteError,
        httpx.RemoteProtocolError,
    ],
)
def test_timeouts_and_resets_after_connecting_are_outcome_unknown(exc_type):
    assert classify_send(_outcome(_api(raising(exc_type)))).kind is ResultKind.OUTCOME_UNKNOWN


def test_the_permanent_table_is_closed_and_exact():
    assert "Forbidden: bot was blocked by the user" in PERMANENT_DESCRIPTIONS
    assert all(d.startswith(("Bad Request: ", "Forbidden: ")) for d in PERMANENT_DESCRIPTIONS)
    assert isinstance(PERMANENT_DESCRIPTIONS, frozenset)


# ---- the client ------------------------------------------------------------------------------


def test_the_request_is_a_post_to_the_pinned_origin_with_the_token_in_the_path():
    seen = []
    _api(fixture_transport("sendMessage_ok", seen)).call(
        "sendMessage", {"chat_id": 1, "text": "hi"}
    )
    (request,) = seen
    assert (request.method, request.url.host, request.url.scheme) == (
        "POST",
        "api.telegram.org",
        "https",
    )
    assert request.url.path == f"/bot{CANARY}/sendMessage"
    assert json.loads(request.content) == {"chat_id": 1, "text": "hi"}


def test_method_not_in_closed_set_refused():
    seen = []
    api = _api(fixture_transport("sendMessage_ok", seen))
    for method in ("logOut", "close", "sendMessage/../logOut", "setWebhook?x=1", ""):
        with pytest.raises(BotRefused):
            api.call(method, {})
    assert seen == [] and "logOut" not in BOT_METHODS and "sendMessage" in BOT_METHODS


@pytest.mark.parametrize("token", ["", "not-a-token", "1:a/b", "1:a?b", "1:a b", "x:abc"])
def test_a_malformed_token_is_refused_before_any_request(token):
    with pytest.raises(BotRefused):
        _api(fixture_transport("sendMessage_ok"), token=token)


def test_token_never_logged_or_in_errors(caplog):
    caplog.set_level(logging.DEBUG)
    api = _api(fixture_transport("sendMessage_ok"))
    response = api.call("sendMessage", {"chat_id": 1, "text": "hi"})
    texts = [repr(api), repr(response), str(response)]
    for exc_type in (httpx.ConnectError, httpx.ReadTimeout):
        exc = _outcome(_api(raising(exc_type)))
        assert isinstance(exc, BotTransportError)
        assert exc.__context__ is None and exc.__cause__ is None  # the httpx error held the URL
        texts += [str(exc), repr(exc), repr(exc.args)]
    texts += [r.getMessage() for r in caplog.records]
    assert any("HTTP Request" in r.getMessage() for r in caplog.records)  # httpx did log
    assert all(CANARY not in t and CANARY.split(":")[1] not in t for t in texts)


def test_bot_response_hides_the_envelope_from_repr():
    response = BotResponse(200, {"ok": True, "result": {"text": "secret body"}})
    assert "secret body" not in repr(response)
