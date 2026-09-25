"""Provider fixture provenance (comms v0.3 Task C5; design §C.8).

``tests/fixtures/providers/PROVENANCE.json`` maps each fixture's path (relative, POSIX) to its
provider, API or layer version, source reference, capture date and SHA-256. ``check`` returns
one line per defect; an empty list is a pass.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers"
INDEX = "PROVENANCE.json"
FIELDS = frozenset({"provider", "api_version", "source", "captured", "sha256"})


def _captured_ok(value: object) -> bool:
    try:
        datetime.date.fromisoformat(str(value))
    except ValueError:
        return False
    return len(str(value)) == 10


def check(root: Path) -> list[str]:
    entries = json.loads((root / INDEX).read_text(encoding="utf-8"))["fixtures"]
    files = {
        p.relative_to(root).as_posix(): p
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != INDEX
    }
    problems = []
    for name in sorted(files.keys() | entries.keys()):
        if name not in entries:
            problems.append(f"{name}: no provenance entry")
        elif name not in files:
            problems.append(f"{name}: entry without a file")
        elif set(entries[name]) != FIELDS:
            problems.append(f"{name}: fields")
        elif not _captured_ok(entries[name]["captured"]):
            problems.append(f"{name}: captured")
        elif hashlib.sha256(files[name].read_bytes()).hexdigest() != entries[name]["sha256"]:
            problems.append(f"{name}: sha256 mismatch")
    return problems
