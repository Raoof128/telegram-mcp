"""comms v0.3 Task C24: WhatsApp template operations (A23, A27)."""

import json

import httpx
import pytest

from comms.core.providers.capability import Capability as C
from comms.core.providers.semantics import SEMANTICS
from comms.transports.whatsapp.cloud.http import GraphApi, GraphRefused
from comms.transports.whatsapp.cloud.templates import TemplateCatalog, TemplateOps, schema_version
from tests.transports.whatsapp_cloud.helpers import FIXTURES, Secrets, raising

WABA = "102290129340398"


def _routed(routes, seen):
    def handler(request):
        seen.append(request)
        key = (request.method, request.url.path, request.url.params.get("after"))
        answer = routes.get(key) or routes[(request.method, request.url.path, None)]
        if isinstance(answer, type):
            raise answer("boom", request=request)
        recorded = json.loads((FIXTURES / f"{answer}.json").read_text())
        return httpx.Response(recorded["http_status"], json=recorded["body"])

    return httpx.MockTransport(handler)


def _ops(routes, seen=None):
    seen = [] if seen is None else seen
    api = GraphApi(
        Secrets(),
        version=1,
        phone_number_id="106540352242922",
        waba_id=WABA,
        transport=_routed(routes, seen),
    )
    return TemplateOps(api), seen


LIST = ("GET", f"/v21.0/{WABA}/message_templates", None)


def test_list_get_create_edit_delete_classified():
    ops, seen = _ops(
        {
            LIST: "templates_page1",
            ("GET", f"/v21.0/{WABA}/message_templates", "QVFC"): "templates_page2",
            ("POST", f"/v21.0/{WABA}/message_templates", None): "template_created",
            ("POST", "/v21.0/1111", None): "template_success",
            ("DELETE", f"/v21.0/{WABA}/message_templates", None): "template_success",
        }
    )
    page = ops.list(limit=2)
    assert [(t["name"], t["language"], t["status"]) for t in page.items] == [
        ("nowruz_greeting", "en", "APPROVED"),
        ("nowruz_greeting", "fa", "PAUSED"),
    ]
    assert page.next_cursor == "QVFC" and seen[0].url.params["limit"] == "2"
    last = ops.list(limit=2, cursor="QVFC")
    assert [t["name"] for t in last.items] == ["event_reminder"] and last.next_cursor is None
    body = {
        "name": "spring_sale",
        "language": "en",
        "category": "MARKETING",
        "components": [{"type": "BODY", "text": "Hi {{1}}"}],
    }
    created = ops.create(body)
    assert (created.outcome, created.provider_ref) == ("SUCCEEDED", "1114")
    assert json.loads(seen[-1].content) == body
    assert (
        ops.edit("1111", {"components": [{"type": "BODY", "text": "Hello {{1}}"}]}).outcome
        == "SUCCEEDED"
    )
    deleted = ops.delete("spring_sale")
    assert deleted.outcome == "SUCCEEDED" and seen[-1].url.params["name"] == "spring_sale"
    got = ops.get("nowruz_greeting", "en")
    assert got["status"] == "APPROVED" and got["schema_version"] == schema_version(
        got["components"]
    )


def test_create_is_create_class():
    semantics = SEMANTICS[(C.TEMPLATE_CREATE, "whatsapp_cloud")]
    assert (semantics.retry_class, semantics.ambiguity_policy) == ("CREATE", "resolve_only")
    assert (
        SEMANTICS[(C.TEMPLATE_DELETE, "whatsapp_cloud")].retry_class == "DESTRUCTIVE_NONIDEMPOTENT"
    )
    body = {
        "name": "x",
        "language": "en",
        "category": "UTILITY",
        "components": [{"type": "BODY", "text": "x"}],
    }
    for answer, expected in (
        (httpx.ReadTimeout, ("OUTCOME_UNKNOWN", None)),
        ("template_created_without_id", ("OUTCOME_UNKNOWN", None)),
        ("err_100_template_exists", ("FAILED", "INVALID_REQUEST")),
        (httpx.ConnectError, ("FAILED", "PROVIDER_UNAVAILABLE")),
        ("err_130429_throughput", ("FAILED", "RATE_LIMITED")),
    ):
        seen = []
        ops, seen = _ops({("POST", f"/v21.0/{WABA}/message_templates", None): answer}, seen)
        result = ops.create(body)
        assert (result.outcome, result.code) == expected and len(seen) == 1


def test_template_status_feeds_availability():
    ops, _ = _ops(
        {
            LIST: "templates_page1",
            ("GET", f"/v21.0/{WABA}/message_templates", "QVFC"): "templates_page2",
        }
    )
    catalog = TemplateCatalog()
    assert ops.refresh(catalog) == 3
    en = schema_version([{"type": "BODY", "text": "Happy Nowruz, {{1}}"}])
    assert catalog.available("nowruz_greeting", "en", en)
    assert not catalog.available(
        "nowruz_greeting", "fa", schema_version([{"type": "BODY", "text": "نوروز مبارک {{1}}"}])
    )
    assert not catalog.available("event_reminder", "en", 1)
    assert not catalog.available(
        "nowruz_greeting", "en", en + 1
    )  # an edited body is another version


def test_schema_version_is_stable_and_positive():
    components = [{"type": "BODY", "text": "Hi {{1}}"}]
    assert schema_version(components) == schema_version([dict(c) for c in components]) >= 1
    assert schema_version(components) != schema_version([{"type": "BODY", "text": "Hi {{2}}"}])


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Bad Name", "language": "en", "category": "MARKETING", "components": []},
        {"name": "x", "language": "english", "category": "MARKETING", "components": []},
        {"name": "x", "language": "en", "category": "SPAM", "components": []},
        {"name": "x", "language": "en", "category": "MARKETING"},
    ],
)
def test_malformed_create_is_refused_before_any_call(body):
    seen = []
    ops, seen = _ops({}, seen)
    with pytest.raises(ValueError):
        ops.create(body)
    assert seen == []


def test_a_malformed_waba_id_or_template_id_is_refused():
    with pytest.raises(GraphRefused):
        GraphApi(
            Secrets(),
            version=1,
            phone_number_id="106540352242922",
            waba_id="../x",
            transport=raising(httpx.ConnectError),
        )
    ops, seen = _ops({})
    with pytest.raises(ValueError):
        ops.edit("../me", {"components": []})
    assert seen == []
