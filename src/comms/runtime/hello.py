"""The admin socket's ``hello`` control request (comms v0.3 Task D29; A34).

It answers with the current security epoch — non-secret, and all the stdio proxy needs to mint
a lease the daemon will accept. It names no client, key or account.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from comms.core.security import security_epoch

__all__ = ["hello_handler"]


def hello_handler(conn: Any) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def hello(args: dict[str, Any]) -> dict[str, Any]:
        if args:
            raise ValueError("hello takes no arguments")
        return {"security_epoch": security_epoch(conn)}

    return hello
