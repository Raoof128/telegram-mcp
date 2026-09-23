"""The only module that imports Telethon (spec §36, §37; design §3.1-§3.3).

An allowlisted capability surface, not a convenience wrapper. The gateway
owns the MTProto boundary: ``_GatewayClient._call`` sends each request once,
with no retry, sleep, flood cache or hidden migrate RPC, and only if the
current operation's allowlist names it and its work budget pays for it.
Telethon's login helpers are never used (they retry, resend and pull
updates on their own). ``InputPeer``s come only from the local entity
cache. Telethon objects never leave this module.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import stat
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from telethon import TelegramClient, errors, utils
from telethon import password as srp
from telethon.tl import functions, types

from telegram_mcp.telegram.deadline import (
    Deadline,
    DeadlineExceeded,
    FairScheduler,
    WorkBudget,
    WorkBudgetExceeded,
)
from telegram_mcp.telegram.errors import GatewayError

__all__ = [
    "OPERATIONS",
    "REVIEWED_REQUESTS",
    "DialogView",
    "MessageView",
    "TelegramConfig",
    "TelethonSession",
    "qualified",
    "translate",
]

# Design §3.3 as revised by the gauntlet: every request class the gateway may
# put on the wire, per operation. Each is classified for side effects in
# docs/verification/telegram-rpc-review.md. Nothing else is ever sent.
OPERATIONS: dict[str, frozenset[str]] = {
    "admin.login": frozenset(
        {
            "auth.SendCodeRequest",
            "auth.SignInRequest",
            "account.GetPasswordRequest",
            "auth.CheckPasswordRequest",
            "help.GetConfigRequest",  # the explicit, budgeted DC switch reads the DC list
        }
    ),
    "admin.status": frozenset({"updates.GetStateRequest", "users.GetUsersRequest"}),
    "admin.discover": frozenset({"messages.GetDialogsRequest"}),
    "mcp.retrieval": frozenset(
        {
            "messages.GetPeerDialogsRequest",
            "messages.GetHistoryRequest",
            "messages.GetMessagesRequest",
            "channels.GetMessagesRequest",
        }
    ),
}
REVIEWED_REQUESTS: frozenset[str] = frozenset().union(*OPERATIONS.values())


def qualified(request: Any) -> str:
    """``<module>.<Class>`` of a TL request, looking through ``Invoke*`` wrappers."""
    while type(request).__name__.startswith("InvokeWith") and hasattr(request, "query"):
        request = request.query
    return f"{type(request).__module__.rsplit('.', 1)[-1]}.{type(request).__name__}"


@dataclass
class _Operation:
    allowed: frozenset[str]
    budget: WorkBudget
    # Requests the session already charged; the client charges everything else
    # (anything Telethon might send on its own), so each request pays once.
    precharged: set[int] = field(default_factory=set)


_CURRENT: ContextVar[_Operation | None] = ContextVar("telegram_mcp_operation", default=None)


@contextlib.contextmanager
def _operation(name: str, budget: WorkBudget) -> Iterator[_Operation]:
    current = _Operation(OPERATIONS[name], budget)
    token = _CURRENT.set(current)
    try:
        yield current
    finally:
        _CURRENT.reset(token)


class _GatewayClient(TelegramClient):  # type: ignore[misc]
    """Telethon's client with the gateway's own ``_call``: one send, reviewed, charged."""

    async def _call(  # type: ignore[no-untyped-def]
        self, sender, request, ordered=False, flood_sleep_threshold=None
    ):
        if isinstance(request, list):
            raise PermissionError("batched requests are not reviewed")
        current = _CURRENT.get()
        name = qualified(request)
        if current is None or name not in current.allowed:
            raise PermissionError(f"unreviewed request: {name}")
        if id(request) in current.precharged:
            current.precharged.discard(id(request))
        else:
            current.budget.spend()
        await request.resolve(self, utils)
        wire = functions.InvokeWithoutUpdatesRequest(request) if self._no_updates else request
        result = await sender.send(wire, ordered=ordered)
        await utils.maybe_async(self.session.process_entities(result))
        return result


_REVOKED = (
    errors.AuthKeyUnregisteredError,
    errors.SessionRevokedError,
    errors.SessionExpiredError,
    errors.UserDeactivatedError,
    errors.UserDeactivatedBanError,
    errors.AuthKeyDuplicatedError,
)
_NOT_ACCESSIBLE = (
    errors.ChannelPrivateError,
    errors.ChatAdminRequiredError,
    errors.ChannelInvalidError,
    errors.PeerIdInvalidError,
)
_UNAVAILABLE = (
    errors.ServerError,  # includes AuthRestartError, TimedOutError, RpcCallFailError
    errors.TimedOutError,
    errors.InterdcCallErrorError,
    errors.RpcCallFailError,
    errors.InvalidDCError,  # a migrate we did not handle explicitly
    ConnectionError,
    OSError,
)
_SESSION_NAME = "primary"


def translate(exc: BaseException) -> GatewayError:
    """Two layers, most specific first (design §6.3). Cancellation is never passed here."""
    if isinstance(exc, GatewayError):
        return exc
    if isinstance(exc, DeadlineExceeded | TimeoutError):
        return GatewayError("DEADLINE_EXCEEDED")
    if isinstance(exc, WorkBudgetExceeded):
        return GatewayError("WORK_BUDGET_EXCEEDED")
    if isinstance(exc, PermissionError):
        return GatewayError("INTERNAL_ERROR")  # an unreviewed request was attempted
    if isinstance(exc, errors.FloodError):
        return GatewayError("FLOOD_WAIT", retry_after=int(getattr(exc, "seconds", 0)) or None)
    if isinstance(exc, _REVOKED):
        return GatewayError("SESSION_REVOKED")
    if isinstance(exc, errors.UnauthorizedError):
        return GatewayError("AUTH_REQUIRED")
    if isinstance(exc, _NOT_ACCESSIBLE):
        return GatewayError("NOT_ACCESSIBLE")
    if isinstance(exc, errors.MsgIdInvalidError):
        return GatewayError("MESSAGE_NOT_FOUND")
    if isinstance(exc, _UNAVAILABLE):
        return GatewayError("TELEGRAM_UNAVAILABLE")
    return GatewayError("INTERNAL_ERROR")


@dataclass(frozen=True)
class TelegramConfig:
    api_id: int
    session_dir: Path
    test_dc: tuple[int, str, int] | None = None


def _default_factory(path: str, api_id: int, api_hash: str, **kwargs: Any) -> Any:
    return _GatewayClient(path, api_id, api_hash, **kwargs)


class TelethonSession:
    def __init__(
        self,
        config: TelegramConfig,
        *,
        api_hash: str,
        client_factory: Callable[..., Any] | None = None,
        scheduler: FairScheduler | None = None,
    ) -> None:
        self._config = config
        self._api_hash = api_hash
        self._factory = client_factory or _default_factory
        self._scheduler = scheduler or FairScheduler()
        self._client: Any = None
        self._lock_fd: int | None = None
        self._code_hash: tuple[str, str] | None = None  # (phone, phone_code_hash), memory only
        self.revoked = False  # Telegram said the authorisation is gone
        self.connected = False
        self.authorized = False
        self.logged_out = False  # the operator ran auth logout-local

    # -- lifecycle ----------------------------------------------------------

    def _prepare_dir(self) -> Path:
        root = self._config.session_dir
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        st = root.stat()
        if stat.S_IMODE(st.st_mode) != 0o700 or st.st_uid != os.geteuid():
            raise GatewayError("ACCOUNT_UNAVAILABLE")
        return root

    def _pin_file_modes(self) -> None:
        for path in self._config.session_dir.glob(f"{_SESSION_NAME}.session*"):
            os.chmod(path, 0o600)

    def _build(self) -> None:
        self._client = self._factory(
            str(self._config.session_dir / _SESSION_NAME),
            self._config.api_id,
            self._api_hash,
            receive_updates=False,
            request_retries=0,
            flood_sleep_threshold=0,
            raise_last_call_error=True,
        )
        if self._config.test_dc is not None:
            dc_id, ip, port = self._config.test_dc
            self._client.session.set_dc(dc_id, ip, port)

    async def start(self) -> None:
        """Lock (fail closed), build, connect. An unreachable Telegram is a state, not a crash."""
        root = self._prepare_dir()
        fd = os.open(root / "session.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise GatewayError("ACCOUNT_UNAVAILABLE") from None
        self._lock_fd = fd
        self._build()
        await self.reconnect()
        self._pin_file_modes()

    async def _probe_authorized(self, deadline: Deadline) -> bool:
        """updates.GetState: any authorised request answers; an unauthorised key is refused."""
        try:
            await self._call_reviewed(
                functions.updates.GetStateRequest(),
                operation="admin.status",
                client_ref="operator",
                deadline=deadline,
                budget=WorkBudget(max_rpcs=1),
            )
        except GatewayError as exc:
            if exc.code in ("SESSION_REVOKED", "AUTH_REQUIRED"):
                return False  # a fresh or logged-out key: unauthorised (a probe never latches)
            raise
        return True

    async def reconnect(self, seconds: float = 10.0) -> bool:
        """Connect (a fresh client after a local logout) and refresh authorisation."""
        if self._client is None:
            self._build()
        try:
            async with asyncio.timeout(seconds):
                await self._client.connect()
            self.connected = True
            self.authorized = await self._probe_authorized(Deadline(seconds))
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except BaseException as exc:  # noqa: BLE001 -- mapped to a frozen code
            if translate(exc).code == "SESSION_REVOKED":
                self.revoked, self.connected = True, True
            else:
                self.connected = False
            return False
        return True

    def readiness(self) -> str | None:
        """None when MCP reads may run; otherwise the §27.1 code to refuse with."""
        if self.revoked:
            return "SESSION_REVOKED"
        if self.logged_out:
            return "AUTH_REQUIRED"
        if not self.connected:
            return "TELEGRAM_UNAVAILABLE"
        if not self.authorized:
            return "AUTH_REQUIRED"
        return None

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.disconnect()  # disconnect only: never log_out
        self.connected = False
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    # -- the one executor (private) ------------------------------------------

    async def _call_reviewed(
        self,
        request: Any,
        *,
        operation: str,
        client_ref: str,
        deadline: Deadline,
        budget: WorkBudget,
        passthrough: tuple[type[BaseException], ...] = (errors.SessionPasswordNeededError,),
    ) -> Any:
        """Send one reviewed request for ``operation``. Refused before the client otherwise.

        ``passthrough`` names Telegram errors a login step handles itself
        (2FA needed, a phone migrate); everything else is translated.
        """
        if qualified(request) not in OPERATIONS[operation]:
            raise GatewayError("INTERNAL_ERROR")  # a programming error, never a network call
        if operation == "mcp.retrieval":
            refused = self.readiness()
            if refused is not None:
                raise GatewayError(refused)
        elif not self.connected and operation != "admin.status":
            if not await self.reconnect(max(0.1, deadline.remaining())):
                raise GatewayError("TELEGRAM_UNAVAILABLE")
        if self._client is None:
            raise GatewayError("AUTH_REQUIRED")
        try:
            async with self._scheduler.slot(client_ref):
                async with asyncio.timeout(deadline.remaining()):
                    with _operation(operation, budget) as current:
                        budget.spend()  # the session charges its own request, always
                        current.precharged.add(id(request))
                        return await self._client(request)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except passthrough:
            raise
        except BaseException as exc:  # noqa: BLE001 -- mapped to a frozen code
            gateway = translate(exc)
            if gateway.code == "SESSION_REVOKED" and operation != "admin.status":
                self.revoked = True
            raise gateway from None

    # -- login and status (admin plane): raw reviewed requests only -----------

    async def _login(
        self,
        request: Any,
        deadline: Deadline,
        budget: WorkBudget,
        passthrough: tuple[type[BaseException], ...] = (errors.SessionPasswordNeededError,),
    ) -> Any:
        return await self._call_reviewed(
            request,
            operation="admin.login",
            client_ref="operator",
            deadline=deadline,
            budget=budget,
            passthrough=passthrough,
        )

    def _signed_in(self) -> None:
        self.revoked, self.logged_out, self.authorized = False, False, True
        self._code_hash = None
        self._pin_file_modes()

    async def is_authorized(self, deadline: Deadline) -> bool:
        if not self.connected and not await self.reconnect(max(0.1, deadline.remaining())):
            raise GatewayError("TELEGRAM_UNAVAILABLE")
        self.authorized = await self._probe_authorized(deadline)
        return self.authorized

    async def send_code(self, phone: str, deadline: Deadline) -> None:
        """One auth.SendCode, never a resend. One explicit, budgeted DC switch on a migrate."""
        budget = WorkBudget(max_rpcs=4)
        request = functions.auth.SendCodeRequest(
            phone, self._config.api_id, self._api_hash, types.CodeSettings()
        )
        migrate = (errors.PhoneMigrateError, errors.NetworkMigrateError)
        try:
            sent = await self._login(request, deadline, budget, passthrough=migrate)
        except migrate as exc:
            try:
                async with asyncio.timeout(deadline.remaining()):
                    with _operation("admin.login", budget):
                        await self._client._switch_dc(int(exc.new_dc))
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except BaseException as switch_failed:  # noqa: BLE001 -- mapped to a frozen code
                raise translate(switch_failed) from None
            sent = await self._login(request, deadline, budget)
        self._code_hash = (phone, sent.phone_code_hash)

    async def sign_in_code(self, phone: str, code: str, deadline: Deadline) -> str:
        if self._code_hash is None or self._code_hash[0] != phone:
            raise GatewayError("AUTH_REQUIRED")  # no code was requested for this number
        try:
            result = await self._login(
                functions.auth.SignInRequest(phone, self._code_hash[1], code),
                deadline,
                WorkBudget(max_rpcs=1),
            )
        except errors.SessionPasswordNeededError:
            return "password_needed"
        if isinstance(result, types.auth.AuthorizationSignUpRequired):
            raise GatewayError("AUTH_REQUIRED")  # third-party apps cannot sign up
        self._signed_in()
        return "authorized"

    async def sign_in_password(self, password: str, deadline: Deadline) -> None:
        budget = WorkBudget(max_rpcs=2)
        challenge = await self._login(functions.account.GetPasswordRequest(), deadline, budget)
        try:
            check = srp.compute_check(challenge, password)
        except ValueError:
            raise GatewayError("AUTH_REQUIRED") from None
        await self._login(functions.auth.CheckPasswordRequest(check), deadline, budget)
        self._signed_in()

    async def me(self, deadline: Deadline) -> int:
        users = await self._call_reviewed(
            functions.users.GetUsersRequest([types.InputUserSelf()]),
            operation="admin.status",
            client_ref="operator",
            deadline=deadline,
            budget=WorkBudget(max_rpcs=1),
        )
        return int(users[0].id)

    async def logout_local(self) -> None:
        """Forget the session locally; never ``log_out``.

        Deleting the files is not enough: the client object holds the auth
        key in memory, and its next connect would silently sign back in. So
        the client is closed and dropped; the next operation builds a fresh
        one from the (now absent) file.
        """
        if self._client is not None:
            await self._client.disconnect()
            with contextlib.suppress(Exception):
                self._client.session.close()
            self._client = None
        for path in self._config.session_dir.glob(f"{_SESSION_NAME}.session*"):
            path.unlink(missing_ok=True)
        self._code_hash = None
        self.connected, self.authorized, self.logged_out = False, False, True

    # -- entities --------------------------------------------------------------

    @staticmethod
    def identity_of(peer: Any) -> tuple[str, int]:
        """Canonical ``(type, id)`` for a Telethon Peer/User/Chat/Channel."""
        if isinstance(peer, types.PeerUser | types.User):
            return "user", int(getattr(peer, "user_id", None) or peer.id)
        if isinstance(peer, types.PeerChat | types.Chat | types.ChatForbidden):
            return "chat", int(getattr(peer, "chat_id", None) or peer.id)
        if isinstance(peer, types.PeerChannel | types.Channel | types.ChannelForbidden):
            return "channel", int(getattr(peer, "channel_id", None) or peer.id)
        raise GatewayError("NOT_ACCESSIBLE")

    def input_peer(self, peer_type: str, peer_id: int) -> Any:
        """From the session's entity cache only. A miss is NOT_ACCESSIBLE, never a lookup."""
        peer = {
            "user": types.PeerUser(peer_id),
            "chat": types.PeerChat(peer_id),
            "channel": types.PeerChannel(peer_id),
        }.get(peer_type)
        if peer is None:
            raise GatewayError("NOT_ACCESSIBLE")
        if self._client is None:
            raise GatewayError("AUTH_REQUIRED")
        try:
            return self._client.session.get_input_entity(utils.get_peer_id(peer))
        except (ValueError, KeyError, TypeError):
            raise GatewayError("NOT_ACCESSIBLE") from None

    async def scan_dialogs(
        self, *, client_ref: str, deadline: Deadline, budget: WorkBudget, page_size: int = 100
    ) -> tuple[list[DialogView], bool]:
        """Page GetDialogs until exhaustion or the budget ends (discovery)."""
        views: list[DialogView] = []
        offset_date, offset_id, offset_peer = None, 0, types.InputPeerEmpty()
        seen: set[str] = set()
        while True:
            try:
                result = await self._call_reviewed(
                    functions.messages.GetDialogsRequest(
                        offset_date=offset_date,
                        offset_id=offset_id,
                        offset_peer=offset_peer,
                        limit=page_size,
                        hash=0,
                    ),
                    operation="admin.discover",
                    client_ref=client_ref,
                    deadline=deadline,
                    budget=budget,
                )
            except GatewayError as exc:
                if exc.code == "WORK_BUDGET_EXCEEDED" and views:
                    return views, False
                raise
            page = [v for v in _views(result) if v.identity not in seen]
            seen.update(v.identity for v in page)
            views.extend(page)
            if len(result.dialogs) < page_size or not page:
                return views, True
            last = result.dialogs[-1]
            last_message = next(
                (
                    m
                    for m in result.messages
                    if m.id == last.top_message
                    and TelethonSession.identity_of(m.peer_id)
                    == TelethonSession.identity_of(last.peer)
                ),
                None,
            )
            offset_date = getattr(last_message, "date", None)
            offset_id = int(last.top_message or 0)
            offset_peer = self.input_peer(*TelethonSession.identity_of(last.peer))

    async def peer_dialogs(
        self,
        identities: list[tuple[str, int]],
        *,
        client_ref: str,
        deadline: Deadline,
        budget: WorkBudget,
    ) -> dict[str, DialogView]:
        """GetPeerDialogs for known peers, 100 at a time; cache misses are skipped."""
        wanted = []
        for peer_type, peer_id in identities:
            try:
                wanted.append(types.InputDialogPeer(peer=self.input_peer(peer_type, peer_id)))
            except GatewayError:
                continue  # not in the entity cache: never looked up over the network
        out: dict[str, DialogView] = {}
        for start in range(0, len(wanted), 100):
            result = await self._call_reviewed(
                functions.messages.GetPeerDialogsRequest(peers=wanted[start : start + 100]),
                operation="mcp.retrieval",
                client_ref=client_ref,
                deadline=deadline,
                budget=budget,
            )
            for view in _views(result):
                out[view.identity] = view
        return out

    async def fetch_history(
        self,
        peer_type: str,
        peer_id: int,
        *,
        offset_id: int,
        max_id: int,
        limit: int,
        client_ref: str,
        deadline: Deadline,
        budget: WorkBudget,
    ) -> tuple[list[MessageView], int | None]:
        """One GetHistory page, newest first; deleted entries dropped.

        Returns the oldest raw id when the page was full, so a caller can
        continue below it even if deleted entries shrank what it can show.
        """
        peer = self.input_peer(peer_type, peer_id)  # cache only: a miss never becomes an RPC
        result = await self._call_reviewed(
            functions.messages.GetHistoryRequest(
                peer=peer,
                offset_id=offset_id,
                offset_date=None,
                add_offset=0,
                limit=limit,
                max_id=max_id,
                min_id=0,
                hash=0,
            ),
            operation="mcp.retrieval",
            client_ref=client_ref,
            deadline=deadline,
            budget=budget,
        )
        entities = {
            TelethonSession.identity_of(entity): entity for entity in [*result.users, *result.chats]
        }
        chat = (peer_type, peer_id)
        views = [
            _message_view(message, chat, entities)
            for message in result.messages
            if not isinstance(message, types.MessageEmpty)
        ]
        full = len(result.messages) >= limit and bool(result.messages)
        return views, (min(int(m.id) for m in result.messages) if full else None)


