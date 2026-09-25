"""comms v0.3 Task C26: WhatsApp account status and capability-gated groups (P §14, §16; A25)."""

import ast
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from comms.core.providers.capability import Capability as C
from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import ADAPTER_CONTRACTS, ProviderTarget, SemanticOperation
from comms.core.providers.semantics import SUPPORT
from comms.transports.whatsapp.cloud.account import WhatsAppCapability
from comms.transports.whatsapp.cloud.groups import GROUP_CAPABILITIES, GroupDiscovery, WhatsAppAdmin
from comms.transports.whatsapp.cloud.http import GraphApi
from tests.conformance.registry import REGISTRY
from tests.conformance.runner import Registry, run_suite
from tests.transports.whatsapp_cloud.helpers import Secrets

NOW = datetime(2026, 9, 25, tzinfo=UTC)
PHONE_ID = "106540352242922"
GROUP_ID = "120363049891234567"
CONTACT = ProviderTarget("whatsapp", "whatsapp_cloud", "dst_c", "+61400000001")
GROUP = ProviderTarget("whatsapp", "whatsapp_cloud", "dst_g", f"group:{GROUP_ID}")
PERMISSION = (
    403,
    {
        "error": {
            "message": "(#10) Application does not have permission",
            "type": "OAuthException",
            "code": 10,
        }
    },
)
UNKNOWN_PATH = (
    400,
    {
        "error": {
            "message": "Unknown path components: /groups",
            "type": "OAuthException",
            "code": 2500,
        }
    },
)
OK_GROUPS = (
    200,
    {"data": [{"id": GROUP_ID, "subject": "Fixture group"}], "paging": {"cursors": {"after": "X"}}},
)
PHONE_OK = (
    200,
    {
        "verified_name": "Fixture Co",
        "quality_rating": "GREEN",
        "status": "CONNECTED",
        "id": PHONE_ID,
    },
)


def _api(routes, seen):
    def handler(request):
        seen.append((request.method, request.url.path, request.content))
        answer = routes[(request.method, request.url.path)]
        if isinstance(answer, type):
            raise answer("boom", request=request)
        return httpx.Response(answer[0], json=answer[1])

    return GraphApi(
        Secrets(), version=1, phone_number_id=PHONE_ID, transport=httpx.MockTransport(handler)
    )


def _world(groups_answer, extra=None):
    seen = []
    routes = {
        ("GET", f"/v21.0/{PHONE_ID}/groups"): groups_answer,
        ("GET", f"/v21.0/{PHONE_ID}"): PHONE_OK,
        **(extra or {}),
    }
    api = _api(routes, seen)
    discovery = GroupDiscovery(api)
    discovery.discover()
    return api, discovery, seen


def test_the_group_capabilities_are_p16s():
    assert set(GROUP_CAPABILITIES) == {
        C.GROUP_LIST,
        C.GROUP_GET,
        C.GROUP_MEMBERS,
        C.GROUP_MEMBER_REMOVE,
        C.GROUP_INVITE_GET,
        C.GROUP_INVITE_RESET,
        C.GROUP_SETTINGS_UPDATE,
        C.GROUP_MESSAGE_SEND,
    }


@pytest.mark.parametrize(
    "answer,state", [(PERMISSION, S.ACCOUNT_INELIGIBLE), (UNKNOWN_PATH, S.PROVIDER_UNSUPPORTED)]
)
def test_groups_unavailable_every_group_op_unsupported(answer, state):
    api, discovery, seen = _world(answer)
    assert set(discovery.states.values()) == {state}
    states = (
        WhatsAppCapability(api, discovery, clock=lambda: NOW)
        .snapshot("whatsapp_cloud", GROUP)
        .states
    )
    assert {states[c] for c in GROUP_CAPABILITIES} == {state}
    before = len(seen)
    admin = WhatsAppAdmin(api, discovery)
    for cap, args in (
        (C.GROUP_MEMBER_REMOVE, {"wa_id": "61400000009"}),
        (C.GROUP_INVITE_RESET, {}),
        (C.GROUP_SETTINGS_UPDATE, {"subject": "New"}),
    ):
        result = admin.invoke(SemanticOperation(cap, args), GROUP, "k")
        assert (result.outcome, result.code) == ("FAILED", state.value)
    assert len(seen) == before  # nothing simulated, nothing sent


def test_discovery_that_cannot_decide_is_unknown_never_available():
    _, discovery, _ = _world(httpx.ReadTimeout)
    assert set(discovery.states.values()) == {S.UNKNOWN}


