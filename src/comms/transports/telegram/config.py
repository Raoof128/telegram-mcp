"""Restricted Phase-1 profile: safe_demo on literal loopback only."""

from collections.abc import Mapping
from ipaddress import ip_address
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DemoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    mode: Literal["safe_demo"] = "safe_demo"
    host: str = "127.0.0.1"
    port: int = Field(default=8766, ge=1024, le=65535)
    max_request_bytes: int = Field(default=65536, ge=1024, le=65536)
    max_response_bytes: int = Field(default=65536, ge=1024, le=65536)

    @field_validator("host")
    @classmethod
    def literal_loopback(cls, value: str) -> str:
        address = ip_address(value)
        if not address.is_loopback:
            raise ValueError("literal loopback required")
        return str(address)


_CREDENTIAL_KEYS = {
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "TELEGRAM_SESSION",
    "TELEGRAM_SESSION_PATH",
}


def validate_environment(environ: Mapping[str, str]) -> None:
    if _CREDENTIAL_KEYS.intersection(environ):
        raise ValueError("safe_demo rejects Telegram credential configuration")
    for key in sorted(environ):
        if not key.upper().startswith("OTEL_"):
            continue
        value = environ[key]
        if key.upper() == "OTEL_SDK_DISABLED":
            if value.lower() not in ("true", "1"):
                raise ValueError("safe_demo requires telemetry export disabled")
            continue
        if value not in ("", "none", "noop"):
            raise ValueError("safe_demo requires telemetry export disabled")
