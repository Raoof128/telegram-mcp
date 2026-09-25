"""D39-PRE Task E3: the daemon settings (R-E5) — 0600, loopback-only, strict, no secrets."""

import json

import pytest

from comms.runtime.settings import HOST, DaemonSettings, SettingsError, load_settings

CLIENT = "cli_" + "a" * 26
AGE = "age1" + "q" * 58


@pytest.fixture
def root(tmp_path):
    d = tmp_path / "comms"
    d.mkdir(mode=0o700)
    return d


def _write(root, body, mode=0o600):
    path = root / "comms.json"
    path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    path.chmod(mode)
    return path


def test_absent_file_gives_loopback_defaults(root):
    settings = load_settings(root / "comms.json")
    assert settings == DaemonSettings()
    assert settings.host == HOST and settings.remote is None and settings.webhook_port is None


def test_a_full_file_loads(root):
    path = _write(root, {
        "host": "127.0.0.1", "local_port": 9001, "telegram_api_id": 12345,
        "retention": {"campaign_body_days": 90}, "webhook_port": 9003,
        "telegram_delivery_actor": "telegram_user",
        "meta": {"phone_number_id": "1234567890", "waba_id": "987"},
        "backup_recipient": AGE,
        "remote": {"issuer": "https://comms.example.org", "port": 9002, "client": CLIENT,
                   "client_id": "chatgpt", "redirect_uris": ["https://chatgpt.com/cb"],
                   "owner": "owner"},
    })  # fmt: skip
    s = load_settings(path)
    assert (s.local_port, s.webhook_port, s.backup_recipient) == (9001, 9003, AGE)
    assert s.adapter.telegram_delivery_actor == "telegram_user" and s.telegram_api_id == 12345
    assert s.retention_days == {"campaign_body_days": 90, "identity_retention_days": 365}
    assert s.adapter.meta_phone_number_id == "1234567890"
    assert s.remote.client == CLIENT and s.remote.port == 9002
    assert s.remote.oauth.resource == "https://comms.example.org/mcp"


@pytest.mark.parametrize(
    "body, message",
    [
        ({"host": "0.0.0.0"}, "loopback only"),
        ('{"local_port": 9001, "local_port": 9002}', "not valid JSON"),
        ({"surprise": 1}, "unknown key"),
        ({"meta": {"access_token": "x"}}, "no secrets"),
        (
            {
                "remote": {
                    "issuer": "http://comms.example.org",
                    "port": 9002,
                    "client": CLIENT,
                    "client_id": "c",
                    "redirect_uris": ["https://x/cb"],
                    "owner": "o",
                }
            },
            "https",
        ),
        ({"remote": {"issuer": "https://comms.example.org"}}, "every field"),
        ({"local_port": 80}, "1024"),
        ({"local_port": True}, "1024"),
        ({"telegram_delivery_actor": "whatsapp_cloud"}, "telegram_delivery_actor"),
        ({"backup_recipient": "not-age"}, "backup_recipient"),
        ({"telegram_api_id": "12345"}, "telegram_api_id"),
        ({"retention": {"campaign_body_days": 0}}, "1 to 3650"),
        ({"retention": {"audit_events_days": 30}}, "unknown key in retention"),
    ],
)
def test_refusals(root, body, message):
    with pytest.raises(SettingsError, match=message):
        load_settings(_write(root, body))


def test_a_wide_file_mode_is_refused(root):
    with pytest.raises(SettingsError, match="0600"):
        load_settings(_write(root, {}, mode=0o644))


def test_a_wide_directory_is_refused(root):
    path = _write(root, {})
    root.chmod(0o755)
    with pytest.raises(SettingsError, match="0700"):
        load_settings(path)


def test_a_symlink_is_refused(root, tmp_path):
    real = _write(root, {})
    link = root / "link.json"
    link.symlink_to(real)
    with pytest.raises(SettingsError, match="regular file"):
        load_settings(link)
