"""The unprivileged stdio proxy (comms v0.3 Task D29; A34, A33, G17).

``comms mcp --stdio --client-seed <path>`` speaks MCP over stdio to one local client and
forwards each request to the daemon's ``/mcp``. It reads **its own** client seed once, from a
0600 file this user owns (``lease_format.read_helper``); a seed is never taken from argv or the
environment. For every request it mints a fresh ``cml1`` lease (at most 60 s) under the
current security epoch, which it learns from the daemon's non-secret ``hello`` and caches for
at most 5 s — so a six-hour session needs no refresh file, and an epoch bump is picked up
without a restart. It holds no provider, database or audit secret, and imports nothing that
could reach one (a closure test pins that).
"""

from __future__ import annotations

import argparse
import ipaddress
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import httpx
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from comms.core.auth.lease_format import HelperFileError, mint, read_helper

__all__ = ["EPOCH_TTL_S", "Proxy", "ProxyError", "build_server", "http_post", "parse_args", "serve"]

EPOCH_TTL_S = 5.0
PROTOCOL_VERSION = "2026-07-28"
_TIMEOUT_S = 30.0
Post = Callable[[str, str, dict[str, Any]], dict[str, Any]]


class ProxyError(Exception):
    """The proxy refused to start or to forward. Fixed messages, never a seed or a token."""


class Proxy:
    def __init__(
        self,
        seed_path: Path,
        *,
        hello: Callable[[], int],
        post: Post,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        try:
            self._cid, self._seed = read_helper(Path(seed_path))
        except HelperFileError as refused:
            raise ProxyError(str(refused)) from None
        self._hello, self._post, self._clock, self._now = hello, post, clock, now
        self._epoch: tuple[float, int] | None = None

    def __repr__(self) -> str:
        return "Proxy(<redacted>)"

    def _security_epoch(self) -> int:
        at = self._clock()
        if self._epoch is None or at - self._epoch[0] >= EPOCH_TTL_S:
            self._epoch = (at, int(self._hello()))
        return self._epoch[1]

    def lease(self) -> str:
        """A fresh lease for exactly one forwarded request."""
        return mint(self._seed, self._cid, self._security_epoch(), now=self._now())

    def list_tools(self) -> dict[str, Any]:
        return self._post(self.lease(), "tools/list", {})

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._post(self.lease(), "tools/call", {"name": name, "arguments": arguments})


def _loopback_origin(base_url: str) -> str:
    url = httpx.URL(base_url)
    try:
        loopback = ipaddress.ip_address(url.host).is_loopback
    except ValueError:
        loopback = url.host == "localhost"
    if url.scheme != "http" or not loopback or url.path not in ("", "/") or url.query:
        raise ProxyError("the daemon must be a loopback http origin")
    return f"http://{url.netloc.decode('ascii')}"


def http_post(base_url: str) -> Post:
    """Forward one JSON-RPC request to the daemon's ``/mcp``; the JSON-RPC result, or refused.
    The daemon is a loopback origin: the proxy never forwards a lease anywhere else."""
    base_url = _loopback_origin(base_url)

    def post(token: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Mcp-Protocol-Version": PROTOCOL_VERSION,
            "Mcp-Method": method,
            "Accept": "application/json, text/event-stream",
        }
        if method == "tools/call":
            headers["Mcp-Name"] = str(params.get("name", ""))
        meta = {
            "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": meta}}
        try:
            with httpx.Client(base_url=base_url, timeout=_TIMEOUT_S) as client:
                response = client.post("/mcp", headers=headers, json=body)
        except httpx.HTTPError:
            raise ProxyError("the daemon is not reachable") from None
        if response.status_code != 200:
            raise ProxyError("the daemon refused the request")
        result = response.json().get("result")
        if not isinstance(result, dict):
            raise ProxyError("the daemon's answer is malformed")
        return result

    return post


def parse_args(argv: list[str]) -> argparse.Namespace:
    """``--client-seed <path>`` is the only way to name the seed; there is no seed-value flag."""
    parser = argparse.ArgumentParser(prog="comms mcp --stdio", allow_abbrev=False)
    parser.add_argument("--client-seed", type=Path, required=True)
    parser.add_argument("--daemon", default="http://127.0.0.1:8765")
    return parser.parse_args(argv)


def build_server(proxy: Proxy) -> Server[Any]:
    """An MCP server over stdio whose every request is forwarded with a fresh lease."""

    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        result = await anyio.to_thread.run_sync(proxy.list_tools)
        return types.ListToolsResult.model_validate(result)

    async def on_call_tool(ctx: Any, params: Any) -> types.CallToolResult:
        arguments = {} if "arguments" not in params.model_fields_set else params.arguments
        result = await anyio.to_thread.run_sync(proxy.call_tool, params.name, arguments or {})
        return types.CallToolResult.model_validate(result)

    server: Server[Any] = Server(
        "comms", version="0.3", on_list_tools=on_list_tools, on_call_tool=on_call_tool
    )
    server.middleware = []
    return server


async def serve(proxy: Proxy) -> None:
    server = build_server(proxy)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
