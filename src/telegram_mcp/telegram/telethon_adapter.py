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
from dataclasses import dataclass
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


_CURRENT: ContextVar[_Operation | None] = ContextVar("telegram_mcp_operation", default=None)


@contextlib.contextmanager
def _operation(name: str, budget: WorkBudget) -> Iterator[None]:
    token = _CURRENT.set(_Operation(OPERATIONS[name], budget))
    try:
        yield
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
                    # The client's own _call spends the budget: one copy of the charge,
                    # and it also covers anything the client might send on its own.
                    with _operation(operation, budget):
                        if not isinstance(self._client, _GatewayClient):
                            budget.spend()  # an injected test client has no _call of ours
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
