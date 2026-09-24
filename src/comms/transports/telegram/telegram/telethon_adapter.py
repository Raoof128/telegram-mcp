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
import inspect
import os
import stat
from collections.abc import Callable, Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from telethon import TelegramClient, errors, utils
from telethon import password as srp
from telethon.tl import functions, types

from comms.core.providers.capability import Capability
from comms.transports.telegram.telegram.deadline import (
    Deadline,
    DeadlineExceeded,
    FairScheduler,
    WorkBudget,
    WorkBudgetExceeded,
)
from comms.transports.telegram.telegram.errors import GatewayError
from comms.transports.telegram.telegram.rights import SelfRights
from comms.transports.telegram.telegram.send_attempt import SendAttempt

__all__ = [
    "ADMIN_RPCS",
    "OPERATIONS",
    "READ_RPCS",
    "REVIEWED_REQUESTS",
    "SESSION_RPCS",
    "WRITE_RPCS",
    "DialogView",
    "MessageView",
    "SearchPage",
    "SendAttempt",
    "TelegramConfig",
    "TelethonSession",
    "capability_operation",
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
    # comms v0.3 B14: the one administrative RPC. One send, never retried, only from the
    # auth handler's revoke flow (telegram/admin_rpc.py); no read path may carry it.
    "admin.revoke": frozenset({"auth.LogOutRequest"}),
    "mcp.retrieval": frozenset(
        {
            "messages.GetPeerDialogsRequest",
            "messages.GetHistoryRequest",
            "messages.GetMessagesRequest",
            "channels.GetMessagesRequest",
            "messages.GetRepliesRequest",  # 4c: get_context inside a forum topic
            "messages.SearchRequest",  # 4c: per-peer search, never SearchGlobal
        }
    ),
}
# comms v0.3 C14 (A21): the request classes each capability may put on the wire for the user
# actor, in three disjoint sets. Each capability is its own operation, so the recorder allows
# exactly that capability's classes and nothing else. Classified in telegram-rpc-review.md.
READ_RPCS: Mapping[Capability, frozenset[str]] = MappingProxyType(
    {
        Capability.HISTORY_READ: frozenset(
            {
                "messages.GetHistoryRequest",
                "messages.GetMessagesRequest",
                "channels.GetMessagesRequest",
                "messages.GetRepliesRequest",
            }
        ),
        Capability.HISTORY_SEARCH: frozenset({"messages.SearchRequest"}),
        Capability.MEMBER_LIST: frozenset(
            {"channels.GetParticipantsRequest", "messages.GetFullChatRequest"}
        ),
        Capability.MEMBER_GET: frozenset(
            {"channels.GetParticipantRequest", "messages.GetFullChatRequest"}
        ),
        Capability.ADMIN_LIST: frozenset(
            {"channels.GetParticipantsRequest", "messages.GetFullChatRequest"}
        ),
        Capability.ADMIN_LOG_READ: frozenset({"channels.GetAdminLogRequest"}),
        Capability.INVITE_LIST: frozenset({"messages.GetExportedChatInvitesRequest"}),
        Capability.JOIN_REQUEST_LIST: frozenset({"messages.GetChatInviteImportersRequest"}),
        Capability.TOPIC_LIST: frozenset({"messages.GetForumTopicsRequest"}),
    }
)
WRITE_RPCS: Mapping[Capability, frozenset[str]] = MappingProxyType(
    {
        Capability.MESSAGE_SEND: frozenset({"messages.SendMessageRequest"}),
        Capability.MESSAGE_EDIT: frozenset({"messages.EditMessageRequest"}),
        Capability.MESSAGE_DELETE: frozenset(
            {"messages.DeleteMessagesRequest", "channels.DeleteMessagesRequest"}
        ),
        Capability.MESSAGE_FORWARD: frozenset({"messages.ForwardMessagesRequest"}),
        Capability.MESSAGE_PIN: frozenset({"messages.UpdatePinnedMessageRequest"}),
    }
)
_BAN = frozenset({"channels.EditBannedRequest"})
_EDIT_INVITE = frozenset({"messages.EditExportedChatInviteRequest"})
_JOIN_REQUEST = frozenset({"messages.HideChatJoinRequestRequest"})
_TOPIC_EDIT = frozenset({"messages.EditForumTopicRequest"})
_ADMIN_RIGHTS = frozenset({"channels.EditAdminRequest", "messages.EditChatAdminRequest"})
ADMIN_RPCS: Mapping[Capability, frozenset[str]] = MappingProxyType(
    {
        Capability.MEMBER_ADD: frozenset(
            {"messages.AddChatUserRequest", "channels.InviteToChannelRequest"}
        ),
        Capability.MEMBER_REMOVE: frozenset({"messages.DeleteChatUserRequest", *_BAN}),
        Capability.MEMBER_BAN: _BAN,
        Capability.MEMBER_UNBAN: _BAN,
        Capability.MEMBER_RESTRICT: _BAN,
        Capability.ADMIN_PROMOTE: _ADMIN_RIGHTS,
        Capability.ADMIN_DEMOTE: _ADMIN_RIGHTS,
        Capability.INVITE_CREATE: frozenset({"messages.ExportChatInviteRequest"}),
        Capability.INVITE_EDIT: _EDIT_INVITE,
        Capability.INVITE_REVOKE: _EDIT_INVITE,
        Capability.JOIN_REQUEST_APPROVE: _JOIN_REQUEST,
        Capability.JOIN_REQUEST_REJECT: _JOIN_REQUEST,
        Capability.CHAT_SET_TITLE: frozenset(
            {"messages.EditChatTitleRequest", "channels.EditTitleRequest"}
        ),
        Capability.CHAT_SET_DESCRIPTION: frozenset({"messages.EditChatAboutRequest"}),
        Capability.CHAT_SET_PHOTO: frozenset(
            {"messages.EditChatPhotoRequest", "channels.EditPhotoRequest"}
        ),
        Capability.CHAT_SET_PERMISSIONS: frozenset({"messages.EditChatDefaultBannedRightsRequest"}),
        Capability.TOPIC_CREATE: frozenset({"messages.CreateForumTopicRequest"}),
        Capability.TOPIC_EDIT: _TOPIC_EDIT,
        Capability.TOPIC_CLOSE: _TOPIC_EDIT,
        Capability.TOPIC_REOPEN: _TOPIC_EDIT,
        Capability.GROUP_CREATE: frozenset(
            {"channels.CreateChannelRequest", "messages.CreateChatRequest"}
        ),
        Capability.GROUP_DELETE: frozenset(
            {"channels.DeleteChannelRequest", "messages.DeleteChatRequest"}
        ),
        Capability.GROUP_MIGRATE: frozenset({"messages.MigrateChatRequest"}),
    }
)


