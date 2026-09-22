import pytest
from pydantic import ValidationError

from telegram_mcp.config import DemoConfig, validate_environment


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "example.com", "localhost.evil"])
def test_demo_requires_literal_loopback(host):
    with pytest.raises(ValidationError):
        DemoConfig(host=host)


def test_demo_rejects_session_configuration():
    with pytest.raises(ValidationError):
        DemoConfig(session_path="/synthetic/forbidden.session")


def test_demo_rejects_credentials_without_echoing_them():
    with pytest.raises(ValueError) as exc:
        validate_environment({"TELEGRAM_API_HASH": "SYNTHETIC_SECRET_CANARY"})
    assert "SYNTHETIC_SECRET_CANARY" not in str(exc.value)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_loopback_accepted(host):
    assert DemoConfig(host=host).host in ("127.0.0.1", "::1")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_request_bytes": 65537},
        {"max_response_bytes": 65537},
        {"max_request_bytes": 0},
        {"port": 80},
        {"port": 70000},
        {"port": "8766"},
        {"mode": "direct_https"},
        {"mode": "tunnel"},
        {"mode": "local_dev"},
        {"unknown_field": 1},
    ],
)
def test_demo_bounds_and_profiles_rejected(kwargs):
    with pytest.raises(ValidationError):
        DemoConfig(**kwargs)


def test_otel_exporter_must_be_disabled():
    with pytest.raises(ValueError, match="telemetry export disabled"):
        validate_environment({"OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.invalid"})
    with pytest.raises(ValueError, match="telemetry export disabled"):
        validate_environment({"OTEL_TRACES_EXPORTER": "otlp"})
    # Explicitly disabled is accepted and never echoes values.
    validate_environment({"OTEL_TRACES_EXPORTER": "none", "OTEL_SDK_DISABLED": "true"})
    validate_environment({})
