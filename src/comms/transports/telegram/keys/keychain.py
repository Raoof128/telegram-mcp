"""Read the Telegram ``api_hash`` from the login Keychain (design D2, G14).

Development-only custody, recorded as a deviation from spec §9.1: any process
of the same user can request this item through the Keychain ACL. Production
moves the secret into the ``telegram-mcpd`` store. The value never enters
argv, the environment or a log line; errors carry fixed text only.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from typing import Any

__all__ = ["KeychainError", "read_api_hash"]

_API_HASH = re.compile(r"[0-9a-f]{32}\Z")


class KeychainError(Exception):
    """The api_hash is missing or malformed. Never carries the value."""


def read_api_hash(
    *,
    service: str = "telegram-mcp",
    account: str = "api_hash",
    runner: Callable[..., Any] = subprocess.run,
) -> str:
    done = runner(
        ["/usr/bin/security", "find-generic-password", "-s", service, "-a", account, "-w"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if done.returncode != 0:
        raise KeychainError("api_hash is not in the login Keychain")
    value = (done.stdout or "").strip()
    if _API_HASH.fullmatch(value) is None:
        raise KeychainError("api_hash in the Keychain is malformed")
    return value
