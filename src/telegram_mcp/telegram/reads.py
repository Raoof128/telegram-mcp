"""The four 4b project reads (spec §17-§19; design §3.4, §3.8).

The universe is ``snapshot.readable``: the project's members that passed
every authority layer at step 2. Chat metadata comes from GetPeerDialogs for
exactly those peers, so nothing outside the project enters memory during an
MCP call. Refs are minted only for records that passed authorisation, and a
message sender outside the project is never minted (§17.4, §19.4). Message
bodies live only in the returned dict.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from telegram_mcp.authority.policy import admit_live
from telegram_mcp.disclosure.bounds import (
    MEDIA_KIND_MAX,
    NAME_MAX,
    TEXT_MAX,
    USERNAME_MAX,
    PageBudget,
    clamp,
    fit,
)
from telegram_mcp.disclosure.coordinator import RetrievalRefusal
from telegram_mcp.disclosure.seams import ProjectSnapshot, normalise
from telegram_mcp.disclosure.search_authority import SearchSnapshot
from telegram_mcp.storage.refstore import PeerRow, RefStore
from telegram_mcp.telegram.deadline import Deadline, WorkBudget
from telegram_mcp.telegram.errors import GatewayError
from telegram_mcp.telegram.search import EngineState, SearchStop, rpc_bounds, run_page

__all__ = ["TelegramReads"]

_GENERAL = 1  # the forum's General topic (design §4.1)
_PAGE = 100  # Telegram's history page ceiling
_CONTEXT_MAX = 101  # spec §20.4: before + after + anchor


def _topic_of(anchor: Any, is_forum: bool) -> int | None:
    """Design §4.1 step 2. ``None`` is an ordinary chat; ``1`` is General."""
    if not is_forum:
        return None
    if anchor.topic_root:
        return int(anchor.message_id)
    if anchor.forum_topic and anchor.reply_to_top_id:
        return int(anchor.reply_to_top_id)
    if anchor.forum_topic and anchor.reply_to_id:
        return int(anchor.reply_to_id)
    return _GENERAL


_RANK = {
    "exact_display_name": 0,
    "exact_username": 1,
    "prefix_display_name": 2,
    "substring_display_name": 3,
}


def _split(identity: str) -> tuple[str, int]:
    peer_type, _, raw = identity.partition(":")
    return peer_type, int(raw)


def _name(text: str | None) -> str:
    return clamp(text, NAME_MAX)[0] or ""


def _key(date: str | None, peer_ref: str) -> tuple[int, tuple[int, ...], str]:
    """Newest first (fixed-width ISO dates, negated), undated last, then ``peer_ref``."""
    if date is None:
        return (1, (), peer_ref)
    return (0, tuple(-ord(c) for c in date), peer_ref)


def _order(row: PeerRow, view: Any) -> tuple[int, tuple[int, ...], str]:
    return _key(view.last_message_at, row.peer_ref)


class TelegramReads:
    def __init__(
        self,
        session: Any,
        conn: sqlite3.Connection,
        *,
        mint_cursor: Callable[[ProjectSnapshot, Mapping[str, Any], Mapping[str, Any]], str],
        mint_search_cursor: Callable[[SearchSnapshot, Mapping[str, Any], Mapping[str, Any]], str]
        | None = None,
        deadline_s: float = 15.0,
        max_rpcs: int = 20,
    ) -> None:
        self._session = session
        self._conn = conn
        self._mint = mint_cursor
        self._mint_search = mint_search_cursor
        self._deadline_s = deadline_s
        self._max_rpcs = max_rpcs

    # -- shared --------------------------------------------------------------

    def _bounds(self) -> tuple[Deadline, WorkBudget]:
        return Deadline(self._deadline_s), WorkBudget(self._max_rpcs)

    async def _chats(
        self, snapshot: ProjectSnapshot, deadline: Deadline, budget: WorkBudget
    ) -> tuple[list[tuple[PeerRow, Any]], bool]:
        """Readable members with fresh dialog views, owner-scoped, newest first."""
        identities = sorted(snapshot.readable)
        views = await self._session.peer_dialogs(
            [_split(i) for i in identities],
            client_ref=snapshot.client_ref,
            deadline=deadline,
            budget=budget,
        )
        refs = RefStore(self._conn, account_id=snapshot.account_id)
        known = refs.peers_by_identities(identities)
        out: list[tuple[PeerRow, Any]] = []
        for identity in identities:
            view = views.get(identity)
            if view is None or not admit_live(
                snapshot.view,
                snapshot.live_request(identity),
                chat_type=view.chat_type,
                archived=view.is_archived,
            ):
                continue
            name, username = _name(view.display_name), clamp(view.username, USERNAME_MAX)[0]
            row = known.get(identity)
            if row is None or (row.display_name, row.username) != (name, username):
                # An authorised member renamed: refresh its cache (one write, only on change).
                row = refs.ensure_peer(
                    view.peer_type, view.peer_id, display_name=name, username=username
                )
            out.append((row, view))
        out.sort(key=lambda pair: _order(*pair))
        return out, len(views) == len(identities)

    def _page(
        self,
        snapshot: ProjectSnapshot,
        arguments: Mapping[str, Any],
        data: dict[str, Any],
        element: str,
        pairs: list[tuple[PeerRow, Any]],
        build: Callable[[PeerRow, Any], dict[str, Any]],
    ) -> dict[str, Any]:
        """Keyset pagination: constant-size state, never a silent end (spec §19.5, §23.5).

        Order is newest first, ties by ``peer_ref``. The cursor holds the last
        emitted key and the first page's newest date as an anchor. A chat
        whose activity moves past the anchor mid-walk is left for a fresh
        first page, so no chat repeats and the walk always ends.
        """
        state = snapshot.state
        anchor = state.get("anchor_date")
        after = (
            _key(state.get("offset_date"), state["offset_peer_ref"])
            if "offset_peer_ref" in state
            else None
        )
        remaining = [
            pair
            for pair in pairs
            if (
                anchor is None
                or pair[1].last_message_at is None
                or pair[1].last_message_at <= anchor
            )
            and (after is None or _order(*pair) > after)
        ]
        page = remaining[: snapshot.limit]
        data[element] = [build(row, view) for row, view in page]
        fit(data, element)
        emitted = page[: len(data[element])]
        if emitted and len(emitted) < len(remaining):
            last_row, last_view = emitted[-1]
            nxt: dict[str, Any] = {"offset_peer_ref": last_row.peer_ref}
            if last_view.last_message_at is not None:
                nxt["offset_date"] = last_view.last_message_at
            first_newest = anchor or next(
                (view.last_message_at for _, view in remaining if view.last_message_at), None
            )
            if first_newest is not None:
                nxt["anchor_date"] = first_newest
            data["_next_cursor"] = self._mint(snapshot, arguments, nxt)
        return data

    def _message_record(
        self, view: Any, chat: PeerRow, snapshot: ProjectSnapshot, refs: RefStore
    ) -> dict[str, Any]:
        """One contract message (get_messages, get_context). Egress runs later."""
        text, cut = clamp(view.text, TEXT_MAX)
        sender_ref = None
        if view.sender is not None:
            identity = f"{view.sender[0]}:{view.sender[1]}"
            if identity in snapshot.readable:  # §19.4: existing refs of readable peers only
                row = refs.peer_by_identity(identity)
                sender_ref = row.peer_ref if row is not None else None
        return {
            "message_ref": refs.message_ref(chat.row_id, view.message_id),
            "origin_project_refs": [snapshot.project_ref],
            "sender_kind": view.sender_kind,
            "sender_display_name": clamp(view.sender_display_name, NAME_MAX)[0],
            "sender_peer_ref": sender_ref,
            "post_author": clamp(view.post_author, NAME_MAX)[0],
            "forum_topic": view.forum_topic,
            "topic_title": None,  # design §3.8: needs an unreviewed RPC
            "sent_at": view.sent_at,
            "outgoing": view.outgoing,
            "text": text,
            "text_truncated": cut,
            "reply_to_message_ref": (
                refs.message_ref(chat.row_id, view.reply_to_id) if view.reply_to_id else None
            ),
            "has_media": view.has_media,
            "media_kind": clamp(view.media_kind, MEDIA_KIND_MAX)[0],
            "edited": view.edited,
        }

    async def _readable_dialog(
        self, snapshot: ProjectSnapshot, deadline: Deadline, budget: WorkBudget
    ) -> Any:
        """The selected peer's dialog, admitted by the owner's chat-kind switches."""
        assert snapshot.peer_identity is not None  # the snapshot refused otherwise
        peer_type, peer_id = _split(snapshot.peer_identity)
        dialogs = await self._session.peer_dialogs(
            [(peer_type, peer_id)],
            client_ref=snapshot.client_ref,
            deadline=deadline,
            budget=budget,
        )
        dialog = dialogs.get(snapshot.peer_identity)
        if dialog is None or not admit_live(
            snapshot.view,
            snapshot.live_request(snapshot.peer_identity),
            chat_type=dialog.chat_type,
            archived=dialog.is_archived,
        ):
            raise GatewayError("NOT_ACCESSIBLE")
        return dialog

    def _project(self, snapshot: ProjectSnapshot) -> dict[str, str]:
        return {"project_ref": snapshot.project_ref, "display_name": snapshot.project_display_name}

    # -- the four tools ------------------------------------------------------

    async def list_chats(
        self, arguments: Mapping[str, Any], snapshot: ProjectSnapshot
    ) -> dict[str, Any]:
        deadline, budget = self._bounds()
        try:
            pairs, complete = await self._chats(snapshot, deadline, budget)
        except GatewayError as exc:
            raise RetrievalRefusal(exc.code, exc.retry_after) from None
        chat_type, archived = arguments["chat_type"], arguments["archived"]
        pairs = [
            (row, view)
            for row, view in pairs
            if chat_type in ("any", view.chat_type)
            and (archived == "include" or view.is_archived == (archived == "only"))
        ]

        def build(row: PeerRow, view: Any) -> dict[str, Any]:
            return {
                "origin_project_refs": [snapshot.project_ref],
                "peer_ref": row.peer_ref,
                "display_name": row.display_name or "",
                "username": row.username,
                "chat_type": view.chat_type,
                "unread_count": max(0, int(view.unread_count)),
                "is_archived": view.is_archived,
                "is_muted": view.is_muted,
                "last_message_at": view.last_message_at,
            }

        data = self._page(
            snapshot, arguments, {"project": self._project(snapshot)}, "chats", pairs, build
        )
        if not complete:
            data["_partial"] = True
        return data

    async def get_unread(
        self, arguments: Mapping[str, Any], snapshot: ProjectSnapshot
    ) -> dict[str, Any]:
        deadline, budget = self._bounds()
        try:
            pairs, complete = await self._chats(snapshot, deadline, budget)
        except GatewayError as exc:
            raise RetrievalRefusal(exc.code, exc.retry_after) from None
        chat_type = arguments["chat_type"]
        pairs = [
            (row, view)
            for row, view in pairs
            if view.unread_count >= 1
            and (arguments["include_muted"] or not view.is_muted)
            and chat_type in ("any", view.chat_type)
        ]

        def build(row: PeerRow, view: Any) -> dict[str, Any]:
            return {
                "peer_ref": row.peer_ref,
                "display_name": row.display_name or "",
                "chat_type": view.chat_type,
                "unread_count": int(view.unread_count),
                "is_muted": view.is_muted,
                "last_message_at": view.last_message_at,
            }

        head: dict[str, Any] = {
            "project": self._project(snapshot),
            "total_unread_visible": sum(int(v.unread_count) for _, v in pairs)
            if complete
            else None,
            "total_is_exact": complete,
        }
        data = self._page(snapshot, arguments, head, "chats", pairs, build)
        if not complete:
            data["_partial"] = True
        return data

    async def resolve_peer(
        self, arguments: Mapping[str, Any], snapshot: ProjectSnapshot
    ) -> dict[str, Any]:
        deadline, budget = self._bounds()
        try:
            pairs, complete = await self._chats(snapshot, deadline, budget)
        except GatewayError as exc:
            raise RetrievalRefusal(exc.code, exc.retry_after) from None
        query = normalise(arguments["query"].strip())
        handle = query.removeprefix("@")
        ranked: list[tuple[int, str, dict[str, Any]]] = []
        for row, view in pairs:
            if arguments["chat_type"] not in ("any", view.chat_type):
                continue
            name = normalise(row.display_name or "")
            if name == query:
                kind = "exact_display_name"
            elif row.username and normalise(row.username) == handle:
                kind = "exact_username"
            elif query and name.startswith(query):
                kind = "prefix_display_name"
            elif query and query in name:
                kind = "substring_display_name"
            else:
                continue
            ranked.append(
                (
                    _RANK[kind],
                    row.peer_ref,
                    {
                        "peer_ref": row.peer_ref,
                        "display_name": row.display_name or "",
                        "username": row.username,
                        "chat_type": view.chat_type,
                        "match_kind": kind,
                    },
                )
            )
        data: dict[str, Any] = {
            "project": self._project(snapshot),
            "matches": [],
            "ambiguous": False,
        }
        if ranked:
            best = min(rank for rank, *_ in ranked)
            tier = sorted(entry for entry in ranked if entry[0] == best)
            data["matches"] = [entry[2] for entry in tier[: int(arguments["limit"])]]
            data["ambiguous"] = len(tier) > 1
            fit(data, "matches")
        if not complete:
            data["_partial"] = True
        return data

    async def get_messages(
        self, arguments: Mapping[str, Any], snapshot: ProjectSnapshot
    ) -> dict[str, Any]:
        assert snapshot.peer_identity is not None  # the snapshot refused otherwise
        deadline, budget = self._bounds()
        peer_type, peer_id = _split(snapshot.peer_identity)
        state = snapshot.state
        anchor = int(state.get("anchor_id", 0))
        offset = int(state.get("offset_id", 0))
        refs = RefStore(self._conn, account_id=snapshot.account_id)
        try:
            dialogs = await self._session.peer_dialogs(
                [(peer_type, peer_id)],
                client_ref=snapshot.client_ref,
                deadline=deadline,
                budget=budget,
            )
            dialog = dialogs.get(snapshot.peer_identity)
            if dialog is None or not admit_live(
                snapshot.view,
                snapshot.live_request(snapshot.peer_identity),
                chat_type=dialog.chat_type,
                archived=dialog.is_archived,
            ):
                raise GatewayError("NOT_ACCESSIBLE")
            views, below = await self._session.fetch_history(
                peer_type,
                peer_id,
                offset_id=offset,
                max_id=anchor + 1 if anchor else 0,
                limit=snapshot.limit,
                client_ref=snapshot.client_ref,
                deadline=deadline,
                budget=budget,
            )
        except GatewayError as exc:
            raise RetrievalRefusal(exc.code, exc.retry_after) from None
        chat = refs.ensure_peer(
            peer_type,
            peer_id,
            display_name=_name(dialog.display_name),
            username=clamp(dialog.username, USERNAME_MAX)[0],
        )
        anchor = anchor or (views[0].message_id if views else 0)
        messages = [self._message_record(view, chat, snapshot, refs) for view in views]
        data: dict[str, Any] = {
            "project": self._project(snapshot),
            "peer": {
                "peer_ref": chat.peer_ref,
                "display_name": chat.display_name or "",
                "chat_type": dialog.chat_type,
            },
            "messages": messages,
        }
        dropped = fit(data, "messages")
        kept = data["messages"]
        # Continue below the last message shown when the cap dropped some;
        # otherwise below Telegram's oldest raw id when its page was full,
        # even if deleted entries left fewer to show (never a silent end).
        offset = views[len(kept) - 1].message_id if kept and dropped else below
        if offset:
            data["_next_cursor"] = self._mint(
                snapshot, arguments, {"anchor_id": anchor, "offset_id": offset}
            )
        return data

    async def get_context(
        self, arguments: Mapping[str, Any], snapshot: ProjectSnapshot
    ) -> dict[str, Any]:
        """Neighbours of one anchor, never crossing a forum topic (spec §20, design §4.1).

        Telegram caps history pages at 100 and its ``add_offset`` edge
        semantics are undocumented, so each side is its own request bounded
        by ``min_id``/``max_id``: the older side reads strictly below the
        anchor, the newer side strictly above it. Neither depends on whether
        Telegram's window includes ``offset_id``.
        """
        assert snapshot.peer_identity is not None and snapshot.anchor_message_id is not None
        before, after = int(arguments["before"]), int(arguments["after"])
        anchor_id = snapshot.anchor_message_id
        deadline, budget = self._bounds()
        peer_type, peer_id = _split(snapshot.peer_identity)
        refs = RefStore(self._conn, account_id=snapshot.account_id)
        partial = False
        try:
            dialog = await self._readable_dialog(snapshot, deadline, budget)
            found, is_forum = await self._session.fetch_by_ids(
                peer_type,
                peer_id,
                [anchor_id],
                client_ref=snapshot.client_ref,
                deadline=deadline,
                budget=budget,
            )
            anchor = next((v for v in found if v.message_id == anchor_id), None)
            if anchor is None:
                raise GatewayError("MESSAGE_NOT_FOUND")
            # The dialog already says whether the chat is a forum; the by-id
            # response may omit the chat entity. Either signal classifies it as
            # a forum, which only ever narrows the window to one topic.
            topic = _topic_of(anchor, dialog.is_forum or is_forum)
            older, newer, partial = await self._window(
                snapshot, peer_type, peer_id, anchor, topic, before, after, deadline, budget
            )
        except GatewayError as exc:
            raise RetrievalRefusal(exc.code, exc.retry_after) from None
        chat = refs.ensure_peer(
            peer_type,
            peer_id,
            display_name=_name(dialog.display_name),
            username=clamp(dialog.username, USERNAME_MAX)[0],
        )
        # Priority order for the page cap: the anchor first, then nearest
        # neighbours alternately, so a cut never drops the anchor.
        ordered_older = sorted(older, key=lambda v: -v.message_id)[:before]
        ordered_newer = sorted(newer, key=lambda v: v.message_id)[:after]
        priority = [anchor]
        for index in range(max(len(ordered_older), len(ordered_newer))):
            priority += ordered_newer[index : index + 1] + ordered_older[index : index + 1]
        records = [self._message_record(v, chat, snapshot, refs) for v in priority]
        data: dict[str, Any] = {
            "project": self._project(snapshot),
            "peer": {"peer_ref": chat.peer_ref, "display_name": chat.display_name or ""},
            "anchor_message_ref": records[0]["message_ref"],
            "messages": records,
        }
        if fit(data, "messages"):
            partial = True
        kept_ids = {v.message_id for v in priority[: len(data["messages"])]}
        by_ref = {r["message_ref"]: r for r in data["messages"]}
        data["messages"] = [
            by_ref[refs.message_ref(chat.row_id, v.message_id)]
            for v in sorted(priority, key=lambda v: v.message_id)
            if v.message_id in kept_ids
        ][:_CONTEXT_MAX]
        if partial:
            data["_partial"] = True
        return data

    async def _window(
        self,
        snapshot: ProjectSnapshot,
        peer_type: str,
        peer_id: int,
        anchor: Any,
        topic: int | None,
        before: int,
        after: int,
        deadline: Deadline,
        budget: WorkBudget,
    ) -> tuple[list[Any], list[Any], bool]:
        anchor_id = anchor.message_id
        kw = {"client_ref": snapshot.client_ref, "deadline": deadline, "budget": budget}
        if topic is None or topic == _GENERAL:
            general = topic == _GENERAL
            older, older_partial = await self._walk(
                peer_type, peer_id, anchor_id, before, "older", general, kw
            )
            newer, newer_partial = await self._walk(
                peer_type, peer_id, anchor_id, after, "newer", general, kw
            )
            return older, newer, older_partial or newer_partial
        older = []
        if before and not anchor.topic_root:  # nothing precedes a root inside its topic
            older = await self._session.fetch_replies(
                peer_type,
                peer_id,
                topic,
                offset_id=anchor_id,
                add_offset=0,
                limit=before,
                min_id=0,
                max_id=anchor_id,
                **kw,
            )
        newer = []
        if after:
            newer = await self._session.fetch_replies(
                peer_type,
                peer_id,
                topic,
                offset_id=anchor_id,
                add_offset=-(after + 1),
                limit=after + 1,
                min_id=anchor_id,
                max_id=0,
                **kw,
            )
        older = [v for v in older if v.message_id < anchor_id]
        newer = [v for v in newer if v.message_id > anchor_id]
        return older, newer, False

    async def _walk(
        self,
        peer_type: str,
        peer_id: int,
        anchor_id: int,
        want: int,
        side: str,
        general: bool,
        kw: dict[str, Any],
    ) -> tuple[list[Any], bool]:
        """Collect ``want`` neighbours on one side. In General, named-topic
        messages are dropped and the walk pages further out until filled, the
        chat ends, or a budget fires (then partial, design §4.1 step 5)."""
        found: list[Any] = []
        edge = anchor_id
        while len(found) < want:
            need = min(want - len(found), _PAGE)
            try:
                if side == "older":
                    views, _below = await self._session.fetch_history(
                        peer_type, peer_id, offset_id=edge, max_id=edge, limit=need, **kw
                    )
                else:
                    views, _below = await self._session.fetch_history(
                        peer_type,
                        peer_id,
                        offset_id=edge,
                        add_offset=-(need + 1),
                        limit=need + 1,
                        max_id=0,
                        min_id=edge,
                        **kw,
                    )
            except GatewayError as exc:
                if exc.code in ("WORK_BUDGET_EXCEEDED", "DEADLINE_EXCEEDED") and general:
                    return found, True
                raise
            # Nearest first, whatever order or window edge Telegram used.
            if side == "older":
                views = sorted(
                    (v for v in views if v.message_id < edge), key=lambda v: -v.message_id
                )
            else:
                views = sorted(
                    (v for v in views if v.message_id > edge), key=lambda v: v.message_id
                )
            if not views:
                return found, False  # the chat ends on this side
            edge = (
                min(v.message_id for v in views)
                if side == "older"
                else max(v.message_id for v in views)
            )
            found += [v for v in views if not (general and v.forum_topic)]
            if not general:
                return found[:want], False
        return found[:want], False

    # -- search (4c) ---------------------------------------------------------

    async def search_messages(
        self, arguments: Mapping[str, Any], snapshot: SearchSnapshot
    ) -> dict[str, Any]:
        return await self._search(arguments, snapshot)

    async def cross_project_search(
        self, arguments: Mapping[str, Any], snapshot: SearchSnapshot
    ) -> dict[str, Any]:
        return await self._search(arguments, snapshot)

    async def _search(
        self, arguments: Mapping[str, Any], snapshot: SearchSnapshot
    ) -> dict[str, Any]:
        """Per-peer search over the snapshot's universe (spec §21.4, §21A.4).

        Never global search. The engine owns continuation and coverage; this
        owns Telegram access, the owner's chat-kind switches (checked from
        dialog data before a peer is first searched) and record shaping.
        """
        assert self._mint_search is not None
        deadline, budget = self._bounds()
        refs = RefStore(self._conn, account_id=snapshot.account_id)
        cross = snapshot.tool_name == "telegram_cross_project_search"
        rows = refs.peers_by_identities(snapshot.universe)
        by_ref = {row.peer_ref: identity for identity, row in rows.items()}
        per_peer = {}
        for ref, entry in snapshot.state.get("per_peer", {}).items():
            if ref not in by_ref:  # never read as "exhausted": that would skip a peer
                raise RetrievalRefusal("INTERNAL_ERROR")
            per_peer[by_ref[ref]] = int(entry["offset_id"])
        state = EngineState(
            upper_date=snapshot.upper_date,
            window_start=int(snapshot.state.get("window_start", 0)),
            next_unstarted_index=int(snapshot.state.get("next_unstarted_index", 0)),
            per_peer=per_peer,
            uncertain=bool(snapshot.state.get("uncertain", 0)),
            scanned=list(snapshot.state.get("scanned_counts", [])),
            excluded=list(snapshot.state.get("excluded_counts", [])),
        )
        min_date, max_date = rpc_bounds(snapshot.since, snapshot.upper_date)
        admitted: dict[str, bool | None] = {}  # None: not reachable in the entity cache
        names: dict[str, str] = {}

        async def check_new_peers(first: str) -> None:
            # Every active peer this call has not checked yet, in one request:
            # a continuation resumes mid-walk peers, not only unstarted ones.
            fresh = [p for p in state.per_peer if p not in admitted and p != first]
            fresh = [first, *fresh][:100]
            views = await self._session.peer_dialogs(
                [_split(p) for p in fresh],
                client_ref=snapshot.client_ref,
                deadline=deadline,
                budget=budget,
            )
            for peer in fresh:
                view = views.get(peer)
                if view is None:
                    admitted[peer] = None
                    continue
                request = snapshot.live_request(peer)
                admitted[peer] = request is not None and admit_live(
                    snapshot.view, request, chat_type=view.chat_type, archived=view.is_archived
                )
                names[peer] = _name(view.display_name)

        async def fetch(peer: str, offset_id: int, want: int) -> Any:
            try:
                if peer not in admitted:
                    await check_new_peers(peer)
                verdict = admitted[peer]
                if not verdict:  # excluded by owner scope, or unreachable: never searched
                    return _Nothing(inexact=verdict is None, excluded=verdict is False)
                peer_type, peer_id = _split(peer)
                return await self._session.search_peer(
                    peer_type,
                    peer_id,
                    snapshot.query,
                    min_date=min_date,
                    max_date=max_date,
                    offset_id=offset_id,
                    limit=want,
                    client_ref=snapshot.client_ref,
                    deadline=deadline,
                    budget=budget,
                )
            except GatewayError as exc:
                if exc.code == "WORK_BUDGET_EXCEEDED":
                    raise SearchStop("rpc_budget") from None
                if exc.code == "DEADLINE_EXCEEDED":
                    raise SearchStop("deadline") from None
                raise RetrievalRefusal(exc.code, exc.retry_after) from None

        projects_of = {
            peer: sorted(p.project_ref for p in snapshot.projects if peer in p.readable)
            for peer in snapshot.universe
        }
        display = {p.project_ref: p.display_name for p in snapshot.projects}
        container: dict[str, Any] = (
            {
                "projects": [
                    {"project_ref": p.project_ref, "display_name": p.display_name}
                    for p in snapshot.projects
                ],
                "search_scope": "cross_project",
            }
            if cross
            else {
                "project": {
                    "project_ref": snapshot.projects[0].project_ref,
                    "display_name": snapshot.projects[0].display_name,
                },
                "search_scope": "peer" if snapshot.peer_ref else "project",
            }
        )
        page_budget = PageBudget({**container, "results": []})

        def record(peer: str, view: Any, message_ref: str) -> dict[str, Any]:
            text, cut = clamp(view.text, TEXT_MAX)
            origins = projects_of[peer]
            hit: dict[str, Any] = {
                "origin_project_refs": origins,
                "peer_ref": rows[peer].peer_ref,
                "peer_display_name": names.get(peer) or _name(rows[peer].display_name),
                "message_ref": message_ref,
                "sender_kind": view.sender_kind,
                "sender_display_name": clamp(view.sender_display_name, NAME_MAX)[0],
                "sent_at": view.sent_at,
                "text": text,
                "text_truncated": cut,
                "has_context": True,  # the ref resolves through get_context in any origin project
            }
            if cross:
                hit["matched_projects"] = [
                    {"project_ref": ref, "display_name": display[ref]} for ref in origins
                ]
            return hit

        def accept(peer: str, view: Any) -> bool:
            # Both §13.2 caps (bytes and combined codepoints); refs are fixed width.
            return page_budget.take(record(peer, view, "tgm_" + "a" * 26))

        result = await run_page(
            list(snapshot.universe),
            state,
            limit=snapshot.limit,
            fetch=fetch,
            since=snapshot.since,
            peer_cap=snapshot.peer_cap,
            accept=accept,
            projects={p.project_ref: p.readable for p in snapshot.projects},
            rpcs_used=lambda: budget.used,  # measured: dialog checks are RPCs too
        )
        results = []
        for hit in result.hits:
            peer_row = rows[hit.peer]
            results.append(
                record(hit.peer, hit.view, refs.message_ref(peer_row.row_id, hit.view.message_id))
            )
        results.sort(key=lambda r: r["message_ref"])
        results.sort(
            key=lambda r: r["sent_at"], reverse=True
        )  # newest first, ref tie-break (§21A.4)
        data: dict[str, Any] = {**container, "results": results}
        if fit(data, "results"):  # accept() held the page under the cap; this is a backstop
            raise RetrievalRefusal("INTERNAL_ERROR")
        data["_coverage"] = result.coverage  # sidecar: never part of the measured data
        if not result.complete:
            data["_partial"] = True
        if result.state is not None:
            data["_next_cursor"] = self._mint_search(
                snapshot,
                arguments,
                {
                    "upper_date": snapshot.upper_date,
                    "universe_digest": snapshot.universe_digest,
                    "window_start": result.state.window_start,
                    "next_unstarted_index": result.state.next_unstarted_index,
                    "per_peer": {
                        rows[peer].peer_ref: {"offset_id": offset}
                        for peer, offset in result.state.per_peer.items()
                    },
                    "uncertain": int(result.state.uncertain),
                    "scanned_counts": result.state.scanned,
                    "excluded_counts": result.state.excluded,
                },
            )
        return data


@dataclass(frozen=True)
class _Nothing:
    """A peer decided without a search request: the owner's live scope excludes
    it (it leaves the eligible set), or the entity cache cannot reach it (it
    stays eligible, unscanned, and the result is ``telegram_partial``)."""

    inexact: bool
    excluded: bool
    searched: bool = False
    views: tuple[()] = ()
    exhausted: bool = True
    next_offset: None = None
