import pytest

from comms.transports.telegram.dispatch import dispatch


def test_unknown_tool_never_reflects_input():
    result = dispatch("SYNTHETIC_CANARY_UNKNOWN", {"secret": "SYNTHETIC_CANARY_VALUE"})
    raw = result.model_dump_json(by_alias=True)
    assert result.is_error is True
    assert "TOOL_NOT_FOUND" in raw
    assert "SYNTHETIC_CANARY" not in raw


def test_status_is_disconnected_and_has_no_disclosure():
    result = dispatch("telegram_status", {})
    assert result.is_error is False
    assert result.structured_content["data"]["connected"] is False
    assert result.structured_content["data"]["authorised"] is False
    assert result.structured_content["meta"]["disclosure"] is None
    assert result.structured_content["meta"]["coverage"] is None


@pytest.mark.parametrize("name", ["telegram_list_projects", "telegram_resolve_project"])
def test_sensitive_catalogue_is_unavailable(name):
    args = {} if name == "telegram_list_projects" else {"query": "synthetic"}
    result = dispatch(name, args)
    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "POLICY_UNCONFIGURED"
