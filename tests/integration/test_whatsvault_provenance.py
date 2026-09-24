"""comms design rev 2 §3.1 and §3.3: the imported tree is the measured tree.

Until comms v0.3 the subtree had to be byte-identical to the measured tree. The first
deliberate change (R-A16, the retired MCP app) replaced that pin with a stronger one:
every divergence from the measured tree must be listed below under the ruling that
sanctioned it, so an unruled edit still fails.
"""

import importlib.metadata
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "transports/whatsapp"
SOURCE_URL = "https://github.com/Raoof128/whatsvault.git"
SOURCE_COMMIT = "b6fd51ac83d91018cb2d7fe37f0bce669c7317aa"
SOURCE_TREE = "abdbcdc775ff62c4eab6d0527e7081128c133ba7"
# Every path that differs from SOURCE_TREE, under the ruling that sanctioned it.
RULED_REMOVED = {
    "R-A16": {
        "apps/launchd/mcp.plist",
        "apps/mcp/__init__.py",
        "apps/mcp/server.py",
        "tests/adversarial/test_injection_reads.py",
        "tests/adversarial/test_oauth_http.py",
        "tests/adversarial/test_redteam_mcp.py",
        "tests/test_mcp_surface.py",
        "tests/test_mcp_transport_live.py",
        "tests/test_mcp_transport_tools_live.py",
    },
}
RULED_MODIFIED = {
    "R-A16": {
        ".env.example",
        ".github/CODEOWNERS",
        ".github/workflows/ci.yml",
        "README.md",
        "docs/MCP.md",
        "docs/USAGE.md",
        "pyproject.toml",
        "tests/test_cli_entry_bootstrap.py",
        "tests/test_daemon_entrypoints.py",
        "tests/test_docs_match_code.py",
        "tests/test_mcp_ops.py",
        "tests/test_ops_launchd.py",
        "tests/test_ops_launchd_runnable.py",
    },
    # comms v0.3 C27: FakeGraph appended beside the unchanged FakeMeta (the Meta contract oracle).
    "R-C27": {"src/whatsvault/providers/fake_meta.py"},
}
RULED_ADDED: dict[str, set[str]] = {}


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)


def test_the_source_commit_is_the_measured_tree():
    assert _git("rev-parse", f"{SOURCE_COMMIT}^{{tree}}").stdout.strip() == SOURCE_TREE


def test_the_subtree_differs_from_the_measured_tree_only_by_ruled_changes():
    measured = {}
    for line in _git("ls-tree", "-r", SOURCE_TREE).stdout.splitlines():
        meta, path = line.split("\t", 1)
        measured[path] = meta.split()[2]
    listed = _git("ls-files", "-co", "--exclude-standard", "--", PREFIX).stdout.splitlines()
    current = {
        name[len(PREFIX) + 1 :]: _git("hash-object", name).stdout.strip()
        for name in listed
        if os.path.isfile(ROOT / name)
    }
    removed = set(measured) - set(current)
    added = set(current) - set(measured)
    modified = {p for p in set(measured) & set(current) if measured[p] != current[p]}
    assert removed == set().union(*RULED_REMOVED.values())
    assert added == set().union(*RULED_ADDED.values())
    assert modified == set().union(*RULED_MODIFIED.values())


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
