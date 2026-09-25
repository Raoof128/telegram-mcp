"""comms v0.3 Task A13: the cutover crash table — every boundary converges to one seal and one genesis.

Each case kills the cutover at one boundary through the ``crash_at`` seam (dead in
production, like the coordinator's), restarts it, and requires the same end state.
"""

import ast
from pathlib import Path

import pytest

from comms.core.audit import cutover as co
from comms.core.audit.verify_all import verify_all
from comms.transports.telegram.storage.authority_view import load_security
from tests.core.audit.legacy_fixtures import comms_world, verify_keys
from tests.core.campaign_helpers import NOW

# The nine phase writes, then the three commits that precede a phase write.
BOUNDARIES = (
    *(f"after_{phase}" for phase in co.PHASES[1:]),
    "after_legacy_seal_commit",
    "after_COMMS_GENESIS_commit_before_anchor",
    "after_legacy_revoke",
)


def test_the_table_names_every_phase_write():
    assert len(co.PHASES[1:]) == 9
    assert len(BOUNDARIES) == 12


def _converged(world):
    conn, port = world["conn"], world["port"]
    assert co.current_phase(conn)[0] == "COMPLETE"
    markers = port.conn.execute(
        "SELECT count(*) FROM audit_events WHERE tool_name = 'system.cutover_final'"
    ).fetchone()[0]
    sealing = port.conn.execute(
        "SELECT count(*) FROM audit_checkpoints c JOIN audit_events e"
        " ON e.event_id = c.last_event_id WHERE e.tool_name = 'system.cutover_final'"
    ).fetchone()[0]
    assert (markers, sealing) == (1, 1)
    assert conn.execute("SELECT count(*) FROM audit_lineage").fetchone()[0] == 1
    kinds = [r[0] for r in conn.execute("SELECT kind FROM audit_events ORDER BY chain_seq")]
    assert kinds == ["system.audit_cutover", "system.legacy_client_auth_revoked"]
    assert list(port.key_dir.glob("lease-seed.*")) == []
    assert (
        port.conn.execute("SELECT count(*) FROM mcp_clients WHERE enabled = 1").fetchone()[0] == 0
    )
    assert load_security(port.conn)[0] == 2
    report = verify_all(conn, port.conn, verify_keys(world))
    assert report.problems == () and report.ok


@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_a_crash_at_every_boundary_converges_on_restart(tmp_path, boundary):
    world = comms_world(tmp_path, bearer=True)
    seam = boundary
    if boundary == "after_COMMS_GENESIS_commit_before_anchor":
        seam = "after_COMMS_GENESIS"
    with pytest.raises(co.CutoverCrash):
        co.run_cutover(world["conn"], world["port"], world["writer"], now=NOW, crash_at=seam)
    if boundary == "after_COMMS_GENESIS_commit_before_anchor":
        world["anchor"].unlink()  # the process died after COMMIT, before the anchor refresh
    assert co.run_cutover(world["conn"], world["port"], world["writer"], now=NOW) == "COMPLETE"
    _converged(world)
    assert co.run_cutover(world["conn"], world["port"], world["writer"], now=NOW) == "COMPLETE"
    _converged(world)


def test_the_crash_seam_is_never_supplied_by_production_code():
    root = Path(__file__).resolve().parents[3] / "src"
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and any(
                k.arg == "crash_at"
                and not (isinstance(k.value, ast.Name) and k.value.id == "crash_at")
                for k in node.keywords
            ):
                raise AssertionError(f"{path} passes a crash point")
