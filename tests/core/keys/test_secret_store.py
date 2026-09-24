"""comms v0.3 Task B11: the daemon-owned 0600 versioned secret store (A13 rev 2, N7, R-B11)."""

import ast
import logging
import os
import stat
from pathlib import Path

import pytest

from comms.core.keys.secrets import FileSecretStore, SecretStoreError

ROOT = Path(__file__).resolve().parents[3]
# The legacy Telegram api_hash reader: the one Keychain caller, replaced in Part C (R-B11).
LEGACY_KEYCHAIN = ROOT / "src" / "comms" / "transports" / "telegram" / "keys" / "keychain.py"
VALUE = b"bot-token-\x00\xff-canary-7c1e"


def test_files_are_0600_in_a_0700_dir_owned_by_the_process_uid(tmp_path):
    store = FileSecretStore(tmp_path / "secrets")
    store.put("telegram-bot-token", 1, VALUE)
    root = tmp_path / "secrets"
    item_dir = root / "telegram-bot-token"
    file = item_dir / "1"
    for directory in (root, item_dir):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert directory.stat().st_uid == os.getuid()
    assert stat.S_IMODE(file.stat().st_mode) == 0o600 and file.stat().st_uid == os.getuid()
    assert store.get("telegram-bot-token", 1) == VALUE
    assert store.versions("telegram-bot-token") == [1]


@pytest.mark.parametrize("mode", [0o750, 0o705, 0o770])
def test_group_or_other_readable_dir_refused(tmp_path, mode):
    root = tmp_path / "secrets"
    root.mkdir(mode=0o700)
    os.chmod(root, mode)
    with pytest.raises(SecretStoreError, match="permissions"):
        FileSecretStore(root)


def test_a_loosened_file_is_refused_on_read(tmp_path):
    store = FileSecretStore(tmp_path / "secrets")
    store.put("meta-app-secret", 1, VALUE)
    os.chmod(tmp_path / "secrets" / "meta-app-secret" / "1", 0o644)
    with pytest.raises(SecretStoreError, match="permissions"):
        store.get("meta-app-secret", 1)


def test_existing_version_never_overwritten(tmp_path):
    store = FileSecretStore(tmp_path / "secrets")
    store.put("meta-access-token", 1, VALUE)
    with pytest.raises(SecretStoreError, match="exists"):
        store.put("meta-access-token", 1, b"other")
    assert store.get("meta-access-token", 1) == VALUE
    store.delete("meta-access-token", 1)
    assert store.versions("meta-access-token") == []


def test_unknown_items_and_versions_are_refused(tmp_path):
    store = FileSecretStore(tmp_path / "secrets")
    for item in ("../escape", "made-up", "audit-chain-key"):
        with pytest.raises(SecretStoreError, match="unknown secret"):
            store.put(item, 1, VALUE)
    with pytest.raises(SecretStoreError, match="missing"):
        store.get("telegram-session", 1)


def test_values_never_in_repr_errors_or_logs(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    store = FileSecretStore(tmp_path / "secrets")
    store.put("meta-webhook-secret", 1, VALUE)
    errors = []
    for attempt in (
        lambda: store.put("meta-webhook-secret", 1, VALUE),
        lambda: store.get("meta-webhook-secret", 9),
    ):
        with pytest.raises(SecretStoreError) as raised:
            attempt()
        errors.append(str(raised.value))
    text = " ".join([repr(store), *errors, caplog.text])
    assert VALUE.decode("latin-1") not in text and VALUE.hex() not in text and "canary" not in text


def _calls_security(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and node.value in ("security", "/usr/bin/security"):
            return True
    return False


def test_no_module_invokes_the_security_cli():
    offenders = {p for p in (ROOT / "src").rglob("*.py") if _calls_security(p)}
    assert offenders <= {LEGACY_KEYCHAIN}, sorted(offenders)
    assert _calls_security(LEGACY_KEYCHAIN), "the pinned exception is stale; drop it (Part C)"
