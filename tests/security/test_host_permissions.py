"""R-A20 (owner, 2026-09-25): host permission UX is defence in depth.

No blanket auto-approval for comms tools: every consequential write (one that reaches a provider
or cannot be undone) stays host-confirmed. The host prompt is not part of Comms authorization.
The ask list is tied to the catalog, so a new consequential tool cannot slip past it.
"""

import json
from pathlib import Path

from comms.mcp.catalog import TOOL_CATALOG

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
CONSEQUENTIAL = {
    s.name for s in TOOL_CATALOG if s.requires_request_id and (s.destructive or s.open_world)
}


def test_every_consequential_tool_is_host_confirmed():
    asked = set(SETTINGS["permissions"]["ask"])
    assert {f"mcp__comms__{name}" for name in CONSEQUENTIAL} <= asked


def test_no_blanket_comms_allow():
    allowed = SETTINGS["permissions"].get("allow", [])
    assert "mcp__comms" not in allowed and "mcp__comms__*" not in allowed
    assert not set(allowed) & {f"mcp__comms__{name}" for name in CONSEQUENTIAL}


def test_the_cli_send_rules_are_kept():
    assert "Bash(comms campaign send:*)" in SETTINGS["permissions"]["ask"]


def test_the_consequential_set_covers_the_named_actions():
    for name in ("comms_message_send", "comms_message_delete", "comms_campaign_send",
                 "comms_group_member_remove", "comms_group_member_ban",
                 "comms_group_admin_promote", "comms_group_admin_demote",
                 "comms_group_delete", "comms_group_invite_create"):  # fmt: skip
        assert name in CONSEQUENTIAL, name
