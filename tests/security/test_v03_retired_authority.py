"""comms v0.3 Task A15: disclosure-on-read and project/grant authority are retired; rows are kept."""

import ast
import secrets
from pathlib import Path

import pytest

from comms.transports.telegram.ipc.admin import (
    ADMIN_COMMANDS,
    RETIRED_ADMIN_COMMANDS,
    RETIRED_IN_V0_3,
)
from comms.transports.telegram.keys.store import provision_missing
from comms.transports.telegram.runtime.composition import build_admin
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.storage.migrations import migrate
from tests.authority_fixtures import seed_authority_rows, seed_project_world
from tests.security.import_closure import closure

ROOT = Path(__file__).resolve().parents[2]
T = "comms.transports.telegram."
RETIRED_AUTHORITY = {
    T + "authority.policy",
    T + "authority.effective",
    T + "disclosure.coordinator",
    T + "disclosure.budget",
    T + "storage.authority_view",
    T + "storage.effective_access",
    T + "ipc.handlers.projects",
    T + "ipc.handlers.scope",
    T + "ipc.handlers.policy",
    T + "ipc.handlers.exposure",
}
KEPT_TABLES = (
    "projects",
    "project_peers",
    "client_projects",
    "peer_policy",
    "policy_state",
    "disclosure_receipts",
    "exposure_ledger",
)


def test_no_production_path_reaches_the_coordinator_budget_or_policy_evaluator():
    reached = closure(T + "cli", T + "runtime.daemon")
    assert reached.isdisjoint(RETIRED_AUTHORITY), sorted(reached & RETIRED_AUTHORITY)


def test_the_retired_commands_are_exactly_projects_scope_policy_and_exposure():
    retired = set(RETIRED_ADMIN_COMMANDS)
    assert retired.isdisjoint(ADMIN_COMMANDS)
    assert {c.split()[0] for c in retired} == {"project", "scope", "policy", "exposure"}
    assert not any(
        c.split()[0] in {"project", "scope", "policy", "exposure"} for c in ADMIN_COMMANDS
    )
    assert len(retired) == 28


@pytest.fixture
def populated(tmp_path):
    store = tmp_path / "keys"
    provision_missing(store, phases=(2, 3))
    conn = open_db(tmp_path / "legacy.db")
    migrate(conn)
    seed_authority_rows(conn)
    seed_project_world(conn)
    (tmp_path / "anchor").mkdir(mode=0o700)
    router = build_admin(
        conn,
        key_dir=store,
        anchor_path=tmp_path / "anchor" / "anchor.json",
        runtime_id=secrets.token_bytes(16),
    )
    return conn, router


def _dump(conn):
    return list(conn.iterdump())


def test_retired_admin_commands_answer_retired(populated):
    conn, router = populated
    before = _dump(conn)
    for command in RETIRED_ADMIN_COMMANDS:
        for args in ({}, {"slug": "ops", "display_name": "Ops"}, {"mode": "all_cloud_chats"}):
            response = router.dispatch({"cmd": command, "args": args})
            assert (response["ok"], response["code"]) == (False, RETIRED_IN_V0_3), command
    assert _dump(conn) == before


async def test_retired_commands_are_refused_on_the_async_path_too(populated):
    _conn, router = populated
    response = await router.adispatch({"cmd": "project create", "args": {"slug": "x"}})
    assert (response["ok"], response["code"]) == (False, RETIRED_IN_V0_3)


def test_consent_commands_stay_tombstoned_not_retired(populated):
    _conn, router = populated
    for command in ("consent status", "consent approve"):
        assert router.dispatch({"cmd": command, "args": {}})["code"] == "UNKNOWN_COMMAND"


def test_projects_grants_rows_are_not_dropped(populated):
    conn, _router = populated
    counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in KEPT_TABLES}
    assert (
        counts["projects"] == 1 and counts["client_projects"] == 1 and counts["project_peers"] == 3
    )
    migrate(conn)  # every migration this build ships, re-run: nothing is dropped or emptied
    assert {
        t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in KEPT_TABLES
    } == counts


def test_production_code_never_widens_the_admin_surface():
    src = ROOT / "src"
    allowed = src / "comms" / "transports" / "telegram" / "runtime" / "legacy_composition.py"
    for path in src.rglob("*.py"):
        if path == allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and any(k.arg == "surface" for k in node.keywords):
                raise AssertionError(f"{path} widens the admin surface")