def capability_operation(capability: Capability) -> str:
    """The recorder operation for one capability's RPCs (``cap.<capability id>``)."""
    return f"cap.{capability.value}"


for _rpcs in (READ_RPCS, WRITE_RPCS, ADMIN_RPCS):
    for _cap, _names in _rpcs.items():
        OPERATIONS[capability_operation(_cap)] = _names
REVIEWED_REQUESTS: frozenset[str] = frozenset().union(*OPERATIONS.values())
# comms v0.3 B14: the session's one self-administration RPC (not a capability).
SESSION_RPCS: frozenset[str] = OPERATIONS["admin.revoke"]


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
    errors.AuthKeyDuplicatedError,
)
# comms v0.3 B15 (design §B.6): a deactivated or banned account is not a revoked session.
_DEACTIVATED = (errors.UserDeactivatedError, errors.UserDeactivatedBanError)
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
    errors.AuthKeyNotFound,  # the server's -404: transient, never a durable state (B15)
    ConnectionError,
    OSError,
    EOFError,  # asyncio.IncompleteReadError: the link dropped mid-read
)
_SESSION_NAME = "primary"
# Documented refusals of messages.sendMessage: nothing was posted, and a resend cannot help.
_SEND_REFUSED = (
    errors.ChatWriteForbiddenError,
    errors.UserBannedInChannelError,
    errors.PeerIdInvalidError,
    errors.ChannelPrivateError,
    errors.InputUserDeactivatedError,
    errors.UserIsBlockedError,
    errors.MessageEmptyError,
    errors.MessageTooLongError,
)


_ALL_ADMIN_RIGHTS = frozenset(
    name for name in inspect.signature(types.ChatAdminRights.__init__).parameters if name != "self"
)


def _flags(tl: Any) -> frozenset[str]:
    """The true boolean flags of a rights object (``ChatAdminRights``/``ChatBannedRights``)."""
    if tl is None:
        return frozenset()
    return frozenset(k for k, v in tl.to_dict().items() if v is True)


