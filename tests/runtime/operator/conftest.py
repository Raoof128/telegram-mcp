"""A daemon-shaped world for operator handlers: provisioned state, the legacy side as the
daemon initialises it, the comms state opened, the legacy chain attached."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from comms.runtime.operator import OperatorContext, operator_handler
from comms.runtime.paths import CommsPaths
from comms.runtime.provision import provision
from comms.runtime.serve import legacy_side
from comms.runtime.settings import RETENTION_DEFAULTS
from comms.runtime.state import open_comms_state
from comms.transports.telegram.disclosure.keys import ensure_current_published
from comms.transports.telegram.keys.store import load_key, set_store_dir
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.storage.identity import ensure_owner_principal
from comms.transports.telegram.storage.migrations import migrate


def _now():
    return datetime.now(UTC)


@pytest.fixture
def daemon_world(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("run").mkdir(mode=0o700)
    paths = CommsPaths(tmp_path / "state")
    provision(paths, now=_now(), runtime_dir=Path("run"))
    set_store_dir(paths.legacy_keys)
    legacy = open_db(paths.legacy_db)  # what run_daemon does before the comms side
    migrate(legacy)
    ensure_owner_principal(legacy, privacy_key=load_key("privacy-key"))
    ensure_current_published(legacy, purpose="audit_checkpoint",
                             private_seed=load_key("audit-checkpoint-key"), now="2026-09-25T00:00:00Z")  # fmt: skip
    state = open_comms_state(paths, clock=_now)
    ctx = OperatorContext(
        writer=state.writer,
        store=state.store,
        clock=_now,
        legacy=legacy_side(legacy, paths),
        retention_days=dict(RETENTION_DEFAULTS),
    )
    handle = operator_handler(ctx)
    yield {"paths": paths, "state": state, "ctx": ctx, "legacy": legacy,
           "run": lambda *words, **args: handle({"command": list(words), **args})}  # fmt: skip
    state.conn.close()
    legacy.close()