@dataclass(frozen=True)
class DialogView:
    peer_type: str
    peer_id: int
    chat_type: str
    display_name: str
    username: str | None
    unread_count: int
    is_archived: bool
    is_muted: bool
    last_message_at: str | None
    read_outbox_max_id: int
    top_message_id: int

    @property
    def identity(self) -> str:
        return f"{self.peer_type}:{self.peer_id}"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chat_type(entity: Any) -> str:
    if isinstance(entity, types.User):
        return "private"
    if isinstance(entity, types.Chat | types.ChatForbidden):
        return "group"
    if isinstance(entity, types.Channel) and entity.megagroup:
        return "supergroup"
    return "channel"


def _display_name(entity: Any) -> str:
    if isinstance(entity, types.User):
        if entity.deleted:
            return "Deleted Account"
        name = " ".join(part for part in (entity.first_name, entity.last_name) if part)
        return name or "(no name)"
    return getattr(entity, "title", None) or "(no title)"


def _views(result: Any) -> list[DialogView]:
    entities: dict[tuple[str, int], Any] = {}
    for entity in [*result.users, *result.chats]:
        entities[TelethonSession.identity_of(entity)] = entity
    tops = {(TelethonSession.identity_of(m.peer_id), m.id): m for m in result.messages}
    views: list[DialogView] = []
    for dialog in result.dialogs:
        if not isinstance(dialog, types.Dialog):
            continue  # folder entries are not chats
        key = TelethonSession.identity_of(dialog.peer)
        entity = entities.get(key)
        if entity is None:
            continue
        top = tops.get((key, dialog.top_message))
        mute_until = getattr(dialog.notify_settings, "mute_until", None)
        views.append(
            DialogView(
                peer_type=key[0],
                peer_id=key[1],
                chat_type=_chat_type(entity),
                display_name=_display_name(entity),
                username=getattr(entity, "username", None),
                unread_count=int(dialog.unread_count or 0),
                is_archived=dialog.folder_id == 1,
                is_muted=bool(mute_until and mute_until > datetime.now(UTC)),
                last_message_at=_iso(getattr(top, "date", None)),
                read_outbox_max_id=int(dialog.read_outbox_max_id or 0),
                top_message_id=int(dialog.top_message or 0),
            )
        )
    return views


