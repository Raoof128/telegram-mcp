"""Admin Touch ID prompts show what is being approved (design §2.7, 0B G6)."""

import pytest

from telegram_mcp.consent.admin_summaries import LIMIT, summarize
from telegram_mcp.ipc.admin import PRESENCE_GATED

LONG_REF = "tpr_" + "q" * 26
LONG_ARGS = {
    "project_ref": LONG_REF,
    "client_ref": "tcl_" + "r" * 26,
    "display_name": "N" * 80,
    "slug": "s" * 32,
    "egress_level": "excerpt",
    "excerpt_max_codepoints": 4000,
    "handle": "tgl_" + "h" * 26,
    "mode": "all_cloud_chats",
    "client": "claude_code_local",
    "target": "chatgpt",
    "phone": "+61400000000",
    "code": "12345",
    "password": "hunter2",
}
_CONTROLS = {*range(0x20), *range(0x7F, 0xA0), 0x200E, 0x200F, 0x061C, 0x2028, 0x2029}


@pytest.mark.parametrize("command", sorted(PRESENCE_GATED))
def test_every_gated_command_fits_and_renders_unchanged(command):
    text = summarize(command, LONG_ARGS)
    assert text.startswith(command)
    assert len(text) <= LIMIT
    assert not any(ord(c) in _CONTROLS for c in text)


def test_secrets_never_appear():
    text = summarize("auth login", LONG_ARGS)
    for secret in ("+61400000000", "12345", "hunter2"):
        assert secret not in text


def test_refs_are_shortened_and_salient_fields_shown():
    text = summarize("project set-egress", LONG_ARGS)
    assert "tpr_qqqq…qqqq" in text and "excerpt" in text and "4000" in text


def test_commands_without_salient_fields_stay_bare():
    assert summarize("lock", {}) == "lock"
    assert summarize("unlock", {"presence": {"token": "t"}}) == "unlock"
