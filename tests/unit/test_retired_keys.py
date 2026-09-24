"""Retired consent keys are inactive, never erased (5b-3 design §2.5, amendment A3)."""

from comms.transports.telegram.keys.registry import KEY_REGISTRY
from comms.transports.telegram.keys.store import FILE_BACKED_KEYS, provision_missing

RETIRED = {"challenge-key", "agent-approval-key", "agent-transport-key"}


def test_the_three_consent_keys_stay_in_the_registry_as_retired():
    assert {name for name, spec in KEY_REGISTRY.items() if spec.retired} == RETIRED


def test_retired_keys_are_never_provisioned_or_inventoried(tmp_path):
    created = provision_missing(tmp_path / "keys", phases=(2, 3))
    assert not RETIRED & set(created)
    assert not (tmp_path / "keys" / "challenge-key").exists()
    assert not RETIRED & set(FILE_BACKED_KEYS)