def _channel_rights(result: Any, channel_id: int) -> SelfRights:
    channel = next((c for c in result.chats if getattr(c, "id", None) == channel_id), None)
    if not isinstance(channel, types.Channel):
        raise GatewayError("NOT_ACCESSIBLE")
    kind: Any = "megagroup" if channel.megagroup else "broadcast"
    forum = bool(channel.forum)
    defaults = _flags(channel.default_banned_rights)
    part = result.participant
    if isinstance(part, types.ChannelParticipantCreator):
        return SelfRights(kind, "creator", _ALL_ADMIN_RIGHTS, frozenset(), forum)
    if isinstance(part, types.ChannelParticipantAdmin):
        return SelfRights(kind, "admin", _flags(part.admin_rights), frozenset(), forum)
    if isinstance(part, types.ChannelParticipantBanned):
        own = _flags(part.banned_rights)
        status: Any = "banned" if "view_messages" in own else "restricted"
        return SelfRights(kind, status, frozenset(), own | defaults, forum)
    if isinstance(part, types.ChannelParticipantLeft):
        return SelfRights(kind, "left", frozenset(), frozenset(), forum)
    return SelfRights(kind, "member", frozenset(), defaults, forum)


def _chat_rights(result: Any, chat_id: int) -> SelfRights:
    chat = next((c for c in result.chats if getattr(c, "id", None) == chat_id), None)
    if isinstance(chat, types.ChatForbidden):
        return SelfRights("chat", "banned")
    if not isinstance(chat, types.Chat):
        raise GatewayError("NOT_ACCESSIBLE")
    if chat.left or chat.deactivated:
        return SelfRights("chat", "left")
    if chat.creator:
        return SelfRights("chat", "creator", _ALL_ADMIN_RIGHTS)
    if chat.admin_rights is not None:
        return SelfRights("chat", "admin", _flags(chat.admin_rights))
    return SelfRights("chat", "member", frozenset(), _flags(chat.default_banned_rights))


