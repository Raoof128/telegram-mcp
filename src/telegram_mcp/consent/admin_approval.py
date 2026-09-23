"""Live Touch ID approval for admin commands (design §3.8).

The signed challenge is the unchanged wire. An admin action is named
``admin.<command>``; the client is a per-install operator sentinel that is
never an ``mcp_clients`` row and so can never authenticate MCP; before login
the account is a per-install pre-login sentinel. Secrets (phone, code,
password) are removed before the request HMAC and never reach a display.

On approval the approver mints a one-time in-memory token that the admin
router's existing ``presence_verifier`` accepts exactly once.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
import sqlite3
import time
from collections.abc import Callable, Mapping
from typing import Any

from telegram_mcp.authority.cursors import project_scope_digest
from telegram_mcp.consent.broker import ConsentBroker, ConsentError
from telegram_mcp.consent.challenge import display_digest, jcs_dumps, synthetic_exposure_digest
from telegram_mcp.consent.prompter import PromptDenied, Prompter, PromptUnavailable
from telegram_mcp.storage.authority_view import load_security

__all__ = ["SECRET_ARGS", "AdminApprover", "sentinel_ref"]

SECRET_ARGS = frozenset({"phone", "code", "password"})
_REQUEST_DOMAIN = b"telegram-mcp-admin-request/v1\0"
_SECRET_DOMAIN = b"telegram-mcp-admin-secret/v1\0"
_SENTINEL_DOMAIN = b"telegram-mcp-sentinel/v1\0"


def sentinel_ref(privacy_key: bytes, label: str, prefix: str) -> str:
    """A stable, well-formed, never-registered opaque ref for this install."""
    digest = hmac.new(privacy_key, _SENTINEL_DOMAIN + label.encode(), hashlib.sha256).digest()
    return prefix + base64.b32encode(digest).decode("ascii")[:26].lower()


class AdminApprover:
    def __init__(
        self,
        broker: ConsentBroker,
        prompter: Prompter,
        *,
        privacy_key: bytes,
        conn: sqlite3.Connection,
        wait_s: float = 45.0,
        token_ttl_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._broker = broker
        self._prompter = prompter
        self._key = privacy_key
        self._conn = conn
        self._wait_s = wait_s
        self._ttl = token_ttl_s
        self._clock = clock
        self._tokens: dict[str, tuple[str, str, float]] = {}  # token -> (command, digest, expiry)

    def request_digest(self, command: str, args: Mapping[str, Any]) -> str:
        """Keyed digest of the exact request: secrets bind by keyed hash, never by value."""
        bound: dict[str, Any] = {}
        for name, value in args.items():
            if name == "presence":
                continue
            if name in SECRET_ARGS:
                raw = f"{name}\0{value}".encode()
                bound[name] = (
                    "hmac:" + hmac.new(self._key, _SECRET_DOMAIN + raw, hashlib.sha256).hexdigest()
                )
            else:
                bound[name] = value
        return hmac.new(
            self._key,
            _REQUEST_DOMAIN + jcs_dumps({"args": bound, "command": command}),
            hashlib.sha256,
        ).hexdigest()

    def _refs(self) -> tuple[str, str, int]:
        row = self._conn.execute(
            "SELECT principal_ref FROM principals ORDER BY id LIMIT 1"
        ).fetchone()
        principal = row[0] if row else sentinel_ref(self._key, "no-principal", "prn_")
        account_row = self._conn.execute(
            "SELECT a.account_ref, ps.policy_epoch FROM accounts a"
            " LEFT JOIN policy_state ps ON ps.account_id = a.id ORDER BY a.id LIMIT 1"
        ).fetchone()
        if account_row is None:
            return principal, sentinel_ref(self._key, "pre-login", "tga_"), 0
        return principal, account_row[0], int(account_row[1] or 0)

    async def approve(self, command: str, args: Mapping[str, Any]) -> str | None:
        tool = "admin." + command.replace(" ", "_").replace("-", "_")
        request_hmac = self.request_digest(command, args)
        principal, account, policy_epoch = self._refs()
        security_epoch, _locked = load_security(self._conn)
        display: dict[str, Any] = {
            "action_display": command,
            "client_display": "Operator (admin socket)",
            "peer_display": None,
            "project_display": [],
            "risk_class": "admin action",
        }
        try:
            handle = await self._broker.issue(
                tool=tool,
                request_hmac=request_hmac,
                principal=principal,
                client=sentinel_ref(self._key, "operator", "tcl_"),
                account=account,
                policy_epoch=policy_epoch,
                project_scope_digest=project_scope_digest(self._key, (), variant="list_projects"),
                security_epoch=security_epoch,
                display_digest=display_digest(display),
                exposure_snapshot_digest=synthetic_exposure_digest(),
            )
        except ConsentError:
            return None
        try:
            envelope = await self._prompter.prompt(
                handle=handle,
                challenge=self._broker.challenge_bytes(handle),
                signature=self._broker.daemon_signature(handle),
                display=display,
                timeout=self._wait_s,
            )
            await self._broker.consume(handle, envelope)
        except (PromptDenied, PromptUnavailable, ConsentError):
            self._broker.invalidate(handle)
            return None
        except asyncio.CancelledError:
            self._broker.invalidate(handle)
            raise
        token = secrets.token_urlsafe(24)
        self._tokens[token] = (command, request_hmac, self._clock() + self._ttl)
        return token

    def verify(self, proof: Any, command: str, args: Mapping[str, Any]) -> bool:
        """Consume a token; true only for the same command and exact request, unexpired."""
        token = proof.get("token") if isinstance(proof, dict) else None
        bound = self._tokens.pop(token, None) if isinstance(token, str) else None
        if bound is None:
            return False
        approved_command, approved_digest, expires = bound
        return (
            approved_command == command
            and self._clock() < expires
            and hmac.compare_digest(approved_digest, self.request_digest(command, args))
        )
