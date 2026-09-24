"""The one conformance registry: each adapter module registers its cases on import."""

from tests.conformance.runner import Registry

REGISTRY = Registry()

# Adapter case modules register on import; keep this list in ADAPTER_CONTRACTS order.
from tests.conformance import telegram_bot as _telegram_bot  # noqa: F401
from tests.conformance import telegram_user as _telegram_user  # noqa: F401
from tests.conformance import whatsapp_cloud as _whatsapp_cloud  # noqa: F401
from tests.conformance import whatsapp_webhooks as _whatsapp_webhooks  # noqa: F401
