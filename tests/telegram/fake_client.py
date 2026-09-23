"""A Telethon-shaped fake: real TL request objects in, scripted TL results out.

It never touches the network. ``calls`` records every request by its
qualified name (the adapter's own ``qualified``), so tests assert what was,
and was not, sent. Login and status requests have realistic defaults;
``script`` overrides any of them.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

from telethon import errors, utils
from telethon import password as srp
from telethon.tl import types

from telegram_mcp.telegram.telethon_adapter import qualified

_ME = 4242


def srp_password() -> types.account.Password:
    """A valid 2FA challenge on Telethon's pinned known-good prime (g=3)."""
    body = re.search(
        r"good_prime = bytes\(\((.*?)\)\)", inspect.getsource(srp.check_prime_and_good), re.DOTALL
    ).group(1)
    prime = bytes(int(v, 16) for v in re.findall(r"0x[0-9A-Fa-f]{2}", body))
    algo = types.PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow(
        salt1=b"s1" * 8, salt2=b"s2" * 8, g=3, p=prime
    )
    b_pub = pow(3, 123456789, int.from_bytes(prime, "big")).to_bytes(256, "big")
    return types.account.Password(
        new_algo=algo,
        new_secure_algo=types.SecurePasswordKdfAlgoUnknown(),
        secure_random=b"\0" * 32,
        has_password=True,
        current_algo=algo,
        srp_B=b_pub,
        srp_id=42,
    )


class FakeSession:
    def __init__(self) -> None:
        self.entities: dict[int, Any] = {}
        self.dc = None

    def get_input_entity(self, marked_id: int) -> Any:
        if marked_id not in self.entities:
            raise ValueError("Could not find the input entity")
        return self.entities[marked_id]

    def set_dc(self, dc_id: int, ip: str, port: int) -> None:
        self.dc = (dc_id, ip, port)

    def remember(self, entity: Any) -> None:
        self.entities[utils.get_peer_id(entity)] = utils.get_input_peer(entity)


class FakeClient:
    def __init__(self, script: dict[str, Any] | None = None, *, authorized: bool = True) -> None:
        self.session = FakeSession()
        self.script = dict(script or {})
        self.calls: list[str] = []
        self.authorized = authorized
        self.connected = False
        self.logged_out = False
        self.me_id = _ME

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def log_out(self) -> None:  # must never be called
        self.logged_out = True

    async def _switch_dc(self, new_dc: int) -> None:
        self.calls.append(f"switch_dc:{new_dc}")

    def _default(self, name: str, request: Any) -> Any:
        me = types.User(id=self.me_id, access_hash=1, is_self=True, first_name="A")
        if name == "updates.GetStateRequest":
            if not self.authorized:
                raise errors.AuthKeyUnregisteredError(request=None)
            return types.updates.State(pts=1, qts=0, date=None, seq=0, unread_count=0)
        if name == "users.GetUsersRequest":
            return [me]
        if name == "auth.SendCodeRequest":
            return types.auth.SentCode(
                type=types.auth.SentCodeTypeApp(length=5), phone_code_hash="hash"
            )
        if name == "auth.SignInRequest":
            if request.phone_code == "needs-2fa":
                raise errors.SessionPasswordNeededError(request=None)
            if request.phone_code == "bad":
                raise errors.PhoneCodeInvalidError(request=None)
            self.authorized = True
            return types.auth.Authorization(user=me)
        if name == "account.GetPasswordRequest":
            return srp_password()
        if name == "auth.CheckPasswordRequest":
            self.authorized = True
            return types.auth.Authorization(user=me)
        return None

    async def __call__(self, request: Any) -> Any:
        name = qualified(request)
        self.calls.append(name)
        if name in self.script:
            outcome = self.script[name]
            if isinstance(outcome, list) and outcome and isinstance(outcome[0], BaseException):
                outcome = outcome.pop(0)  # a sequence of failures, then the default
                if not self.script[name]:
                    del self.script[name]
            if isinstance(outcome, BaseException):
                raise outcome
            if callable(outcome):
                outcome = outcome(request)
        else:
            outcome = self._default(name, request)
        # Telethon's _call feeds every result's users/chats to the entity
        # cache; the fake does the same, so cache-only peers resolve.
        for entity in [*getattr(outcome, "users", []), *getattr(outcome, "chats", [])]:
            self.session.remember(entity)
        return outcome
