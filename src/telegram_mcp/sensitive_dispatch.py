"""Validated arguments + caller identity -> the coordinator -> an MCP result.

This module holds no retrieval object. It receives one callable, the
coordinator's ``disclose`` with the adapter already bound by composition, and
the only success it can serialise is a released ``DisclosureOutcome``: data
and receipt in one object (Phase-3 design, controlling statement).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from mcp import types

from telegram_mcp.results import error_result, success_result

__all__ = ["SensitiveDispatcher"]

_logger = logging.getLogger("telegram_mcp.sensitive")


class SensitiveDispatcher:
    """One disclosure in flight per client (spec §28 ceiling, lowered to 1).

    Budget buckets are per client and the approved exposure digest must match
    exactly at step 6 (§9.8, §23C.3). A sibling call from the same client moves
    that snapshot twice, once when it reserves and again when it commits, so
    concurrent same-client calls would re-prompt and then refuse by
    construction: executing revision 1 of this plan produced four prompts
    for three calls, all approved, and one refusal. Serialising per client
    removes the collision. Prompts are serial anyway, so the only overlap
    lost is one call's retrieval against the next call's prompt. Different
    clients never share a bucket and are not serialised against each other.
    """

    def __init__(
        self, disclose: Callable[..., Awaitable[Any]], *, max_response_bytes: int = 65536
    ) -> None:
        self._disclose = disclose
        self._max = max_response_bytes
        self._per_client: dict[int, asyncio.Lock] = {}

    async def call(
        self, name: str, validated: Mapping[str, Any], principal: Any
    ) -> types.CallToolResult:
        lock = self._per_client.setdefault(principal.client_id, asyncio.Lock())
        try:
            async with lock:
                outcome = await self._disclose(
                    tool_name=name, arguments=validated, principal=principal
                )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            _logger.exception("sensitive dispatch failed", extra={"tool": name})
            return error_result("INTERNAL_ERROR")
        if outcome.released:
            return success_result(name, outcome.data, outcome.meta, max_response_bytes=self._max)
        return error_result(outcome.error_code or "INTERNAL_ERROR")
