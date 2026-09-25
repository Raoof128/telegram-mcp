"""Live credential proofs for ``credential set|rotate`` (D39-PRE E8; A13; R-E6).

Each proof takes the candidate value and raises on failure (``rotate_credential`` then
destroys the candidate and keeps the working credential). Provider proofs make one call through
the adapter's own pinned client over a one-value store holding the candidate:

- ``telegram-bot-token``: Bot API ``getMe`` answers ``ok`` with ``is_bot``;
- ``meta-access-token``: Graph ``GET /{phone_number_id}`` answers 200 (needs
  ``meta.phone_number_id`` in comms.json).

The two Meta webhook secrets have no whoami-style provider call (R-E6, owner wording): they are
checked locally here and confirmed in operation — the app secret by the first POST whose
``X-Hub-Signature-256`` verifies, the verify token by Meta's GET subscription challenge.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from comms.transports.telegram.bot.http import BotApi
from comms.transports.whatsapp.cloud.http import GraphApi

__all__ = ["CredentialProofFailed", "build_proofs"]

_APP_SECRET = re.compile(rb"^[0-9a-f]{32}$")
_VERIFY_TOKEN = re.compile(rb"^[\x21-\x7e]{32,128}$")  # printable, no whitespace, high entropy


class CredentialProofFailed(Exception):
    """The candidate did not prove itself. Fixed message; the value never appears."""


class _One:
    """A one-value secret store: the candidate, as version 1 of its purpose."""

    def __init__(self, purpose: str, value: bytes) -> None:
        self._purpose, self._value = purpose, value

    def get(self, item: str, version: int) -> bytes:
        if (item, version) != (self._purpose, 1):
            raise CredentialProofFailed("proof store misuse")
        return self._value

    def __repr__(self) -> str:
        return "_One(<redacted>)"


def build_proofs(
    *, phone_number_id: str | None, transport: Any = None
) -> Mapping[str, Callable[[bytes], None]]:
    """The proof per purpose; ``transport`` is the httpx transport seam for tests."""

    def bot(value: bytes) -> None:
        store: Any = _One("telegram-bot-token", value)
        api = BotApi(store, version=1, transport=transport)
        try:
            response = api.call("getMe", {})
        finally:
            api.close()
        envelope = response.envelope or {}
        raw = envelope.get("result")
        result: Mapping[str, Any] = raw if isinstance(raw, dict) else {}
        if (
            response.http_status != 200
            or envelope.get("ok") is not True
            or result.get("is_bot") is not True
        ):
            raise CredentialProofFailed("the bot token did not prove itself")

    def meta(value: bytes) -> None:
        if phone_number_id is None:
            raise CredentialProofFailed("set meta.phone_number_id in comms.json first")
        store: Any = _One("meta-access-token", value)
        api = GraphApi(store, version=1, phone_number_id=phone_number_id, transport=transport)
        try:
            response = api.phone_info()
        finally:
            api.close()
        if response.http_status != 200:
            raise CredentialProofFailed("the access token did not prove itself")

    def app_secret(value: bytes) -> None:
        if not _APP_SECRET.match(value):
            raise CredentialProofFailed("the app secret is 32 lowercase hex characters")

    def verify_token(value: bytes) -> None:
        if not _VERIFY_TOKEN.match(value):
            raise CredentialProofFailed("the verify token is 32 to 128 printable characters")

    return {
        "telegram-bot-token": bot,
        "meta-access-token": meta,
        "meta-app-secret": app_secret,
        "meta-webhook-secret": verify_token,
    }