def _sent_message_id(result: Any, random_id: int) -> int | None:
    if isinstance(result, types.UpdateShortSentMessage):
        return int(result.id)
    for update in getattr(result, "updates", None) or ():
        if isinstance(update, types.UpdateMessageID) and update.random_id == random_id:
            return int(update.id)
    return None


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
    if isinstance(exc, _DEACTIVATED):
        return GatewayError("ACCOUNT_UNAVAILABLE")
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
        self.account_unavailable = False  # Telegram said the account is deactivated or banned
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
            auto_reconnect=False,  # A21: Telethon's reconnect re-sends in-flight requests
            connection_retries=0,
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
            code = translate(exc).code
            if code == "SESSION_REVOKED":
                self.revoked, self.connected = True, True
            elif code == "ACCOUNT_UNAVAILABLE":
                self.account_unavailable, self.connected = True, True
            else:
                self.connected = False
            return False
        return True

    def readiness(self) -> str | None:
        """None when MCP reads may run; otherwise the §27.1 code to refuse with."""
        if self.account_unavailable:
            return "ACCOUNT_UNAVAILABLE"
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
            if isinstance(exc, OSError | EOFError):
                self.connected = False  # auto_reconnect is off: the keeper reconnects explicitly
            if operation != "admin.status":
                if gateway.code == "SESSION_REVOKED":
                    self.revoked = True
                elif gateway.code == "ACCOUNT_UNAVAILABLE":
                    self.account_unavailable = True
            raise gateway from None

    async def call_capability(
        self,
        capability: Capability,
        request: Any,
        *,
        timeout: float,
        passthrough: tuple[type[BaseException], ...] = (),
    ) -> Any:
        """One request of one capability's reviewed set (C14), one RPC, never retried here."""
        return await self._call_reviewed(
            request,
            operation=capability_operation(capability),
            client_ref="comms",
            deadline=Deadline(max(0.001, timeout)),
            budget=WorkBudget(max_rpcs=1),
            passthrough=passthrough,
        )

    async def send_text_once(
        self, peer: Any, text: str, random_id: int, *, timeout: float
    ) -> SendAttempt:
        """One ``messages.sendMessage`` carrying ``random_id`` (A20), classified; never retried."""
        request = functions.messages.SendMessageRequest(peer, text, random_id=random_id)
        try:
            result = await self.call_capability(
                Capability.MESSAGE_SEND,
                request,
                timeout=timeout,
                passthrough=(errors.RandomIdDuplicateError, *_SEND_REFUSED),
            )
        except errors.RandomIdDuplicateError:
            return SendAttempt("duplicate")
        except _SEND_REFUSED:
            return SendAttempt("refused")
        except GatewayError as exc:
            if exc.code == "FLOOD_WAIT":
                return SendAttempt("flood", retry_after=exc.retry_after)
            ambiguous = exc.code in ("TELEGRAM_UNAVAILABLE", "DEADLINE_EXCEEDED")
            return SendAttempt("ambiguous" if ambiguous else "failed")
        return SendAttempt("sent", message_id=_sent_message_id(result, random_id))

    async def self_rights(self, peer_type: str, peer_id: int, *, timeout: float) -> SelfRights:
        """The account's own standing in a group (C17), one read RPC: ``channels.getParticipant
        (self)`` for a channel or supergroup, ``messages.getFullChat`` for a basic group."""
        if peer_type == "channel":
            request = functions.channels.GetParticipantRequest(
                self.input_peer("channel", peer_id), types.InputUserSelf()
            )
            try:
                result = await self.call_capability(
                    Capability.MEMBER_GET,
                    request,
                    timeout=timeout,
                    passthrough=(errors.UserNotParticipantError,),
                )
            except errors.UserNotParticipantError:
                return SelfRights("megagroup", "left")
            return _channel_rights(result, peer_id)
        if peer_type == "chat":
            result = await self.call_capability(
                Capability.MEMBER_GET,
                functions.messages.GetFullChatRequest(peer_id),
                timeout=timeout,
            )
            return _chat_rights(result, peer_id)
        raise GatewayError("NOT_ACCESSIBLE")

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
        self.account_unavailable = False
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

    async def admin_log_out(self, deadline: Deadline) -> None:
        """Exactly one ``auth.LogOutRequest`` (comms v0.3 B14); only ``admin_rpc`` calls this."""
        await self._call_reviewed(
            functions.auth.LogOutRequest(),
            operation="admin.revoke",
            client_ref="operator",
            deadline=deadline,
            budget=WorkBudget(max_rpcs=1),
        )

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
        requested: set[str] = set()
        for peer_type, peer_id in identities:
            try:
                wanted.append(types.InputDialogPeer(peer=self.input_peer(peer_type, peer_id)))
                requested.add(f"{peer_type}:{peer_id}")
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
                if view.identity in requested:  # a dialog nobody asked for is never admitted
                    out[view.identity] = view
        return out

    def _message_views(
        self, result: Any, chat: tuple[str, int]
    ) -> tuple[list[MessageView], dict[tuple[str, int], Any]]:
        entities = {
            TelethonSession.identity_of(entity): entity for entity in [*result.users, *result.chats]
        }
        views = [
            _message_view(message, chat, entities)
            for message in result.messages
            if not isinstance(message, types.MessageEmpty)
            and _belongs(message, chat)  # never attribute another chat's message to this one
        ]
        return views, entities

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
        add_offset: int = 0,
        min_id: int = 0,
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
                add_offset=add_offset,
                limit=limit,
                max_id=max_id,
                min_id=min_id,
                hash=0,
            ),
            operation="mcp.retrieval",
            client_ref=client_ref,
            deadline=deadline,
            budget=budget,
        )
        views, _entities = self._message_views(result, (peer_type, peer_id))
        below, _foreign = _page_end(result, limit, (peer_type, peer_id))
        return views, below

    async def fetch_by_ids(
        self,
        peer_type: str,
        peer_id: int,
        ids: list[int],
        *,
        client_ref: str,
        deadline: Deadline,
        budget: WorkBudget,
    ) -> tuple[list[MessageView], bool]:
        """Messages by id (the get_context anchor), and whether the chat is a forum."""
        peer = self.input_peer(peer_type, peer_id)
        wanted = [types.InputMessageID(int(i)) for i in ids]
        request = (
            functions.channels.GetMessagesRequest(peer, wanted)
            if peer_type == "channel"
            else functions.messages.GetMessagesRequest(wanted)
        )
        result = await self._call_reviewed(
            request,
            operation="mcp.retrieval",
            client_ref=client_ref,
            deadline=deadline,
            budget=budget,
        )
        chat = (peer_type, peer_id)
        views, entities = self._message_views(result, chat)
        return views, bool(getattr(entities.get(chat), "forum", False))

    async def fetch_replies(
        self,
        peer_type: str,
        peer_id: int,
        topic_id: int,
        *,
        offset_id: int,
        add_offset: int,
        limit: int,
        min_id: int,
        max_id: int,
        client_ref: str,
        deadline: Deadline,
        budget: WorkBudget,
    ) -> list[MessageView]:
        """messages.GetReplies inside one forum topic: it never crosses into another."""
        peer = self.input_peer(peer_type, peer_id)
        result = await self._call_reviewed(
            functions.messages.GetRepliesRequest(
                peer=peer,
                msg_id=topic_id,
                offset_id=offset_id,
                offset_date=None,
                add_offset=add_offset,
                limit=limit,
                max_id=max_id,
                min_id=min_id,
                hash=0,
            ),
            operation="mcp.retrieval",
            client_ref=client_ref,
            deadline=deadline,
            budget=budget,
        )
        views, _entities = self._message_views(result, (peer_type, peer_id))
        return views

    async def search_peer(
        self,
        peer_type: str,
        peer_id: int,
        query: str,
        *,
        min_date: datetime | None,
        max_date: datetime | None,
        offset_id: int,
        limit: int,
        client_ref: str,
        deadline: Deadline,
        budget: WorkBudget,
    ) -> SearchPage:
        """One messages.Search page in one peer (never SearchGlobal).

        ``None`` bounds are sent as 0, which Telegram reads as unbounded.
        """
        peer = self.input_peer(peer_type, peer_id)
        result = await self._call_reviewed(
            functions.messages.SearchRequest(
                peer=peer,
                q=query,
                filter=types.InputMessagesFilterEmpty(),
                min_date=min_date,
                max_date=max_date,
                offset_id=offset_id,
                add_offset=0,
                limit=limit,
                max_id=0,
                min_id=0,
                hash=0,
            ),
            operation="mcp.retrieval",
            client_ref=client_ref,
            deadline=deadline,
            budget=budget,
        )
        views, _entities = self._message_views(result, (peer_type, peer_id))
        next_offset, foreign = _page_end(result, limit, (peer_type, peer_id))
        return SearchPage(
            views=views,
            exhausted=next_offset is None,
            # A response naming another chat is not a response we can vouch for.
            inexact=bool(getattr(result, "inexact", False)) or foreign,
            next_offset=next_offset,
            examined=len(result.messages),  # raw, before any filtering
        )


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
    is_forum: bool = False

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
                is_forum=bool(getattr(entity, "forum", False)),
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
    # Topic classification for get_context (design §4.1). Never exposed: the
    # contract carries only forum_topic, and raw topic ids must not leave.
    reply_to_top_id: int | None = None
    topic_root: bool = False


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
    reply_to_top_id = None
    forum_topic = False
    if isinstance(reply, types.MessageReplyHeader):
        forum_topic = bool(reply.forum_topic)
        if reply.reply_to_peer_id is None and reply.reply_to_msg_id:
            reply_to_id = int(reply.reply_to_msg_id)
        if reply.reply_to_top_id:
            reply_to_top_id = int(reply.reply_to_top_id)
    topic_root = service and isinstance(
        getattr(message, "action", None), types.MessageActionTopicCreate
    )
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
        reply_to_top_id=reply_to_top_id,
        topic_root=topic_root,
    )


