"""comms v0.3 Task D26: MCP result shaping and the closed dispatcher at full size (A32; P §54)."""

import pytest

from comms.core import refs
from comms.core.errors import CommsError
from comms.mcp.catalog import TOOL_CATALOG
from comms.mcp.dispatch import AuthenticatedClient, Dispatcher
from comms.services.registry import ServiceRegistry

CLIENT = AuthenticatedClient(client_ref="cli_" + "a" * 26, auth_kind="cml1")
GROUP, RCP = "grp_" + "a" * 26, "rcp_" + "b" * 26
CTX_HANDLE = "ctx_" + "c" * 26
CURSOR_REF = "cur_" + "d" * 26


def _dispatcher(answer):
    seen = []
    services = ServiceRegistry()

    def make(name):
        def service(client, arguments):
            seen.append((name, arguments))
            if isinstance(answer, BaseException):
                raise answer
            return answer

        return service

    for spec in TOOL_CATALOG:
        services.register(spec.service, make(spec.service))
    return Dispatcher(services), seen


def _member_remove(group=GROUP, recipient=RCP):
    return {"group": group, "recipient": recipient, "request_id": refs.mint("request")}


MEMBER_RESULT = {
    "group": GROUP,
    "recipient": RCP,
    "operation": "remove",
    "result": "SUCCEEDED",
    "code": None,
    "actor": "telegram_bot",
    "op_ref": "op_" + "e" * 26,
    "replayed": False,
}


@pytest.mark.parametrize(
    ("raised", "code"),
    [
        (CommsError("NOT_AUTHORIZED"), "NOT_AUTHORIZED"),
        (CommsError("OUTCOME_UNKNOWN"), "OUTCOME_UNKNOWN"),
        (RuntimeError("secret detail +61400000001"), "INTERNAL_ERROR"),
    ],
)
def test_error_code_only_in_structured_content(raised, code):
    dispatcher, _seen = _dispatcher(raised)
    payload = dispatcher.call(CLIENT, "comms_group_member_remove", _member_remove()).to_mcp()
    assert payload["isError"] is True
    assert payload["structuredContent"] == {"error": {"code": code}}
    text = " ".join(part["text"] for part in payload["content"])
    assert code not in text and "61400000001" not in text and "secret" not in text


@pytest.mark.parametrize(
    "arguments",
    [
        _member_remove(group=CTX_HANDLE),
        _member_remove(recipient=CTX_HANDLE),
        _member_remove(group=CURSOR_REF),
    ],
)
def test_write_with_ctx_handle_as_target_refused(arguments):
    dispatcher, seen = _dispatcher(MEMBER_RESULT)
    result = dispatcher.call(CLIENT, "comms_group_member_remove", arguments)
    assert result.error_code == "INVALID_ARGUMENT" and seen == []


def test_a_ctx_handle_hidden_in_any_write_field_is_refused():
    """Even a free string field of a write (a campaign title) never carries a handle."""
    dispatcher, seen = _dispatcher(
        {"campaign": "cmp_" + "f" * 26, "op_ref": "op_" + "e" * 26, "replayed": False}
    )
    for smuggled in (CTX_HANDLE, CURSOR_REF + ".00112233445566778899aabbccddeeff"):
        result = dispatcher.call(
            CLIENT, "comms_campaign_create", {"title": smuggled, "request_id": refs.mint("request")}
        )
        assert result.error_code == "INVALID_ARGUMENT"
    assert seen == []


@pytest.mark.parametrize(
    "arguments",
    [
        _member_remove(group="the Nowruz group"),
        _member_remove(recipient="Ali Rezaei"),
        _member_remove(group="-1001234567890"),
        _member_remove(recipient="+61400000001"),
    ],
)
def test_write_with_free_text_target_refused(arguments):
    dispatcher, seen = _dispatcher(MEMBER_RESULT)
    result = dispatcher.call(CLIENT, "comms_group_member_remove", arguments)
    assert result.error_code == "INVALID_ARGUMENT" and seen == []


def test_results_carry_next_actions():
    dispatcher, _seen = _dispatcher(MEMBER_RESULT)
    ok = dispatcher.call(CLIENT, "comms_group_member_remove", _member_remove()).to_mcp()
    assert ok["isError"] is False and isinstance(ok["structuredContent"]["next_actions"], list)
    for spec in TOOL_CATALOG:
        assert "next_actions" in spec.output_schema["properties"], spec.name


@pytest.mark.parametrize(
    ("outcome", "code", "suggested"),
    [
        ("FAILED", "NOT_AUTHORIZED", "comms_capability_for_group"),
        ("OUTCOME_UNKNOWN", None, "comms_context_recent"),
        ("INVITE_REQUIRED", None, "comms_group_member_invite"),
    ],
)
def test_next_actions_follow_the_outcome(outcome, code, suggested):
    answer = {**MEMBER_RESULT, "operation": "add", "result": outcome, "code": code, "invite": None}
    dispatcher, _seen = _dispatcher(answer)
    arguments = {"group": GROUP, "recipient": RCP, "request_id": refs.mint("request")}
    structured = dispatcher.call(CLIENT, "comms_group_member_add", arguments).structured
    assert suggested in [a["tool"] for a in structured["next_actions"]]
    assert all(a["tool"] in {s.name for s in TOOL_CATALOG} for a in structured["next_actions"])


def test_a_page_with_more_suggests_its_next_page():
    page = {"items": [], "next_cursor": "12"}
    dispatcher, _seen = _dispatcher(page)
    structured = dispatcher.call(CLIENT, "comms_campaign_list", {}).structured
    assert structured["next_actions"] == [
        {"tool": "comms_campaign_list", "why": "more results", "arguments": {"cursor": "12"}}
    ]


def test_a_service_result_outside_its_schema_is_internal_error():
    dispatcher, _seen = _dispatcher({**MEMBER_RESULT, "chat_id": "-1001234567890"})
    result = dispatcher.call(CLIENT, "comms_group_member_remove", _member_remove())
    assert result.error_code == "INTERNAL_ERROR"
