"""comms v0.3 Task A1: the rulings register exists with its required columns (spec A39)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULINGS = ROOT / "docs" / "verification" / "comms-v0.3-rulings.md"
COLUMNS = ["ID", "Date", "Section", "Discovery", "Decision", "Reason", "Tests", "SHA"]


def test_rulings_file_has_the_required_columns():
    lines = RULINGS.read_text(encoding="utf-8").splitlines()
    header = next(line for line in lines if line.startswith("| ID "))
    assert [c.strip() for c in header.strip("|").split("|")] == COLUMNS
