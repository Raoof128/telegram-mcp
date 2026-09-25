"""comms 5b-4 Task 13: the owner's source §31 test list, line by line.

Every line of §31 in docs/provenance/comms-gateway-v0.1.md maps to the test that
proves it; ``test_every_section_31_line_is_mapped_to_an_existing_test`` keeps the map
complete and honest. Lines with no test elsewhere are tested here.
"""

import ast
import inspect
import re
from pathlib import Path

from comms.core.campaigns import drafts
from comms.core.delivery import freeze
from comms.core.delivery.engine import Engine
from tests.core import fakes
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW, person, ready

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs" / "provenance" / "comms-gateway-v0.1.md"

SECTION_31 = {
    # comms v0.3 (D5, owner_full_admin) supersedes this line: sends are legitimate AI tools,
    # reached only through the typed service layer, never a raw primitive (A37, P §37).
    "AI cannot obtain send primitive": "tests/security/test_ai_boundary.py::test_mcp_handlers_reach_the_service_layer_only",
    "operator send requires no secondary permission": "tests/core/test_source_checklist.py::test_operator_send_requires_no_secondary_permission",
    "one send command creates one immutable snapshot": "tests/core/test_freeze.py::test_one_send_creates_one_immutable_generation",
    "all-locations resolves only configured locations": "tests/core/test_resolve.py::test_all_locations_resolves_only_configured_enabled_locations",
    "disabled location excluded": "tests/core/test_resolve.py::test_all_locations_resolves_only_configured_enabled_locations",
    "disabled destination excluded": "tests/core/test_resolve.py::test_disabled_destination_excluded_and_direct_destination_in_disabled_location_excluded",
    "duplicate WhatsApp recipient sent once": "tests/core/test_freeze.py::test_duplicate_whatsapp_recipient_gets_one_job",
    "same recipient may receive once per selected transport": "tests/core/test_freeze.py::test_same_person_gets_one_job_per_selected_transport",
    "campaign content cannot mutate after SENDING": "tests/core/test_source_checklist.py::test_campaign_content_cannot_mutate_after_sending",
    "retry never duplicates successful delivery": "tests/core/test_operations.py::test_retry_never_duplicates_successful_delivery",
    "cancel stops only unsent work": "tests/core/test_operations.py::test_cancel_stops_only_unsent_work",
    "Telegram failure does not stop WhatsApp": "tests/core/test_engine.py::test_a_raising_deliver_on_one_transport_does_not_stop_the_other",
    "WhatsApp failure does not stop Telegram": "tests/core/test_engine.py::test_a_raising_deliver_on_one_transport_does_not_stop_the_other",
    "raw phone numbers absent from audit/log output": "tests/core/test_privacy.py::test_canaries_absent_from_events_returns_reprs_exceptions_and_logs",
    "transport credentials absent from campaign data": "tests/core/test_privacy.py::test_transport_credentials_absent_from_campaign_data",
    "scheduled campaign needs no second approval": "tests/core/test_scheduling_and_recovery.py::test_scheduled_campaign_needs_no_second_approval",
    "scheduled content remains frozen": "tests/core/test_freeze.py::test_scheduled_content_is_frozen",
    "unknown audience fails closed": "tests/core/test_resolve.py::test_unknown_targets_fail_closed",
    "audience cycle rejected": "tests/core/test_directory.py::test_audience_cycle_is_refused",
    "recipient-set digest stable": "tests/core/test_source_checklist.py::test_recipient_set_digest_is_stable",
    "delivery idempotency survives restart": "tests/core/test_scheduling_and_recovery.py::test_delivery_idempotency_survives_restart",
    "restart resumes unfinished campaign safely": "tests/core/test_scheduling_and_recovery.py::test_restart_resumes_unfinished_campaign_safely",
}


def _section_31_lines() -> list[str]:
    text = SOURCE.read_text(encoding="utf-8")
    body = text[text.index("# 31. Tests") : text.index("# 32.")]
    block = re.search(r"```text\n(.*?)```", body, re.DOTALL)
    assert block is not None
    return [line.strip() for line in block.group(1).splitlines() if line.strip()]


def test_every_section_31_line_is_mapped_to_an_existing_test():
    lines = _section_31_lines()
    assert len(lines) == 22 and set(lines) == set(SECTION_31)
    for target in SECTION_31.values():
        path, name = target.split("::")
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert name in names, target


def test_operator_send_requires_no_secondary_permission(tmp_path):
    """The operator's call is the authorization: no approver, token or presence anywhere."""
    for fn in (freeze.send, freeze.schedule, Engine.execute):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"approver", "approval", "presence", "token", "consent"}, fn
    conn = fx.migrated(tmp_path)
    wa = fakes.FakeWhatsApp(conn=conn)
    rcp, _ = person(conn, phone="+61400000001")
    cmp = ready(conn, {"recipients": [rcp]})
    freeze.send(conn, cmp, {"whatsapp": wa}, now=NOW)
    from comms.core.delivery.engine import ExecutorLease

    Engine(conn, {"whatsapp": wa}, clock=lambda: NOW).execute(ExecutorLease(fakes.FakeLock()), cmp)
    assert wa.sent == ["+61400000001"]


def test_campaign_content_cannot_mutate_after_sending(tmp_path):
    conn = fx.migrated(tmp_path)
    wa = fakes.FakeWhatsApp(conn=conn)
    rcp, _ = person(conn, phone="+61400000001")
    cmp = ready(conn, {"recipients": [rcp]})
    freeze.send(conn, cmp, {"whatsapp": wa}, now=NOW)
    before = conn.execute("SELECT content FROM generations").fetchone()[0]
    for call in (
        lambda: drafts.set_content(conn, cmp, canonical="changed", now=NOW),
        lambda: drafts.set_targets(conn, cmp, {}, frozenset({"whatsapp"}), now=NOW),
        lambda: drafts.edit(conn, cmp, now=NOW),
    ):
        try:
            call()
        except drafts.LifecycleError:
            continue
        raise AssertionError("content mutated after SENDING")
    assert conn.execute("SELECT content FROM generations").fetchone()[0] == before


def test_recipient_set_digest_is_stable():
    jobs = [
        ("djb_" + "a" * 26, "whatsapp", ("rct_" + "b" * 26,), "PENDING"),
        (
            "djb_" + "c" * 26,
            "telegram",
            ("rct_" + "e" * 26, "dst_" + "d" * 26),
            "SKIPPED_PLATFORM_POLICY",
        ),
    ]
    digest = freeze.recipient_digest(jobs)
    shuffled = [jobs[1][:2] + (tuple(reversed(jobs[1][2])),) + jobs[1][3:], jobs[0]]
    assert freeze.recipient_digest(shuffled) == digest
    assert freeze.recipient_digest(jobs[:1]) != digest
    assert digest == "".join(c for c in digest if c in "0123456789abcdef") and len(digest) == 64
