"""The agent renderer strips every prompt-spoofing control (spec §9.8)."""

import subprocess

import pytest

UNSAFE = [
    "\u202e",
    "\u2066",
    "\u2069",
    "\u200e",
    "\u200f",
    "\u061c",
    "\u2028",
    "\u2029",
    "\n",
    "\x85",
]


def _render(binary, text: str) -> str:
    done = subprocess.run(  # noqa: PLW1510 -- returncode asserted below
        [str(binary), "selftest-render", text.encode("utf-8").hex()],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return bytes.fromhex(done.stdout.strip()).decode("utf-8")


@pytest.mark.parametrize("control", UNSAFE)
def test_unsafe_controls_never_reach_the_prompt(consent_agent_binary, control):
    assert control not in _render(consent_agent_binary, f"list{control}chats")


def test_persian_text_survives(consent_agent_binary):
    assert _render(consent_agent_binary, "انجمن فارسی") == "انجمن فارسی"
