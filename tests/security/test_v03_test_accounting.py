"""comms v0.3 Part A test accounting (Task A21): unexpectedly missing = 0.

Every test ID collected before Part A (A1's manifest) and absent after it is classified:
removed with a retired surface, or replaced by a named test that is itself collected.
Parametrize IDs that embed an object address are normalised, since the address changes
on every run. WhatsVault's collection is accounted for by the R-A16 removal list.
"""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "verification"
_ADDRESS = re.compile(r"0x[0-9a-f?]+")


def _manifest(name: str) -> set[str]:
    text = (EVIDENCE / name).read_text(encoding="utf-8")
    return {_ADDRESS.sub("0x?", line) for line in text.splitlines() if "::" in line}


def _classification() -> dict:
    return json.loads((EVIDENCE / "comms-v0.3-classification-a.json").read_text(encoding="utf-8"))


def test_unexpectedly_missing_is_zero():
    before = _manifest("comms-v0.3-collected-before.txt")
    after = _manifest("comms-v0.3-collected-after-a.txt")
    classified = _classification()
    removed, replaced = set(classified["removed_with"]), set(classified["replaced_by"])
    assert not removed & replaced, "a test is either removed or replaced, never both"
    assert before - after == removed | replaced


def test_every_replacement_is_a_collected_test():
    after = _manifest("comms-v0.3-collected-after-a.txt")
    for old, new in _classification()["replaced_by"].items():
        assert new in after, (old, new)


def test_whatsvault_removals_account_for_the_whole_difference():
    listed = (EVIDENCE / "comms-v0.3-whatsvault-removed-a16.txt").read_text(encoding="utf-8")
    removed = {line.split("\t")[0] for line in listed.splitlines() if "::" in line}
    assert all(
        line.endswith("removed_with:whatsvault-mcp-app")
        for line in listed.splitlines()
        if "::" in line
    )
    done = subprocess.run(
        [
            "../../.venv/bin/python",
            "-m",
            "pytest",
            "-p",
            "no:randomly",
            "-p",
            "no:cacheprovider",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
        ],
        cwd=ROOT / "transports" / "whatsapp",
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    after = {line for line in done.stdout.splitlines() if "::" in line}
    assert len(after) + len(removed) == _classification()["whatsvault"]["collected_before"]
    assert not after & removed
