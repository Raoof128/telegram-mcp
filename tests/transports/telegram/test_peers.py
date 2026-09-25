"""The marked Telegram chat id and its inverse (5b-4 S2; comms v0.3 C7, C16)."""

import pytest
from telethon import utils
from telethon.tl import types

from comms.transports.telegram.peers import marked_chat_id, unmark_chat_id


@pytest.mark.parametrize(
    "kind,peer",
    [("user", types.PeerUser), ("group", types.PeerChat), ("channel", types.PeerChannel)],
)
@pytest.mark.parametrize("number", [1, 42, 999999999, 1234567890, 999999999999])
def test_marking_agrees_with_telethon(kind, peer, number):
    assert marked_chat_id(f"{kind}:{number}") == str(utils.get_peer_id(peer(number)))


@pytest.mark.parametrize(
    "marked,expected",
    [
        ("42", ("user", 42)),
        ("-42", ("chat", 42)),
        ("-1000000000042", ("channel", 42)),
        ("-1001234567890", ("channel", 1234567890)),
    ],
)
def test_unmark_is_the_inverse(marked, expected):
    assert unmark_chat_id(marked) == expected


@pytest.mark.parametrize("marked", ["0", "", "x", "4 2", "-0", "+42", "٤٢"])
def test_unmark_refuses_anything_else(marked):
    with pytest.raises(ValueError):
        unmark_chat_id(marked)
