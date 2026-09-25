"""comms v0.3 Task D34: the Comms runtime composition — one place builds the running surface."""

import os
from datetime import UTC, datetime

import pytest

from comms.core import refs
from comms.core.keys import rotate as rot
from comms.core.keys.secrets import FileSecretStore
from comms.mcp.oauth.server import OAuthSettings
from comms.runtime.adapters import AdapterSettings, build_adapters
from comms.runtime.comms_runtime import RemoteConfig, build_comms_runtime
from comms.transports.telegram.ipc.admin import AdminRouter
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW


class Archive:
    def ingest(self, raw):
        return None


@pytest.fixture
def runtime(tmp_path):
    w = comms_world(tmp_path)
    for purpose in ("campaign-commit-key", "cursor-key", "oauth-signing-key"):
        rot.rotate(
            w["writer"], w["store"], purpose, material=os.urandom(32), prove=lambda m: None, now=NOW
        )
    adapters = build_adapters(
        w["conn"],
        FileSecretStore(tmp_path / "secrets"),
        AdapterSettings(),
        clock=lambda: datetime.now(UTC),
        monotonic=lambda: 0.0,
        archive=Archive(),
    )
    remote = RemoteConfig(
        settings=OAuthSettings(
            issuer="https://comms.example.org",
            resource="https://comms.example.org/mcp",
            client_id="remote",
            redirect_uris=("https://claude.ai/cb",),
            owner="owner",
        ),
        client_ref=refs.mint("client"),
        port=8767,
    )
    built = build_comms_runtime(
        w["conn"],
        w["writer"],
        w["store"],
        adapters,
        clock=lambda: datetime.now(UTC),
        monotonic=lambda: 0.0,
        host="127.0.0.1",
        local_port=8765,
        remote=remote,
    )
    router = AdminRouter(built.admin_handlers, control_handlers=built.control_handlers)
    return {"w": w, "built": built, "router": router, "tmp": tmp_path}


def test_the_admin_socket_reaches_the_dispatcher(runtime):
    response = runtime["router"].dispatch(
        {
            "cmd": "tool call",
            "args": {
                "tool": "comms_location_create",
                "arguments": {"name": "Parramatta", "request_id": refs.mint("request")},
            },
        }
    )
    assert response["ok"] is True and response["data"]["error"] is None
    assert response["data"]["result"]["location"].startswith("loc_")


def test_hello_and_client_add_and_oauth_approve(runtime):
    assert runtime["router"].dispatch({"control": "hello"})["data"] == {"security_epoch": 1}
    (runtime["tmp"] / "h").mkdir(mode=0o700)
    added = runtime["router"].dispatch(
        {
            "cmd": "operator",
            "args": {
                "command": ["client", "add"],
                "name": "claude-code",
                "helper_path": str(runtime["tmp"] / "h" / "seed"),
            },
        }
    )
    assert added["ok"] is True and added["data"]["client"].startswith("cli_")
    approved = runtime["router"].dispatch(
        {"cmd": "operator", "args": {"command": ["oauth", "approve"]}}
    )
    assert approved["ok"] is True and len(approved["data"]["owner_code"]) >= 16


def test_an_unwired_operator_command_is_refused_not_faked(runtime):
    refused = runtime["router"].dispatch(
        {"cmd": "operator", "args": {"command": ["retention", "run"]}}
    )
    assert refused["ok"] is False and refused["code"] == "MALFORMED_REQUEST"


def test_no_credentials_means_no_telegram_actor_and_a_clear_refusal(runtime):
    response = runtime["router"].dispatch(
        {
            "cmd": "tool call",
            "args": {"tool": "comms_group_get", "arguments": {"group": "grp_" + "a" * 26}},
        }
    )
    assert response["ok"] is True and response["data"]["error"] == "NOT_FOUND"
    assert runtime["built"].services.actors == ()


def test_listeners_are_three_separate_apps(runtime):
    listeners = runtime["built"].listeners
    assert listeners.local is not None and listeners.remote is not None
    assert listeners.local is not listeners.remote
    assert listeners.webhook is None  # no webhook secret configured: no webhook listener
