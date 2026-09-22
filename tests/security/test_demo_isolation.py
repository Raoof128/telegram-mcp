"""Demo isolation: no Telegram/session/network surface in the Phase-1 binary."""

import sys

from telegram_mcp import config as config_module


def test_no_telethon_import():
    assert "telethon" not in sys.modules
    assert not hasattr(config_module, "Telethon")
    import telegram_mcp.config

    assert "telethon" not in telegram_mcp.config.__dict__.get("__doc__", "")


def test_no_session_or_database_access(monkeypatch):
    import sqlite3

    def _forbidden(*args, **kwargs):
        raise AssertionError("session/database access forbidden in safe_demo")

    monkeypatch.setattr(sqlite3, "connect", _forbidden)
    # Config construction itself must not touch the network or filesystem.
    from telegram_mcp.config import DemoConfig

    DemoConfig()


def test_only_limited_profiles_exist():
    from telegram_mcp.config import DemoConfig

    fields = set(DemoConfig.model_fields)
    assert fields == {"mode", "host", "port", "max_request_bytes", "max_response_bytes"}
    assert DemoConfig.model_fields["mode"].annotation is not None
