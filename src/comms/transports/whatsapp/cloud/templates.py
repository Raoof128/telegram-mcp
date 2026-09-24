"""The template catalogue the WhatsApp transport checks at claim time (comms v0.3 C23; A23).

An in-memory map of ``(name, language) -> (status, schema version)``, replaced whole by the
template sync outside any transaction, so ``still_valid`` stays pure (S3): it reads memory, not
the network or a database. Only an ``APPROVED`` template of the frozen schema version is
available; anything else, or an absent entry, is not.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

__all__ = ["TemplateCatalog"]


class TemplateCatalog:
    def __init__(self) -> None:
        self._entries: Mapping[tuple[str, str], tuple[str, int]] = MappingProxyType({})

    def replace(self, entries: Mapping[tuple[str, str], tuple[str, int]]) -> None:
        self._entries = MappingProxyType(dict(entries))

    def available(self, name: str, language: str, schema_version: int) -> bool:
        return self._entries.get((name, language)) == ("APPROVED", schema_version)
