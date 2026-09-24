"""The catalog's tool families, in P order (comms v0.3 D18–D24)."""

from __future__ import annotations

from comms.mcp.spec import ToolSpec
from comms.mcp.tools.context import CONTEXT_TOOLS

__all__ = ["FAMILIES"]

FAMILIES: tuple[tuple[ToolSpec, ...], ...] = (CONTEXT_TOOLS,)
