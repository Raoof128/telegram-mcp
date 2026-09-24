"""comms v0.3 Task C34: the Part C exit gate — the plan's list, every row by its owning tests.

The gate re-runs exactly the owning tests in a fresh process and requires every one to be
collected and to pass; none may fail, be skipped or be deselected. Conformance is also checked
here directly: every contract every adapter advertises, with zero skips of any kind.
"""

import re
import subprocess
import sys
from pathlib import Path

from comms.core.providers.protocols import ADAPTER_CONTRACTS
from tests.conformance.registry import REGISTRY
from tests.conformance.runner import run_suite

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "superpowers" / "plans" / "2026-09-24-comms-v0.3.md"
EVIDENCE = ROOT / "docs" / "verification" / "comms-v0.3.md"

EXIT_CHECKLIST: dict[str, tuple[str, ...]] = {
    "conformance over every contract, with zero unexplained skips": (
        "tests/conformance/test_framework.py",
        "tests/security/test_v03_part_c_exit.py::test_every_advertised_contract_passes_with_zero_skips",
        "tests/transports/telegram_bot/test_context.py::test_the_whole_telegram_bot_conformance_suite_passes",
        "tests/transports/telegram_user/test_context.py::test_the_whole_telegram_user_conformance_suite_passes",
        "tests/transports/whatsapp_cloud/test_account_groups.py::test_the_whole_whatsapp_cloud_conformance_suite_passes",
        "tests/transports/whatsapp_webhooks/test_inbox.py::test_the_whole_whatsapp_webhooks_conformance_suite_passes",
        "tests/conformance/test_meta_oracle.py",
    ),
    "classification tables": (
        "tests/transports/telegram_bot/test_classify.py",
        "tests/transports/telegram_bot/test_admin_members.py",
        "tests/transports/telegram_bot/test_admin_chat.py",
        "tests/transports/telegram_bot/test_admin_invites.py",
        "tests/transports/telegram_user/test_admin_members.py",
        "tests/transports/telegram_user/test_admin_chat.py",
        "tests/transports/whatsapp_cloud/test_classify.py",
        "tests/transports/whatsapp_cloud/test_templates.py",
    ),
    "semantics": (
        "tests/core/providers/test_semantics.py",
        "tests/core/providers/test_ratelimit.py",
    ),
    "egress": ("tests/security/test_egress.py", "tests/transports/whatsapp_cloud/test_media.py"),
    "`random_id`": (
        "tests/transports/telegram_user/test_send_random_id.py",
        "tests/core/test_provider_request_key.py",
        "tests/transports/telegram_user/test_updates.py",
    ),
    "retries pinned": (
        "tests/telegram/test_rpc_sets.py",
        "tests/unit/test_telethon_session.py::test_the_client_is_built_exactly_as_section_36_requires",
    ),
    "the window and templates": (
        "tests/transports/whatsapp_cloud/test_delivery.py",
        "tests/transports/whatsapp_cloud/test_templates.py",
    ),
    "the webhook": (
        "tests/transports/whatsapp_webhooks/test_ingress.py",
        "tests/transports/whatsapp_webhooks/test_inbox.py",
        "tests/core/maintenance/test_retention_inbound.py",
    ),
    "canaries": ("tests/security/test_adapter_canaries.py",),
    "fixture provenance": ("tests/conformance/test_provenance.py",),
}


def test_the_checklist_is_the_plan_list_in_full():
    text = PLAN.read_text(encoding="utf-8")
    task = text[text.index("### Task C34:") :]
    block = task[task.index("The exit test pins:") : task.index("**Step 2.**")]
    listed = [
        line.strip()[2:].rstrip(";.") for line in block.splitlines() if line.startswith("  - ")
    ]
    assert listed == list(EXIT_CHECKLIST)


def test_every_advertised_contract_passes_with_zero_skips():
    report = run_suite(REGISTRY, ADAPTER_CONTRACTS)
    assert report.ok and report.skipped == {} and report.failures == (), report.failures
    assert {adapter for adapter, _contract in REGISTRY.cases} == set(ADAPTER_CONTRACTS)


def test_the_part_c_evidence_names_the_inventory_versions_and_follow_ups():
    text = EVIDENCE.read_text(encoding="utf-8")
    part_c = text[text.index("## Part C") :]
    for needle in (
        "telegram_bot",
        "telegram_user",
        "whatsapp_cloud",
        "whatsapp_webhooks",
        "Graph API v21.0",
        "Telethon 1.45.0",
        "PROVENANCE.json",
        "Follow-ups",
    ):
        assert needle in part_c, needle


def test_part_c_exit_checklist():
    ids = sorted(
        {i for group in EXIT_CHECKLIST.values() for i in group}
        - {
            "tests/security/test_v03_part_c_exit.py::test_every_advertised_contract_passes_with_zero_skips"
        }
    )
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly", "-p", "no:cacheprovider", *ids],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    tail = done.stdout[-2000:] + done.stderr[-2000:]
    assert done.returncode == 0, tail
    assert re.search(r"\d+ passed", done.stdout), tail
    assert not re.search(r"\d+ (failed|skipped|errors?|deselected)", done.stdout), (
        tail
    )  # fail closed
