"""comms v0.3 Task B25: the comms-backup/v1 payload, its account binding, and the plaintext scan."""

import base64
import json
import os

import pytest

from comms.core.backup import age
from comms.core.backup.payload import account_binding, build_payload
from comms.core.delivery import freeze
from comms.core.installation import installation_ref
from comms.core.keys.secrets import FileSecretStore
from comms.core.keys.slots import KeySlotStore, bootstrap_comms_audit_keys
from tests.core import fakes
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW, person, ready

PHONE = "+61400000077"
BODY = "backup body canary 51c9"
PROVIDERS = {"telegram_user": "4242", "telegram_bot": "777000", "meta_waba": "10155"}


@pytest.fixture
def world(tmp_path):
    conn = fx.migrated(tmp_path)
    rcp, _ = person(conn, phone=PHONE)
    cmp = ready(conn, {"recipients": [rcp]}, body=BODY)
    freeze.send(conn, cmp, {"whatsapp": fakes.FakeWhatsApp(conn=conn)}, now=NOW)
    return conn, cmp


def _payload(conn):
    return build_payload(conn, PROVIDERS, now=NOW)


def test_payload_excludes_bodies_generations_payloads_attempts_provider_events(world):
    conn, cmp = world
    body = json.loads(_payload(conn))
    assert set(body) == {
        "schema",
        "binding",
        "installation_ref",
        "providers",
        "directory",
        "campaigns",
        "settings",
    }
    assert BODY not in json.dumps(body)
    (campaign,) = body["campaigns"]
    assert campaign["ref"] == cmp and "content" not in campaign
    for absent in (
        "generations",
        "delivery_jobs",
        "delivery_attempts",
        "provider_events",
        "audit_events",
        "key_slots",
    ):
        assert absent not in json.dumps(sorted(body)), absent


def test_plaintext_scan_every_secret_absent(world, tmp_path):
    conn, _cmp = world
    slots = KeySlotStore(tmp_path / "slots")
    bootstrap_comms_audit_keys(conn, slots, now=NOW)
    secrets = FileSecretStore(tmp_path / "secrets")
    planted = {
        name: os.urandom(24)
        for name in (
            "telegram-session",
            "telegram-bot-token",
            "meta-access-token",
            "meta-app-secret",
            "meta-webhook-secret",
            "comms-db-key",
            "tls-key",
        )
    }
    for name, value in planted.items():
        secrets.put(name, 1, value)
    material = [
        slots.read(p, v)
        for p in ("audit-chain-key", "audit-checkpoint-key")
        for v in slots.versions(p)
    ]
    payload = _payload(conn)
    for value in [*planted.values(), *material, fx.KEY]:
        for spelling in (
            value,
            value.hex().encode(),
            base64.b64encode(value),
            base64.urlsafe_b64encode(value).rstrip(b"="),
        ):
            assert spelling not in payload
    assert BODY.encode() not in payload


def test_identities_present_in_plaintext_payload_only(world):
    conn, _cmp = world
    payload = _payload(conn)
    assert PHONE.encode() in payload  # the directory is what a backup is for
    identity = age.generate_identity()
    assert PHONE.encode() not in age.encrypt(payload, age.recipient_of(identity))


def test_binding_changes_with_any_provider_identity(world):
    conn, _cmp = world
    ref = installation_ref(conn, now=NOW)
    base = account_binding(ref, PROVIDERS)
    assert json.loads(_payload(conn))["binding"] == base
    for provider in PROVIDERS:
        assert account_binding(ref, {**PROVIDERS, provider: "other"}) != base
    assert account_binding(ref, {k: v for k, v in PROVIDERS.items() if k != "meta_waba"}) != base
    assert account_binding("cin_" + "b" * 26, PROVIDERS) != base


def test_the_installation_ref_is_minted_once(world):
    conn, _cmp = world
    first = installation_ref(conn, now=NOW)
    assert first.startswith("cin_") and installation_ref(conn, now=NOW) == first
