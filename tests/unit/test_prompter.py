"""The daemon half of the prompt-frame wire (design §2.4)."""

import asyncio
import socket

import pytest

from telegram_mcp.consent.prompter import (
    PromptDenied,
    Prompter,
    PromptUnavailable,
    parse_answer,
    prompt_frame,
)
from telegram_mcp.ipc.framing import decode_json_frame, encode_json_frame, read_frame, write_frame

H1 = "tgu_" + "a" * 26
H2 = "tgu_" + "b" * 26
ENVELOPE = {"challenge_sha256": "0" * 64, "sig": "c2ln", "key_id": "p256:sha256:" + "1" * 64}


async def _pair():
    left, right = socket.socketpair()
    daemon = await asyncio.open_connection(sock=left)
    agent = await asyncio.open_connection(sock=right)
    return daemon, agent


def test_frame_builder_matches_the_frozen_shape():
    frame = prompt_frame(handle=H1, challenge=b"{}", signature="c2ln", display={"a": 1})
    assert frame == {
        "type": "PROMPT",
        "handle": H1,
        "challenge": "e30",
        "sig": "c2ln",
        "display": {"a": 1},
    }


def test_answers_parse_and_malformed_ones_do_not():
    assert parse_answer({"type": "APPROVAL", "handle": H1, "envelope": ENVELOPE}) == (
        H1,
        ENVELOPE,
        None,
    )
    assert parse_answer({"type": "DENIAL", "handle": H1, "reason": "DISPLAY-MISMATCH"}) == (
        H1,
        None,
        "DISPLAY-MISMATCH",
    )
    for bad in (
        {"type": "APPROVAL", "handle": H1},
        {"type": "OK", "handle": H1},
        {"type": "DENIAL"},
    ):
        with pytest.raises(ValueError):
            parse_answer(bad)


async def test_no_agent_is_unavailable_at_once():
    with pytest.raises(PromptUnavailable):
        await asyncio.wait_for(
            Prompter().prompt(handle=H1, challenge=b"{}", signature="s", display={}, timeout=45),
            timeout=1,
        )


async def test_an_approval_round_trips():
    (d_reader, d_writer), (a_reader, a_writer) = await _pair()
    prompter = Prompter()
    session = asyncio.create_task(prompter.attach(d_reader, d_writer))
    await asyncio.sleep(0)

    async def agent():
        frame = decode_json_frame(await read_frame(a_reader))
        await write_frame(
            a_writer,
            encode_json_frame(
                {"type": "APPROVAL", "handle": frame["handle"], "envelope": ENVELOPE}
            ),
        )

    helper = asyncio.create_task(agent())
    envelope = await prompter.prompt(
        handle=H1, challenge=b"{}", signature="s", display={}, timeout=5
    )
    assert envelope == ENVELOPE
    await helper
    session.cancel()


async def test_a_late_answer_is_never_matched_to_the_next_prompt():
    (d_reader, d_writer), (a_reader, a_writer) = await _pair()
    prompter = Prompter()
    session = asyncio.create_task(prompter.attach(d_reader, d_writer))
    await asyncio.sleep(0)
    with pytest.raises(PromptDenied) as timed_out:
        await prompter.prompt(handle=H1, challenge=b"{}", signature="s", display={}, timeout=0.1)
    assert timed_out.value.reason == "timeout"

    async def agent():
        await read_frame(a_reader)  # H1's prompt
        second = decode_json_frame(await read_frame(a_reader))
        # the operator's late tap on H1 arrives first, then H2's own answer
        await write_frame(
            a_writer, encode_json_frame({"type": "APPROVAL", "handle": H1, "envelope": ENVELOPE})
        )
        await write_frame(
            a_writer,
            encode_json_frame(
                {"type": "DENIAL", "handle": second["handle"], "reason": "USER-CANCEL"}
            ),
        )

    helper = asyncio.create_task(agent())
    with pytest.raises(PromptDenied) as denied:
        await prompter.prompt(handle=H2, challenge=b"{}", signature="s", display={}, timeout=5)
    assert denied.value.reason == "USER-CANCEL"
    await helper
    session.cancel()


async def test_agent_death_mid_prompt_is_a_denial_and_detaches():
    (d_reader, d_writer), (a_reader, a_writer) = await _pair()
    prompter = Prompter()
    session = asyncio.create_task(prompter.attach(d_reader, d_writer))
    await asyncio.sleep(0)

    async def agent():
        await read_frame(a_reader)
        a_writer.close()

    helper = asyncio.create_task(agent())
    with pytest.raises(PromptDenied) as lost:
        await prompter.prompt(handle=H1, challenge=b"{}", signature="s", display={}, timeout=5)
    assert lost.value.reason == "agent-lost"
    await helper
    await asyncio.wait_for(session, timeout=1)
    assert prompter.connected is False


async def test_agent_death_while_idle_detaches_and_a_new_agent_can_attach():
    (d_reader, d_writer), (_a_reader, a_writer) = await _pair()
    prompter = Prompter()
    session = asyncio.create_task(prompter.attach(d_reader, d_writer))
    await asyncio.sleep(0)
    a_writer.close()  # the agent dies with no prompt in flight
    await asyncio.wait_for(session, timeout=1)
    assert prompter.connected is False

    (d_reader, d_writer), (a_reader, a_writer) = await _pair()
    session = asyncio.create_task(prompter.attach(d_reader, d_writer))
    await asyncio.sleep(0)

    async def agent():
        frame = decode_json_frame(await read_frame(a_reader))
        await write_frame(
            a_writer,
            encode_json_frame(
                {"type": "APPROVAL", "handle": frame["handle"], "envelope": ENVELOPE}
            ),
        )

    helper = asyncio.create_task(agent())
    envelope = await prompter.prompt(
        handle=H1, challenge=b"{}", signature="s", display={}, timeout=5
    )
    assert envelope == ENVELOPE
    await helper
    session.cancel()
