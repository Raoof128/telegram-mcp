"""Fixtures shared by the service tests."""

import pytest

from comms.core.campaigns import directory as d
from comms.core.groups import group_ref
from comms.core.providers.protocols import ProviderTarget
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW


@pytest.fixture
def world(tmp_path):
    """Twelve Telegram groups, each with its grp_ ref and user-actor target."""
    conn = fx.migrated(tmp_path)
    targets = []
    for n in range(12):
        loc = d.add_location(conn, f"L{n}", now=NOW)
        dst = d.add_destination(
            conn, loc, "telegram", f"group:{100 + n}", f"G{n}", normalize=fx.tg, now=NOW
        )
        grp = group_ref(conn, dst, now=NOW)
        targets.append((grp, ProviderTarget("telegram", "telegram_user", dst, f"-{100 + n}")))
    return {"conn": conn, "targets": targets}
