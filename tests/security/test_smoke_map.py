"""comms v0.3 Task A22: every legacy smoke check is accounted for (unexpectedly missing = 0)."""

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAP = ROOT / "docs" / "verification" / "comms-v0.3-smoke-map.json"
SMOKE = ROOT / "scripts" / "e2e_smoke.py"
STATUS = re.compile(r"(retained|retired:[a-z0-9_.]+|replaced_by:\S.*)\Z")


def _current_checks() -> list[str]:
    """``<phase function>::<check name>`` for every ledger.run in the smoke."""
    found = []
    for function in ast.parse(SMOKE.read_text(encoding="utf-8")).body:
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run"
                and getattr(node.func.value, "id", None) == "ledger"
            ):
                name = node.args[1]
                assert isinstance(name, ast.Constant), "check names are literal"
                found.append(f"{function.name}::{name.value}")
    return found


def test_every_legacy_smoke_check_is_accounted_for():
    mapping = json.loads(MAP.read_text(encoding="utf-8"))
    legacy, added = mapping["legacy"], mapping["added_in_v0_3"]
    current = _current_checks()
    assert len(legacy) == 52
    for check, status in legacy.items():
        assert STATUS.fullmatch(status), (check, status)
        if status == "retained":
            assert check in current, check
        if status.startswith("replaced_by:"):
            assert status.removeprefix("replaced_by:") in current, check
    unaccounted = set(current) - set(legacy) - set(added)
    assert not unaccounted, sorted(unaccounted)
    assert set(added) <= set(current)


def test_the_cutover_phase_is_in_the_smoke():
    current = _current_checks()
    assert (
        "phase_v03_cutover::verify --all is green: legacy seal, lineage, comms genesis, anchor"
        in current
    )


COMMS_PHASE = "phase_v03_comms::"
# Task D37: the phase covers each of these (reads, writes, a campaign, an admin operation, a
# degraded response, context, WhatsApp, capability, OAuth with a local AS, a request-id replay).
REQUIRED = (
    "a read over stdio reaches its service",
    "a write over HTTP records one operation",
    "a campaign runs through the real CLI over the admin socket",
    "an admin operation reaches the provider once",
    "a degraded audit trail refuses new writes",
    "context page resumes by cursor",
    "WhatsApp mark_read is its own audited write",
    "capability for a group names every actor",
    "OAuth: a local AS issues a token the remote /mcp accepts",
    "request-id replay returns the first result, no second effect",
    "audit verify --all is clean after every surface wrote",  # D38
)


def test_every_legacy_smoke_check_accounted_and_every_replacement_present():
    mapping = json.loads(MAP.read_text(encoding="utf-8"))
    current = set(_current_checks())
    missing = [
        status.removeprefix("replaced_by:")
        for status in mapping["legacy"].values()
        if status.startswith("replaced_by:") and status.removeprefix("replaced_by:") not in current
    ]
    assert missing == []  # unexpectedly missing = 0
    assert all(COMMS_PHASE + name in current for name in REQUIRED)
    assert all(COMMS_PHASE + name in mapping["added_in_v0_3"] for name in REQUIRED)
    replaced = [s for s in mapping["legacy"].values() if s.startswith("replaced_by:" + COMMS_PHASE)]
    assert len(replaced) == 7