def test_groups_available_ops_classified():
    extra = {
        ("DELETE", f"/v21.0/{GROUP_ID}/participants"): (200, {"success": True}),
        ("POST", f"/v21.0/{GROUP_ID}/invite_link"): (
            200,
            {"invite_link": "https://chat.whatsapp.com/AbC"},
        ),
        ("POST", f"/v21.0/{GROUP_ID}"): (
            400,
            {"error": {"message": "Invalid parameter", "code": 100}},
        ),
    }
    api, discovery, seen = _world(OK_GROUPS, extra)
    assert set(discovery.states.values()) == {S.AVAILABLE}
    admin = WhatsAppAdmin(api, discovery)
    removed = admin.invoke(
        SemanticOperation(C.GROUP_MEMBER_REMOVE, {"wa_id": "61400000009"}), GROUP, "k"
    )
    assert removed.outcome == "SUCCEEDED"
    assert json.loads(seen[-1][2]) == {
        "messaging_product": "whatsapp",
        "participants": [{"user": "61400000009"}],
    }
    reset = admin.invoke(SemanticOperation(C.GROUP_INVITE_RESET, {}), GROUP, "k")
    assert (reset.outcome, reset.provider_ref) == ("SUCCEEDED", "https://chat.whatsapp.com/AbC")
    refused = admin.invoke(
        SemanticOperation(C.GROUP_SETTINGS_UPDATE, {"subject": "New"}), GROUP, "k"
    )
    assert (refused.outcome, refused.code) == ("FAILED", "INVALID_REQUEST")
    with pytest.raises(ValueError):
        admin.invoke(SemanticOperation(C.GROUP_SETTINGS_UPDATE, {}), GROUP, "k")
    with pytest.raises(ValueError):
        admin.invoke(
            SemanticOperation(C.GROUP_MEMBER_REMOVE, {"wa_id": "61400000009"}), CONTACT, "k"
        )


def test_account_status_feeds_the_baseline_states():
    api, discovery, _ = _world(OK_GROUPS)
    states = (
        WhatsAppCapability(api, discovery, clock=lambda: NOW)
        .snapshot("whatsapp_cloud", CONTACT)
        .states
    )
    assert set(states) == {c for c, a in SUPPORT.items() if "whatsapp_cloud" in a}
    assert states[C.MESSAGE_SEND_TEXT] is S.AVAILABLE and states[C.TEMPLATE_LIST] is S.AVAILABLE
    flagged = {
        ("GET", f"/v21.0/{PHONE_ID}"): (
            200,
            {"status": "FLAGGED", "quality_rating": "RED", "id": PHONE_ID},
        )
    }
    api, discovery, _ = _world(OK_GROUPS, flagged)
    states = (
        WhatsAppCapability(api, discovery, clock=lambda: NOW)
        .snapshot("whatsapp_cloud", CONTACT)
        .states
    )
    assert states[C.MESSAGE_SEND_TEXT] is S.TEMPORARILY_UNAVAILABLE
    assert states[C.ACCOUNT_INSPECT] is S.AVAILABLE


def test_not_configured_without_credentials():
    capability = WhatsAppCapability(None, None, clock=lambda: NOW)
    assert set(capability.snapshot("whatsapp_cloud", CONTACT).states.values()) == {S.NOT_CONFIGURED}


def test_account_inspect_returns_status_with_the_name_untrusted():
    api, discovery, _ = _world(OK_GROUPS)
    status = WhatsAppCapability(api, discovery, clock=lambda: NOW).inspect_phone()
    assert status == {
        "status": "CONNECTED",
        "quality_rating": "GREEN",
        "untrusted": {"verified_name": "Fixture Co"},
    }


def test_no_unofficial_automation_module():
    banned = (
        "selenium",
        "playwright",
        "pyppeteer",
        "whatsapp_web",
        "yowsup",
        "webwhatsapi",
        "pywhatkit",
        "baileys",
    )
    root = Path(__file__).resolve().parents[3] / "src" / "comms"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {
            n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
        }
        assert not [n for n in names if n.split(".")[0].lower() in banned], path
        assert not any(
            b in path.name.lower() for b in ("browser", "web_automation", "whatsapp_web")
        ), path


def test_the_whole_whatsapp_cloud_conformance_suite_passes():
    registry = Registry({k: v for k, v in REGISTRY.cases.items() if k[0] == "whatsapp_cloud"})
    report = run_suite(registry, {"whatsapp_cloud": ADAPTER_CONTRACTS["whatsapp_cloud"]})
    assert report.ok and report.skipped == {}, report.failures
