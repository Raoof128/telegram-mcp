"""``ToolSpec``: one MCP tool's static declaration (comms v0.3 A29). See ``catalog``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["ToolSpec"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool
    requires_request_id: bool
    failure_modes: tuple[str, ...]
    service: str  # "<service>.<method>" in the ServiceRegistry
