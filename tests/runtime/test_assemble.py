"""D39-PRE Task E4: one composition root over a real provisioned state, fake adapters injected."""

import pytest

from comms.core.providers.capability import CapabilityState as S
from comms.mcp.catalog import TOOL_CATALOG
from comms.mcp.dispatch import AuthenticatedClient
from comms.mcp.oauth.server import OAuthSettings
from comms.runtime.adapters import Adapters
from comms.runtime.assemble import assemble_runtime
from comms.runtime.paths import CommsPaths
from comms.runtime.provision import provision
from comms.runtime.settings import DaemonSettings, RemoteSettings
from comms.runtime.state import open_comms_state
from tests.runtime.test_provision import NOW
from tests.services.group_fixtures import Provider

ACTORS = ("telegram_bot", "telegram_user", "whatsapp_cloud")


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "run").mkdir(mode=0o700)
    paths = CommsPaths(tmp_path / "state")
    provision(paths, now=NOW, runtime_dir=tmp_path / "run")
    s = open_comms_state(paths, clock=lambda: NOW)
    yield s
    s.conn.close()


def _fakes(seen):
    def factory(state, settings):
        seen.append((state, settings))
        return Adapters(capability={a: Provider(S.AVAILABLE) for a in ACTORS})

    return factory


def _assemble(state, settings, seen):
    return assemble_runtime(
        state, settings, adapters_factory=_fakes(seen), clock=lambda: NOW, monotonic=lambda: 0.0
    )


def test_the_root_serves_the_whole_catalog_over_the_injected_adapters(state):
    seen = []
    built = _assemble(state, DaemonSettings(), seen)
    assert seen == [(state, DaemonSettings())]  # the factory gets the state and the settings
    client = AuthenticatedClient(client_ref="cli_" + "a" * 26, auth_kind="cml1")
    dispatcher = built.runtime.dispatcher
    assert dispatcher.call(client, "comms_capability_list", {}).error_code is None
    assert dispatcher.call(client, "comms_nope", {}).error_code == "TOOL_NOT_FOUND"
    for spec in TOOL_CATALOG:  # every catalog tool reaches a registered service
        assert dispatcher.call(client, spec.name, {}).error_code != "TOOL_NOT_FOUND", spec.name
    assert built.runtime.listeners.remote is None  # no remote settings, no remote listener
    assert built.runtime.listeners.webhook is None  # no webhook ingress among the adapters
    assert repr(built) == "Assembled(<redacted>)"


def test_remote_settings_build_the_remote_listener(state):
    remote = RemoteSettings(
        oauth=OAuthSettings(issuer="https://comms.example.org", resource="https://comms.example.org/mcp",
                            client_id="chatgpt", redirect_uris=("https://chatgpt.com/cb",), owner="owner"),
        client="cli_" + "a" * 26, port=9002,
    )  # fmt: skip
    built = _assemble(state, DaemonSettings(remote=remote), [])
    assert built.runtime.listeners.remote is not None and built.runtime.oauth is not None
