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
from typing import Any

from telegram_mcp.disclosure.bounds import (
    MEDIA_KIND_MAX,
    NAME_MAX,
    TEXT_MAX,
    USERNAME_MAX,
    clamp,
    fit,
)
from telegram_mcp.disclosure.coordinator import RetrievalRefusal
from telegram_mcp.disclosure.seams import ProjectSnapshot, normalise
from telegram_mcp.storage.refstore import PeerRow, RefStore
from telegram_mcp.telegram.deadline import Deadline, WorkBudget
from telegram_mcp.telegram.errors import GatewayError

__all__ = ["TelegramReads"]

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
        deadline_s: float = 15.0,
        max_rpcs: int = 20,
    ) -> None:
        self._session = session
        self._conn = conn
        self._mint = mint_cursor
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
            if view is None or not snapshot.owner_scope.admits(view.chat_type, view.is_archived):
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
            if dialog is None or not snapshot.owner_scope.admits(
                dialog.chat_type, dialog.is_archived
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
        messages = []
        for view in views:
            text, cut = clamp(view.text, TEXT_MAX)
            sender_ref = None
            if view.sender is not None:
                identity = f"{view.sender[0]}:{view.sender[1]}"
                if identity in snapshot.readable:  # §19.4: existing refs of readable peers only
                    row = refs.peer_by_identity(identity)
                    sender_ref = row.peer_ref if row is not None else None
            messages.append(
                {
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
                        refs.message_ref(chat.row_id, view.reply_to_id)
                        if view.reply_to_id
                        else None
                    ),
                    "has_media": view.has_media,
                    "media_kind": clamp(view.media_kind, MEDIA_KIND_MAX)[0],
                    "edited": view.edited,
                }
            )
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