@dataclass(frozen=True)
class MessageView:
    message_id: int
    sent_at: str
    outgoing: bool
    text: str | None
    sender_kind: str
    sender: tuple[str, int] | None
    sender_display_name: str | None
    post_author: str | None
    forum_topic: bool
    reply_to_id: int | None
    has_media: bool
    media_kind: str | None
    edited: bool


def _media_kind(media: Any) -> str | None:
    if media is None or isinstance(media, types.MessageMediaEmpty):
        return None
    return type(media).__name__.removeprefix("MessageMedia").lower() or "unknown"


def _sender(
    message: Any, chat: tuple[str, int], entities: dict[tuple[str, int], Any]
) -> tuple[str, tuple[str, int] | None]:
    if isinstance(message, types.MessageService):
        return "service", None
    from_peer = getattr(message, "from_id", None)
    if from_peer is None:
        if chat[0] == "user":
            return "user", (None if message.out else chat)
        if chat[0] == "channel" and not getattr(entities.get(chat), "megagroup", False):
            return "channel", chat
        return "unknown", None
    try:
        identity = TelethonSession.identity_of(from_peer)
    except GatewayError:
        return "unknown", None
    if identity == chat and chat[0] == "channel":
        if getattr(entities.get(chat), "megagroup", False):
            return "anonymous_admin", None
        return "channel", chat
    return identity[0], identity


