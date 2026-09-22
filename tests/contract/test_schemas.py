import copy

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from telegram_mcp.contract import (
    EXPECTED_TOOLS,
    assemble_output,
    load_contracts,
    strict_json_loads,
    validate_output,
)

TPR_A = "tpr_" + "a" * 26
TPR_B = "tpr_" + "b" * 26
TGP_A = "tgp_" + "c" * 26
TGM_A = "tgm_" + "d" * 26


def _message():
    return {
        "message_ref": TGM_A,
        "origin_project_refs": [TPR_A],
        "sender_kind": "user",
        "sender_display_name": "Synthetic Sender",
        "sender_peer_ref": None,
        "post_author": None,
        "forum_topic": False,
        "topic_title": None,
        "sent_at": "2026-09-22T00:00:00Z",
        "outgoing": False,
        "text": "Synthetic fixture text",
        "text_truncated": False,
        "reply_to_message_ref": None,
        "has_media": False,
        "media_kind": None,
        "edited": False,
    }


def _project(ref=TPR_A):
    return {"project_ref": ref, "display_name": "Synthetic Project"}


def _meta(source, trust):
    return {
        "source": source,
        "content_trust": trust,
        "truncated": False,
        "partial": False,
        "next_cursor": None,
        "disclosure": None,
        "coverage": None,
    }


GATEWAY_META = _meta("gateway", "non_instructional_gateway_metadata")
TELEGRAM_META = _meta("telegram", "untrusted_external_content")


def _data(tool):
    if tool == "telegram_status":
        return {
            "connected": False,
            "authorised": False,
            "account_ref": None,
            "account_label": None,
            "read_scope_mode": None,
            "policy_epoch": None,
            "security_epoch": 1,
            "security_locked": False,
            "disclosure_proof_key_id": "synthetic-test-key",
            "disclosure_proof_public_key": "A" * 43,
            "capabilities": {
                "read_chats": False,
                "search_messages": False,
                "project_namespaces": True,
                "cross_project_search": True,
                "project_selection_required": True,
                "proof_carrying_retrieval": True,
                "egress_profiles": True,
                "exposure_budgets": True,
                "tamper_evident_audit": True,
                "emergency_lock": True,
                "write_messages": False,
                "attachments": False,
                "secret_chats": False,
            },
        }
    if tool == "telegram_list_projects":
        return {"projects": []}
    if tool == "telegram_resolve_project":
        return {"matches": [], "ambiguous": False}
    if tool == "telegram_list_chats":
        return {"project": _project(), "chats": []}
    if tool == "telegram_resolve_peer":
        return {"project": _project(), "matches": [], "ambiguous": False}
    if tool == "telegram_get_messages":
        return {
            "project": _project(),
            "peer": {"peer_ref": TGP_A, "display_name": "Synthetic Chat", "chat_type": "group"},
            "messages": [_message()],
        }
    if tool == "telegram_get_context":
        return {
            "project": _project(),
            "peer": {"peer_ref": TGP_A, "display_name": "Synthetic Chat"},
            "anchor_message_ref": TGM_A,
            "messages": [_message()],
        }
    if tool == "telegram_search_messages":
        return {
            "project": _project(),
            "results": [
                {
                    "origin_project_refs": [TPR_A],
                    "peer_ref": TGP_A,
                    "peer_display_name": "Synthetic Chat",
                    "message_ref": TGM_A,
                    "sender_kind": "user",
                    "sender_display_name": "Synthetic Sender",
                    "sent_at": "2026-09-22T00:00:00Z",
                    "text": "Synthetic hit",
                    "text_truncated": False,
                    "has_context": False,
                }
            ],
            "search_scope": "peer",
        }
    if tool == "telegram_cross_project_search":
        return {
            "projects": [_project(TPR_A), _project(TPR_B)],
            "results": [],
            "search_scope": "cross_project",
        }
    if tool == "telegram_get_unread":
        return {
            "project": _project(),
            "total_unread_visible": 0,
            "total_is_exact": True,
            "chats": [],
        }
    raise AssertionError(tool)


def _success_meta(tool):
    if tool in ("telegram_status", "telegram_list_projects", "telegram_resolve_project"):
        return GATEWAY_META
    return TELEGRAM_META


def test_duplicate_keys_rejected():
    with pytest.raises(ValueError, match="duplicate JSON key"):
        strict_json_loads('{"ok":true,"ok":false}')


def test_non_finite_numbers_rejected():
    with pytest.raises(ValueError, match="non-finite JSON number"):
        strict_json_loads('{"ok": NaN}')


def test_message_definition_is_at_schema_root():
    data = {
        "type": "array",
        "$defs": {"message": {"type": "string"}},
        "items": {"$ref": "#/$defs/message"},
    }
    output = assemble_output(data, {"type": "object"}, {"const": False})
    assert "message" in output["$defs"]
    assert "$defs" not in output["oneOf"][0]["properties"]["data"]
    assert "$defs" in data  # Assembly does not mutate the source contract.


