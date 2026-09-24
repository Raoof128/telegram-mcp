"""The one conformance registry: each adapter module registers its cases on import."""

from tests.conformance.runner import Registry

REGISTRY = Registry()

# Adapter case modules register on import; keep this list in ADAPTER_CONTRACTS order.
from tests.conformance import telegram_bot as _telegram_bot  # noqa: F401
