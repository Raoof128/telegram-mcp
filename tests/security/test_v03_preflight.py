"""comms v0.3 Task A1: the rulings register exists with its required columns (spec A39)."""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULINGS = ROOT / "docs" / "verification" / "comms-v0.3-rulings.md"
COLUMNS = ["ID", "Date", "Section", "Discovery", "Decision", "Reason", "Tests", "SHA"]


def test_rulings_file_has_the_required_columns():
    lines = RULINGS.read_text(encoding="utf-8").splitlines()
    header = next(line for line in lines if line.startswith("| ID "))
    assert [c.strip() for c in header.strip("|").split("|")] == COLUMNS


def test_the_spec_and_design_are_exactly_the_last_pinned_versions():
    """A spec or design edit is a ruling (A39): it must be pinned in the register."""
    text = RULINGS.read_text(encoding="utf-8")
    for doc in (
        "docs/comms-spec-v0.3.md",
        "docs/superpowers/specs/2026-09-24-comms-v0.3-design.md",
    ):
        pins = re.findall(
            rf"^\| `{re.escape(doc)}` [^|]*\| `([0-9a-f]{{16}})…` \|$", text, re.MULTILINE
        )
        assert pins, doc
        actual = hashlib.sha256((ROOT / doc).read_bytes()).hexdigest()
        assert actual.startswith(pins[-1]), (doc, pins[-1], actual[:16])
