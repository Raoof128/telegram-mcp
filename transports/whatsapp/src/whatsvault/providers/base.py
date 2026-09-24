"""Provider Protocol. The real MetaCloudProvider is Phase-0-gated; FakeMeta drives tests."""

from typing import Protocol


class WhatsAppProvider(Protocol):
    def send_text(self, *, phone_number_id, recipient_wa_id, body) -> dict: ...
    def health(self) -> dict: ...
