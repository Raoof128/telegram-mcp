"""The live acceptance run (comms v0.3 Task C32). Collected only with --run-live-acceptance.

Runs every adapter contract against the operator's disposable accounts and writes the report to
the evidence directory. It asserts nothing: live results are evidence, never a gate.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

from comms.core.providers.protocols import ADAPTER_CONTRACTS
from tests.conformance import live
from tests.conformance.registry import REGISTRY
from tests.conformance.runner import record, run_suite

EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "docs" / "verification" / "live-acceptance"


def test_live_acceptance_evidence():
    started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = run_suite(REGISTRY, ADAPTER_CONTRACTS, live=True)
    target = Path(os.environ.get("COMMS_LIVE_EVIDENCE", EVIDENCE_DIR / f"{started[:10]}.json"))
    record(report, target, started=started, accounts=tuple(live.accounts()))
