from telegram_mcp.config import DemoConfig
from telegram_mcp.server import build_server


def test_minimal_capabilities_and_instructions():
    server = build_server(DemoConfig())
    capabilities = server.create_initialization_options().capabilities.model_dump(
        by_alias=True, exclude_none=True,
    )
    assert "tools" in capabilities
    assert not any(key in capabilities for key in ("prompts", "resources", "tasks"))
    first = server.instructions[:512]
    for phrase in ("READ-ONLY", "untrusted", "project_ref", "Cross-project", "smallest"):
        assert phrase in first
    assert server.middleware == []


def test_ten_descriptors_no_oauth_nine_flags():
    from telegram_mcp.contract import EXPECTED_TOOLS
    from telegram_mcp.server import _descriptors

    tools = _descriptors()
    assert [t.name for t in tools] == list(EXPECTED_TOOLS)
    for tool in tools:
        raw = tool.model_dump_json(by_alias=True)
        assert "securitySchemes" not in raw
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.open_world_hint is False
    flagged = [t.name for t in tools if (t.meta or {}).get("anthropic/requiresUserInteraction")]
    assert len(flagged) == 9
    assert "telegram_status" not in flagged