def _message_view(
    message: Any, chat: tuple[str, int], entities: dict[tuple[str, int], Any]
) -> MessageView:
    kind, sender = _sender(message, chat, entities)
    service = isinstance(message, types.MessageService)
    reply = getattr(message, "reply_to", None)
    reply_to_id = None
    forum_topic = False
    if isinstance(reply, types.MessageReplyHeader):
        forum_topic = bool(reply.forum_topic)
        if reply.reply_to_peer_id is None and reply.reply_to_msg_id:
            reply_to_id = int(reply.reply_to_msg_id)
    media_kind = None if service else _media_kind(getattr(message, "media", None))
    entity = entities.get(sender) if sender is not None else None
    return MessageView(
        message_id=int(message.id),
        sent_at=_iso(message.date) or "1970-01-01T00:00:00Z",
        outgoing=bool(message.out),
        text=None if service else (message.message or None),
        sender_kind=kind,
        sender=sender,
        sender_display_name=_display_name(entity) if entity is not None else None,
        post_author=getattr(message, "post_author", None),
        forum_topic=forum_topic,
        reply_to_id=reply_to_id,
        has_media=media_kind is not None,
        media_kind=media_kind,
        edited=bool(getattr(message, "edit_date", None)) and not bool(message.edit_hide),
    )