def test_conflicting_definitions_rejected():
    with pytest.raises(ValueError, match="conflicting schema definition"):
        assemble_output(
            {"$defs": {"x": {"type": "string"}}}, {"$defs": {"x": {"type": "integer"}}}, {}
        )


def test_external_ref_rejected():
    from jsonschema.exceptions import _WrappedReferencingError  # exact pin: jsonschema==4.26.0

    contracts = load_contracts()
    bad = copy.deepcopy(contracts["telegram_status"].output_schema)
    bad["oneOf"][0]["properties"]["data"]["properties"]["connected"] = {
        "$ref": "https://example.invalid/schema.json#/x"
    }
    with pytest.raises(_WrappedReferencingError):
        # External ref cannot resolve locally; validator raises instead of fetching.
        Draft202012Validator(bad, format_checker=FormatChecker()).validate(
            {"ok": True, "data": _data("telegram_status"), "meta": GATEWAY_META}
        )


def test_ten_tool_names():
    assert set(load_contracts()) == set(EXPECTED_TOOLS)


@pytest.mark.parametrize("tool", EXPECTED_TOOLS)
def test_success_fixture_validates(tool):
    validate_output(tool, {"ok": True, "data": _data(tool), "meta": _success_meta(tool)})


@pytest.mark.parametrize("tool", EXPECTED_TOOLS)
def test_error_fixture_validates(tool):
    validate_output(
        tool,
        {
            "ok": False,
            "error": {
                "code": "POLICY_UNCONFIGURED",
                "message": "The request could not be completed.",
                "retryable": False,
                "retry_after_seconds": None,
            },
        },
    )


@pytest.mark.parametrize("tool", EXPECTED_TOOLS)
def test_missing_required_field_fails(tool):
    value = {"ok": True, "data": _data(tool), "meta": _success_meta(tool)}
    data = value["data"]
    key = next(iter(data))
    del data[key]
    with pytest.raises(ValidationError):
        validate_output(tool, value)


@pytest.mark.parametrize("tool", EXPECTED_TOOLS)
def test_unknown_field_fails(tool):
    value = {"ok": True, "data": _data(tool), "meta": _success_meta(tool)}
    value["data"]["synthetic_unknown"] = 1
    with pytest.raises(ValidationError):
        validate_output(tool, value)


@pytest.mark.parametrize("tool", EXPECTED_TOOLS)
def test_extra_top_level_field_fails(tool):
    value = {"ok": True, "data": _data(tool), "meta": _success_meta(tool), "extra": 1}
    with pytest.raises(ValidationError):
        validate_output(tool, value)


def test_nonempty_messages_exercise_root_ref():
    # Empty messages would hide the C3 dangling-ref defect; this is the regression.
    contracts = load_contracts()
    for tool in ("telegram_get_messages", "telegram_get_context"):
        schema = contracts[tool].output_schema
        assert "message" in schema["$defs"]
        validate_output(tool, {"ok": True, "data": _data(tool), "meta": TELEGRAM_META})


def test_bound_violation_fails():
    value = {"ok": True, "data": _data("telegram_list_projects"), "meta": GATEWAY_META}
    value["data"]["projects"] = [
        {
            "project_ref": "BAD",
            "display_name": "x",
            "egress_level": "full_text",
            "can_cross_search": True,
            "excerpt_max_codepoints": None,
        }
    ]
    with pytest.raises(ValidationError):
        validate_output("telegram_list_projects", value)


def test_enum_violation_fails():
    value = {"ok": True, "data": _data("telegram_search_messages"), "meta": TELEGRAM_META}
    value["data"]["search_scope"] = "all_projects"
    with pytest.raises(ValidationError):
        validate_output("telegram_search_messages", value)


def test_source_trust_rules():
    for tool in ("telegram_status", "telegram_list_projects", "telegram_resolve_project"):
        assert _success_meta(tool)["source"] == "gateway"
    for tool in (
        "telegram_list_chats",
        "telegram_resolve_peer",
        "telegram_get_messages",
        "telegram_get_context",
        "telegram_search_messages",
        "telegram_cross_project_search",
        "telegram_get_unread",
    ):
        assert _success_meta(tool)["source"] == "telegram"


def test_cross_project_requires_two_projects():
    value = {"ok": True, "data": _data("telegram_cross_project_search"), "meta": TELEGRAM_META}
    value["data"]["projects"] = [_project(TPR_A)]
    with pytest.raises(ValidationError):
        validate_output("telegram_cross_project_search", value)


def test_tool_not_found_not_in_error_enum():
    contracts = load_contracts()
    for tool in EXPECTED_TOOLS:
        schema = contracts[tool].output_schema
        error_branch = schema["oneOf"][1]
        assert (
            "TOOL_NOT_FOUND"
            not in error_branch["properties"]["error"]["properties"]["code"]["enum"]
        )
