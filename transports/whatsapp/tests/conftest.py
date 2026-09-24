"""Test-wide safety net: no test may touch the operator's real vault.

A test that injected a temporary Paths still created ~/.whatsvault, because
cmd_init resolved the layout from the environment rather than from the context it
was handed. It wrote 300KB of SQLCipher databases into the real home with keys
held in a MemoryKeyStore that vanished at the end of the run — leaving behind
ciphertext nobody could ever read, and which then blocked a genuine `init` by
tripping the stranded-database check.

Nothing failed at the time. The suite stayed green and the damage was outside the
tree. This autouse fixture makes that class of escape loud instead: it points
$WHATSVAULT_HOME at a per-test tmp directory so an env-resolved path lands
somewhere harmless, and fails any test that leaves a real one behind.
"""

import os
from pathlib import Path

import pytest

REAL_HOME = Path(os.path.expanduser("~")) / ".whatsvault"


@pytest.fixture(autouse=True)
def _never_touch_the_real_vault(tmp_path, monkeypatch):
    existed = REAL_HOME.exists()
    monkeypatch.setenv("WHATSVAULT_HOME", str(tmp_path / "wv-home"))
    yield
    if REAL_HOME.exists() and not existed:
        # Leave it in place: deleting a vault is exactly the operation this
        # project refuses to do automatically. Name it and fail.
        pytest.fail(
            f"test created {REAL_HOME} — a test must never write to the real vault. "
            "It is still on disk; inspect and remove it by hand."
        )
