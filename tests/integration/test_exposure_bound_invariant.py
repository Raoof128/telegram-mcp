"""Gauntlet G-8: the reservation dominates the exact Phase-3 measurement.

For every 4b tool and every egress mode, adversarial inputs (maximal and
over-long strings, U+0001, quotes, backslashes, astral emoji, lone
surrogates, bidi, nulls) go through the real reads, the real egress, the
coordinator's own sidecar split and Phase 3's ``buckets_for``. Every actual
bucket must be within the step-3 reservation: records and bytes. Seeded,
so a failure reproduces.
"""

import random

import pytest
from telethon.tl import types

from telegram_mcp.disclosure.budget import buckets_for
from telegram_mcp.disclosure.coordinator import _split_sidecar
from tests.integration.test_telegram_reads import (  # noqa: F401 -- the pytest fixture
    WHEN,
    _dialog,
    world,
)

ALPHABET = [
    "a",
    "\u00e9",
    "\x01",
    '"',
    "\\",
    "\U0001f469\u200d\U0001f4bb",
    "\ud800",
    "\u202e",
    "\u0301",
    " ",
    "\u0645",
]
ROUNDS = 12


def _text(rng: random.Random, longest: int) -> str | None:
    if rng.random() < 0.15:
        return None
    return "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, longest)))


def _entities(rng):
    ali = types.User(
        id=100,
        access_hash=1,
        first_name=_text(rng, 400) or "x",
        last_name=_text(rng, 400),
        username=_text(rng, 80),
    )
    news = types.Channel(
        id=7,
        title=_text(rng, 400) or "n",
        photo=types.ChatPhotoEmpty(),
        date=WHEN,
        broadcast=True,
        access_hash=3,
    )
    team = types.Chat(
        id=9,
        title=_text(rng, 400) or "t",
        photo=types.ChatPhotoEmpty(),
        participants_count=3,
        date=WHEN,
        version=1,
    )
    zed = types.User(
        id=555, access_hash=6, first_name=_text(rng, 400) or "z", last_name=_text(rng, 400)
    )
    return ali, news, team, zed


def _script(rng, limit):
    ali, news, team, zed = _entities(rng)
    specs = [
        (types.PeerUser(100), {"unread": rng.randint(0, 2**31), "minute": 5}),
        (
            types.PeerChannel(7),
            {"unread": rng.randint(0, 9), "minute": 9, "archived": rng.random() < 0.5},
        ),
        (types.PeerChat(9), {"unread": rng.randint(1, 9), "minute": 1}),
    ]
    pairs = [_dialog(peer, **kw) for peer, kw in specs]
    dialogs = types.messages.PeerDialogs(
        dialogs=[d for d, _ in pairs],
        messages=[m for _, m in pairs],
        chats=[news, team],
        users=[ali, zed],
        state=types.updates.State(pts=1, qts=0, date=WHEN, seq=0, unread_count=0),
    )
    messages = []
    for i in range(limit, 0, -1):
        media = rng.choice([None, types.MessageMediaPhoto(), types.MessageMediaEmpty()])
        reply = (
            types.MessageReplyHeader(
                reply_to_msg_id=rng.randint(1, 10**9), forum_topic=rng.random() < 0.5
            )
            if rng.random() < 0.5
            else None
        )
        messages.append(
            types.Message(
                id=i,
                peer_id=types.PeerChat(9),
                date=WHEN,
                message=_text(rng, 5000) or "",
                from_id=rng.choice([types.PeerUser(100), types.PeerUser(555), None]),
                post_author=_text(rng, 400),
                media=media,
                reply_to=reply,
                edit_date=WHEN if rng.random() < 0.5 else None,
            )
        )
    history = types.messages.Messages(messages=messages, topics=[], chats=[team], users=[ali, zed])
    return {"messages.GetPeerDialogsRequest": dialogs, "messages.GetHistoryRequest": history}


@pytest.mark.parametrize(
    "egress", [("metadata_only", None), ("excerpt", 64), ("excerpt", 4000), ("full_text", None)]
)
async def test_actual_charge_never_exceeds_the_reservation(world, egress):  # noqa: F811 -- the imported fixture
    conn, fake, reads, snap, refs = world
    conn.execute("UPDATE client_projects SET egress_level = ?, excerpt_max_codepoints = ?", egress)
    conn.execute("UPDATE policy_state SET include_archived = 1")
    conn.commit()
    rng = random.Random(f"g8-{egress}")
    cases = {
        "telegram_list_chats": (100, {"chat_type": "any", "archived": "include"}, "list_chats"),
        "telegram_get_unread": (100, {"include_muted": True, "chat_type": "any"}, "get_unread"),
        "telegram_resolve_peer": (20, {"chat_type": "any"}, "resolve_peer"),
        "telegram_get_messages": (100, {"peer_ref": refs["chat:9"]}, "get_messages"),
    }
    authority = reads._mint.__self__
    for _round in range(ROUNDS):
        for tool, (cap, extra, method) in cases.items():
            limit = rng.randint(1, cap)
            fake.script.update(_script(rng, limit))
            args = {"limit": limit, **extra}
            if tool == "telegram_resolve_peer":
                args["query"] = rng.choice(["a", "é", "\x01", "م", "z"])
            validated, snapshot = snap(tool, **args)
            raw = await getattr(reads, method)(validated, snapshot)
            data, _side = _split_sidecar(authority.apply_egress(raw, snapshot))
            actual = buckets_for(tool, data, client_id=snapshot.client_id)
            reserved = authority.worst_case_buckets(tool, snapshot)
            for key, usage in actual.items():
                assert key in reserved, (tool, key)
                assert usage.records <= reserved[key].records, (tool, egress, usage, reserved[key])
                assert usage.bytes <= reserved[key].bytes, (tool, egress, usage, reserved[key])
