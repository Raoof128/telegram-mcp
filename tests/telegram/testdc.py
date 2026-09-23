"""Test DC configuration, verified 2026-09-23 against Telethon's "Test Servers" page.

Fresh sessions only; ``session.set_dc(dc, ip, 80)``; numbers 99966XYYYY
where X is the DC id; the login code is X repeated five times (six if five
fails). Test accounts are public: anyone can log in to them, so fixtures
carry nothing sensitive, and every run uses a fresh random marker.

Environment (non-secret): TG_TESTDC_API_ID, TG_TESTDC_DC, TG_TESTDC_IP.
The api_hash comes from the login Keychain, like the daemon's.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

from telegram_mcp.keys.keychain import read_api_hash


@dataclass(frozen=True)
class TestDC:
    api_id: int
    api_hash: str
    dc: int
    ip: str

    def phone(self) -> str:
        return f"99966{self.dc}{secrets.randbelow(10_000):04d}"

    def code(self, length: int = 5) -> str:
        return str(self.dc) * length


def load() -> TestDC:
    return TestDC(
        api_id=int(os.environ["TG_TESTDC_API_ID"]),
        api_hash=read_api_hash(),
        dc=int(os.environ["TG_TESTDC_DC"]),
        ip=os.environ["TG_TESTDC_IP"],
    )
