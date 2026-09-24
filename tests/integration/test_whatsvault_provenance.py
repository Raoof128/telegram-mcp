"""comms design rev 2 §3.1 and §3.3: the imported tree is the measured tree.

The tree-hash tests hold only while the subtree is untouched. They are
retired, with a ledger note, at 5b-2's first deliberate seam change (§3.4).
"""

import importlib.metadata
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "transports/whatsapp"
SOURCE_URL = "https://github.com/Raoof128/whatsvault.git"
SOURCE_COMMIT = "b6fd51ac83d91018cb2d7fe37f0bce669c7317aa"
SOURCE_TREE = "abdbcdc775ff62c4eab6d0527e7081128c133ba7"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)


def test_the_subtree_is_the_measured_tree():
    committed = _git("rev-parse", f"HEAD:{PREFIX}")
    assert committed.returncode == 0, committed.stderr
    assert committed.stdout.strip() == SOURCE_TREE
    dirty = _git("status", "--porcelain", "--", PREFIX)
    assert dirty.stdout == "", "the subtree must be untouched in 5b-2"


def test_the_source_commit_and_its_history_are_present():
    assert _git("merge-base", "--is-ancestor", SOURCE_COMMIT, "HEAD").returncode == 0
    count = _git("rev-list", "--count", SOURCE_COMMIT)
    assert int(count.stdout.strip()) > 1  # full history, not a squash


def test_whatsvault_imports_from_the_subtree():
    import whatsvault

    location = Path(whatsvault.__file__).resolve()
    assert location.is_relative_to(ROOT / PREFIX / "src" / "whatsvault"), location


def test_the_native_substrate_is_recorded():
    import sqlcipher3

    assert importlib.metadata.version("sqlcipher3") == "0.6.2"
    cipher = sqlcipher3.connect(":memory:").execute("PRAGMA cipher_version").fetchone()[0]
    assert cipher.startswith("4.12.0"), cipher
    if sys.platform == "darwin" and platform.machine() == "arm64":
        dist = importlib.metadata.distribution("sqlcipher3")
        wheel = (Path(dist._path) / "WHEEL").read_text()  # type: ignore[attr-defined]
        assert "cp312-cp312-macosx_11_0_arm64" in wheel
