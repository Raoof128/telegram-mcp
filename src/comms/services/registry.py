"""The closed service registry: ``"<service>.<method>"`` names to callables (A37)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

__all__ = ["ServiceRegistry", "ServiceRegistryError"]

_NAME = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\Z")


class ServiceRegistryError(Exception):
    """A registry operation was refused. Fixed messages."""


class ServiceRegistry:
    def __init__(self) -> None:
        self._services: dict[str, Callable[..., Any]] = {}

    def register(self, name: str, fn: Callable[..., Any]) -> None:
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ServiceRegistryError("invalid service name")
        if name in self._services:
            raise ServiceRegistryError("service already registered")
        self._services[name] = fn

    def resolve(self, name: str) -> Callable[..., Any]:
        try:
            return self._services[name]
        except KeyError:
            raise ServiceRegistryError("unknown service") from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._services))
