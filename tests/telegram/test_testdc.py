"""End to end on Telegram's test DC (opt-in: --run-telegram-testdc)."""

import json
import secrets
import subprocess
import sys

import pytest

from telegram_mcp.disclosure.seams import CoordinatorAuthority
from telegram_mcp.ipc.handlers.auth import auth_handlers
from telegram_mcp.ipc.handlers.projects import project_handlers
from telegram_mcp.ipc.handlers.scope import scope_handlers
from telegram_mcp.keys.store import load_key, provision_missing
from telegram_mcp.runtime.identity import resolve_principal
from telegram_mcp.storage.db import bind_cursor_store, open_db
from telegram_mcp.storage.identity import ensure_owner_principal
from telegram_mcp.telegram.deadline import Deadline, WorkBudget
from telegram_mcp.telegram.discovery import DiscoveryStore
from telegram_mcp.telegram.reads import TelegramReads
from telegram_mcp.telegram.telethon_adapter import TelegramConfig, TelethonSession
from tests.telegram import fixture_builder, testdc
from tests.telegram.recorder import PHASE, recording_factory, violations

pytestmark = pytest.mark.telegram_testdc


async def test_the_four_tools_on_the_test_dc(tmp_path):
    dc = testdc.load()
    marker = "marker-" + secrets.token_hex(16)
    log: list[tuple[str, str]] = []
    keys = tmp_path / "keys"
    provision_missing(keys, phases=(2, 3))
    conn = open_db(tmp_path / "meta.db")
    ensure_owner_principal(conn, privacy_key=load_key("privacy-key"))
    session = TelethonSession(
        TelegramConfig(dc.api_id, tmp_path / "tg", (dc.dc, dc.ip, 80)),
        api_hash=dc.api_hash,
        client_factory=recording_factory(log),
    )
    await session.start()
    auth = auth_handlers(conn, session)

    phone = dc.phone()
    # The gateway only signs in (it never signs up), so register A first in a
    # throwaway session outside the gateway.
    await (await fixture_builder.client_for(dc, str(tmp_path / "a-register"), phone)).disconnect()

    PHASE.set("admin.login")
    started = await auth["auth login"]({"step": "start", "phone": phone})
    done = await auth["auth login"]({"step": "code", "login": started["login"], "code": dc.code()})
    assert done["authorized"] is True
    a_id = await session.me(Deadline(30))

    b = await fixture_builder.client_for(dc, str(tmp_path / "b"), dc.phone())
    c_phone = dc.phone()
    c = await fixture_builder.client_for(dc, str(tmp_path / "c"), c_phone)
    seeded = await fixture_builder.seed(b, c, phone, c_phone, marker)
    assert seeded["a_id"] == a_id
    await b.disconnect()  # the witness opens B's session in its own process

    def witness() -> dict:
        out = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.telegram.witness",
                str(tmp_path / "b"),
                str(dc.api_id),
                dc.api_hash,
                str(a_id),
                str(seeded["group_id"]),
                str(seeded["channel_id"]),
                str(seeded["post_id"]),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        return json.loads(out.stdout)

    before = witness()

    PHASE.set("admin.discover")
    discovery = DiscoveryStore()
    scope = scope_handlers(conn, session, discovery)
    projects = project_handlers(conn)
    project_ref = projects["project create"]({"slug": "testdc", "display_name": "Test DC"})[
        "project_ref"
    ]
    # allow + add every chat kind the fixture made: each is touched by the read tools
    for kind in ("private", "group", "channel", "supergroup"):  # supergroup: the 4c forum
        found = await scope["scope discover"]({})
        handle = next(s["handle"] for s in found["selections"] if s["chat_type"] == kind)
        scope["scope allow"]({"handle": handle})
        found = await scope["scope discover"]({})
        handle = next(s["handle"] for s in found["selections"] if s["chat_type"] == kind)
        scope["project add-peer"]({"project_ref": project_ref, "handle": handle})
    client_ref = "tcl_" + "t" * 26
    conn.execute(
        "INSERT INTO mcp_clients (principal_id, client_ref, auth_kind, auth_binding, client_kind, created_at)"
        " VALUES (1, ?, 'bearer', 'testdc', 'codex_local', 'now')",
        (client_ref,),
    )
    conn.commit()
    projects["project grant-client"](
        {"project_ref": project_ref, "client_ref": client_ref, "egress_level": "full_text"}
    )

    PHASE.set("mcp.retrieval")
    authority = CoordinatorAuthority(
        conn,
        privacy_key=load_key("privacy-key"),
        cursor_key=load_key("cursor-key"),
        cursor_store=bind_cursor_store(conn),
        runtime_id=b"\x05" * 16,
    )
    reads = TelegramReads(
        session,
        conn,
        mint_cursor=authority.mint_project_cursor,
        mint_search_cursor=authority.mint_search_cursor,
    )
    principal = resolve_principal(conn, client_ref)

    def snap(tool, **args):
        args["project_ref"] = project_ref
        request = authority.freeze_arguments(tool, args, principal=principal)
        return request.validated_args, authority.snapshot(tool, request)

    chats = await reads.list_chats(
        *snap("telegram_list_chats", limit=20, chat_type="any", archived="include")
    )
    assert {c["chat_type"] for c in chats["chats"]} == {"private", "group", "channel", "supergroup"}
    unread_before = await reads.get_unread(
        *snap("telegram_get_unread", limit=30, include_muted=True, chat_type="any")
    )
    await reads.resolve_peer(*snap("telegram_resolve_peer", query="4b", chat_type="any", limit=10))
    for chat in chats["chats"]:
        page = await reads.get_messages(
            *snap("telegram_get_messages", peer_ref=chat["peer_ref"], limit=30)
        )
        if chat["chat_type"] == "group":
            from_c = [
                m
                for m in page["messages"]
                if marker in (m["text"] or "") and m["text"].startswith("from C")
            ]
            assert from_c and from_c[0]["sender_peer_ref"] is None  # C's DM is not in the project
        if chat["chat_type"] == "private":
            assert any(marker in (m["text"] or "") for m in page["messages"])

    # ---- 4c: forum isolation, real search exhaustion, the before=0 window ----
    forum = next(c for c in chats["chats"] if c["chat_type"] == "supergroup")
    page = await reads.get_messages(
        *snap("telegram_get_messages", peer_ref=forum["peer_ref"], limit=30)
    )
    by_text = {m["text"]: m["message_ref"] for m in page["messages"] if m["text"]}
    for name, other in (("alpha", "beta"), ("beta", "alpha")):
        anchor = by_text[f"topic-{name} 1 {marker}"]
        ctx = await reads.get_context(
            *snap("telegram_get_context", message_ref=anchor, before=5, after=5)
        )
        texts = [m["text"] or "" for m in ctx["messages"]]
        assert all(f"topic-{other}" not in t and "general" not in t for t in texts), texts
        assert f"topic-{name} 0 {marker}" in texts and f"topic-{name} 2 {marker}" in texts
    general = await reads.get_context(
        *snap("telegram_get_context", message_ref=by_text[f"general 1 {marker}"], before=5, after=5)
    )
    assert all("topic-" not in (m["text"] or "") for m in general["messages"])
    for before, after in (
        (0, 0),
        (0, 2),
        (2, 0),
    ):  # design §4.1: the edge cases, on the real server
        ctx = await reads.get_context(
            *snap(
                "telegram_get_context",
                message_ref=by_text[f"general 1 {marker}"],
                before=before,
                after=after,
            )
        )
        assert ctx["anchor_message_ref"] in [m["message_ref"] for m in ctx["messages"]]
    found, cursor = [], None
    for _page in range(20):  # real search exhaustion across the whole project
        extra = {"cursor": cursor} if cursor else {}
        out = await reads.search_messages(
            *snap("telegram_search_messages", query=marker, limit=50, **extra)
        )
        found += [r["message_ref"] for r in out["results"]]
        cursor = out.get("_next_cursor")
        if cursor is None:
            assert out["_coverage"]["complete"] is True, out["_coverage"]
            break
    assert (
        len(found) == len(set(found)) and len(found) >= 12
    )  # 3+3 topic, 3 General, DM, group, post
    # Telegram's own paging, observed on the real server (design §4.7): two
    # hits a page over the forum's nine marker messages. Only Telegram's own
    # end signal stops the walk, and every hit arrives exactly once.
    ids, offset = [], 0
    for _page in range(10):
        page = await session.search_peer(
            "channel",
            seeded["forum_id"],
            marker,
            min_date=None,
            max_date=None,
            offset_id=offset,
            limit=2,
            client_ref=client_ref,
            deadline=Deadline(15),
            budget=WorkBudget(),
        )
        ids += [view.message_id for view in page.views]
        if page.exhausted:
            break
        offset = page.next_offset
    assert len(ids) == len(set(ids)) == 9, ids  # 3+3 topic, 3 General

    # Independent: markers Telegram reports from B's side (DM, group, channel views).
    assert witness() == before, "a read tool moved a read marker or a view counter"
    # Not independent (read through the gateway itself), recorded as such: A's unread counts.
    unread_after = await reads.get_unread(
        *snap("telegram_get_unread", limit=30, include_muted=True, chat_type="any")
    )
    counts = lambda body: {c["peer_ref"]: c["unread_count"] for c in body["chats"]}
    assert counts(unread_after) == counts(unread_before)
    assert unread_after["total_unread_visible"] == unread_before["total_unread_visible"]
    assert violations(log) == [], violations(log)

    await session.stop()
    conn.close()
    for path in [*tmp_path.glob("meta.db*"), *(tmp_path / "tg").rglob("*")]:
        if path.is_file():
            assert marker.encode() not in path.read_bytes(), f"marker persisted in {path}"
    await c.disconnect()
