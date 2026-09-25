"""comms v0.3 Task C22: the Meta Graph client and its explicit error-code table (A19, A26)."""

import json
import logging

import httpx
import pytest

from comms.core.delivery.transport import ResultKind
from comms.transports.whatsapp.cloud.classify import META_CODES, classify_send
from comms.transports.whatsapp.cloud.http import (
    GRAPH_ORIGIN,
    GraphApi,
    GraphRefused,
    GraphTransportError,
)
from tests.transports.whatsapp_cloud.helpers import CANARY, Secrets, fixture_transport, raising

PHONE_ID = "106540352242922"
BODY = {
    "messaging_product": "whatsapp",
    "to": "61400000001",
    "type": "text",
    "text": {"body": "hi"},
}


def _api(transport, token=CANARY):
    return GraphApi(Secrets(token), version=1, phone_number_id=PHONE_ID, transport=transport)


def _outcome(api):
    try:
        return api.send_message(BODY)
    except GraphTransportError as exc:
        return exc


def _classify(name):
    return classify_send(_outcome(_api(fixture_transport(name))))


def test_a_send_is_one_post_to_the_pinned_origin_with_a_bearer_header():
    seen = []
    _api(fixture_transport("send_ok", seen)).send_message(BODY)
    (request,) = seen
    assert (request.method, f"{request.url.scheme}://{request.url.host}") == ("POST", GRAPH_ORIGIN)
    assert request.url.path == f"/v21.0/{PHONE_ID}/messages" and CANARY not in str(request.url)
    assert request.headers["authorization"] == f"Bearer {CANARY}"
    assert json.loads(request.content) == BODY


def test_accepted_carries_the_wamid():
    result = _classify("send_ok")
    assert result.kind is ResultKind.ACCEPTED and result.provider_message_ref.startswith("wamid.")


@pytest.mark.parametrize(
    "name,kind,code",
    [
        ("err_130429_throughput", ResultKind.FAILED_TRANSIENT, "RATE_LIMITED"),
        ("err_131056_pair_rate", ResultKind.FAILED_TRANSIENT, "RATE_LIMITED"),
        ("err_190_token_expired", ResultKind.FAILED_TRANSIENT, "CREDENTIAL"),
        ("err_131047_reengagement", ResultKind.FAILED_PERMANENT, "WINDOW_CLOSED"),
        ("err_132001_template_missing", ResultKind.FAILED_PERMANENT, "TEMPLATE_UNAVAILABLE"),
        ("err_132015_template_paused", ResultKind.FAILED_PERMANENT, "TEMPLATE_UNAVAILABLE"),
        ("err_131026_undeliverable", ResultKind.FAILED_PERMANENT, "UNDELIVERABLE"),
        ("err_100_invalid_parameter", ResultKind.FAILED_PERMANENT, "INVALID_REQUEST"),
        ("err_131000_generic", ResultKind.OUTCOME_UNKNOWN, None),
    ],
)
def test_each_documented_code(name, kind, code):
    result = _classify(name)
    assert (result.kind, result.code) == (kind, code)


def test_unknown_meta_code_is_outcome_unknown():
    assert 999999 not in META_CODES
    assert _classify("err_999999_unknown").kind is ResultKind.OUTCOME_UNKNOWN


@pytest.mark.parametrize("name", ["http_502", "malformed", "send_ok_without_id"])
def test_5xx_malformed_and_a_success_without_an_id_are_unknown(name):
    assert _classify(name).kind is ResultKind.OUTCOME_UNKNOWN


def test_a_documented_code_on_a_5xx_is_still_unknown():
    assert _classify("err_131000_generic").kind is ResultKind.OUTCOME_UNKNOWN
    assert META_CODES[131000][0] is ResultKind.OUTCOME_UNKNOWN


@pytest.mark.parametrize(
    "exc_type,kind",
    [
        (httpx.ConnectError, ResultKind.FAILED_TRANSIENT),
        (httpx.ConnectTimeout, ResultKind.FAILED_TRANSIENT),
        (httpx.ReadTimeout, ResultKind.OUTCOME_UNKNOWN),
        (httpx.RemoteProtocolError, ResultKind.OUTCOME_UNKNOWN),
    ],
)
def test_transport_failures(exc_type, kind):
    assert classify_send(_outcome(_api(raising(exc_type)))).kind is kind


def test_every_table_row_names_a_result_kind_and_a_code():
    for code, (kind, name) in META_CODES.items():
        assert isinstance(code, int) and isinstance(kind, ResultKind)
        assert (name is None) == (kind is ResultKind.OUTCOME_UNKNOWN)


@pytest.mark.parametrize(
    "token", ["", "short", "has space in it 1234567890", "tok\nen0123456789012345"]
)
def test_a_malformed_token_is_refused(token):
    with pytest.raises(GraphRefused):
        _api(fixture_transport("send_ok"), token=token)


def test_a_malformed_phone_number_id_is_refused():
    with pytest.raises(GraphRefused):
        GraphApi(
            Secrets(), version=1, phone_number_id="../me", transport=fixture_transport("send_ok")
        )


def test_token_canary_absent(caplog):
    caplog.set_level(logging.DEBUG)
    api = _api(fixture_transport("send_ok"))
    texts = [repr(api), repr(api.send_message(BODY))]
    for exc_type in (httpx.ConnectError, httpx.ReadTimeout):
        exc = _outcome(_api(raising(exc_type)))
        assert exc.__context__ is None and exc.__cause__ is None
        texts += [str(exc), repr(exc)]
    texts += [r.getMessage() for r in caplog.records]
    assert any("HTTP Request" in r.getMessage() for r in caplog.records)
    assert all(CANARY not in t for t in texts)
