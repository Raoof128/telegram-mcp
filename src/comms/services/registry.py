"""The closed service registry: ``"<service>.<method>"`` names to callables (A37).

``call`` is the one boundary where a service failure becomes a fixed ``CommsError``: a
``CommsError`` passes through, a degraded audit trail is ``AUDIT_INTEGRITY_DEGRADED``, and any
other exception is ``INTERNAL_ERROR`` with nothing of it attached (D2).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from comms.core.audit.integrity import AuditIntegrityDegraded
from comms.services.errors import CommsError

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

    def call(self, name: str, **kwargs: Any) -> Any:
        fn = self._services.get(name)
        if fn is None:
            raise CommsError("TOOL_NOT_FOUND")
        code: str | None = None
        try:
            return fn(**kwargs)
        except CommsError:
            raise
        except AuditIntegrityDegraded:
            code = "AUDIT_INTEGRITY_DEGRADED"
        except Exception:  # noqa: BLE001 -- nothing of an unexpected failure leaves the boundary
            code = "INTERNAL_ERROR"
        raise CommsError(code)  # outside the handler: no chained exception