@dataclass(frozen=True)
class SearchPage:
    """One per-peer search page: views plus Telegram's own completeness signals."""

    views: list[MessageView]
    exhausted: bool
    inexact: bool
    next_offset: int | None
    examined: int = 0  # entries Telegram returned, before dropping deleted or foreign ones


def _page_end(result: Any, limit: int, chat: tuple[str, int]) -> tuple[int | None, bool]:
    """``(next offset, or None on the last page; whether another chat's entry appeared)``.

    One copy of the stop rule, for history and search alike. A short page is
    *not* the end: Telethon 1.45.0 (``client/messages.py:213-225``) documents
    that channels withhold messages, so it stops only on an empty page, a
    non-slice ``messages.Messages`` (everything), or a highest id within the
    limit (ids start at 1). The offset comes from this chat's own entries
    only, so a foreign entry can never steer it past hits.
    """
    raw = list(result.messages)
    foreign = any(getattr(m, "peer_id", None) is not None and not _belongs(m, chat) for m in raw)
    own = [int(m.id) for m in raw if getattr(m, "peer_id", None) is None or _belongs(m, chat)]
    last = not own or isinstance(result, types.messages.Messages) or max(own) <= limit
    return (None if last else min(own)), foreign


def _belongs(message: Any, chat: tuple[str, int]) -> bool:
    try:
        return TelethonSession.identity_of(message.peer_id) == chat
    except GatewayError:
        return False
