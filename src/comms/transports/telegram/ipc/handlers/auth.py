"""``auth login`` in three approved steps, ``auth status``, ``auth logout-local``.

Spec §9.2: the phone number, code and 2FA password reach the daemon only over
the admin socket and are held for the single step that needs them. They are
never logged, never placed in a challenge or display, never returned.
``auth revoke-this-session`` (comms v0.3 B14) is registered only when a comms audit writer
is supplied: its ``started``/``finished`` events live on the comms chain.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from comms.core import timeutil
from comms.transports.telegram.storage.identity import bind_login
from comms.transports.telegram.telegram import admin_rpc
from comms.transports.telegram.telegram.deadline import Deadline
from comms.transports.telegram.telegram.errors import GatewayError

__all__ = ["auth_handlers"]

_PHONE = re.compile(r"\+?[0-9]{6,15}\Z")
_CODE = re.compile(r"[0-9A-Za-z-]{3,12}\Z")
_LOGIN_TTL_S = 600.0
_STEP_DEADLINE_S = 30.0


@dataclass
class _Login:
    phone: str
    stage: str  # "code" or "password"
    expires: float
    new_account: bool = False


def auth_handlers(
    conn: sqlite3.Connection,
    session: Any,
    *,
    clock: Callable[[], float] = time.monotonic,
    writer: Any = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]]:
    logins: dict[str, _Login] = {}

    def _take(handle: Any, stage: str) -> _Login:
        entry = logins.get(handle) if isinstance(handle, str) else None
        if entry is None or entry.expires <= clock() or entry.stage != stage:
            logins.pop(handle, None) if isinstance(handle, str) else None
            raise ValueError("unknown or expired login session")
        return entry

    async def _finish(handle: str) -> dict[str, Any]:
        entry = logins.pop(handle, None)
        user_id = await session.me(Deadline(_STEP_DEADLINE_S))
        try:
            ref = bind_login(
                conn,
                telegram_user_id=user_id,
                new_account=bool(entry and entry.new_account),
                now=timeutil.iso(now()),
            )
        except ValueError:
            await session.logout_local()  # never keep another account's session
            raise
        return {"authorized": True, "account_ref": ref}

    async def login(args: dict[str, Any]) -> dict[str, Any]:
        step = args.get("step")
        try:
            if step == "start":
                phone = args.get("phone")
                if not isinstance(phone, str) or _PHONE.fullmatch(phone) is None:
                    raise ValueError("phone must be digits, optionally with a leading +")
                new_account = args.get("new_account", False)
                if not isinstance(new_account, bool):
                    raise ValueError("new_account must be a boolean")
                await session.send_code(phone, Deadline(_STEP_DEADLINE_S))
                handle = secrets.token_urlsafe(18)
                logins[handle] = _Login(
                    phone=phone,
                    stage="code",
                    expires=clock() + _LOGIN_TTL_S,
                    new_account=new_account,
                )
                return {"login": handle, "next": "code"}
            if step == "code":
                entry = _take(args.get("login"), "code")
                code = args.get("code")
                if not isinstance(code, str) or _CODE.fullmatch(code) is None:
                    raise ValueError("code is malformed")
                outcome = await session.sign_in_code(entry.phone, code, Deadline(_STEP_DEADLINE_S))
                if outcome == "password_needed":
                    entry.stage = "password"
                    return {"next": "password", "login": args["login"]}
                return await _finish(args["login"])
            if step == "password":
                entry = _take(args.get("login"), "password")
                password = args.get("password")
                if not isinstance(password, str) or not 1 <= len(password) <= 256:
                    raise ValueError("password is malformed")
                await session.sign_in_password(password, Deadline(_STEP_DEADLINE_S))
                return await _finish(args["login"])
        except GatewayError as exc:
            # A fixed code, never the Telegram message, never the secret.
            raise ValueError(f"telegram refused the step: {exc.code}") from None
        raise ValueError("step must be start, code or password")

    async def status(args: dict[str, Any]) -> dict[str, Any]:
        try:
            authorized = await session.is_authorized(Deadline(_STEP_DEADLINE_S))
        except GatewayError as exc:  # unreachable is an answer, not a handler failure
            return {"authorized": False, "revoked": bool(session.revoked), "state": exc.code}
        return {"authorized": bool(authorized), "revoked": bool(session.revoked)}

    async def logout_local(args: dict[str, Any]) -> dict[str, Any]:
        await session.logout_local()
        logins.clear()
        return {"logged_out_locally": True}

    async def revoke(args: dict[str, Any]) -> dict[str, Any]:
        if set(args):
            raise ValueError("unknown argument")
        outcome = await admin_rpc.revoke_session(conn, writer, session, now=now())
        logins.clear()
        return {"revoked": True, "remote_revoke": outcome}

    handlers: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = {
        "auth login": login,
        "auth status": status,
        "auth logout-local": logout_local,
    }
    if writer is not None:
        handlers["auth revoke-this-session"] = revoke
    return handlers
