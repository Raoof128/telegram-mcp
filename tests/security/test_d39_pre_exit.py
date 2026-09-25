"""D39-PRE Task E11: the exit gate — the plan's tasks E0–E11, every one by its owning tests.

The checklist is read against the plan's own task titles, so the two cannot drift. The gate
re-runs exactly the owning tests in a fresh process and requires every one to be collected and
to pass; none may fail, be skipped or be deselected. The real-daemon acceptance (D39-A) is the
smoke's ``phase_v03_daemon``; its checks are pinned by the smoke map.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "superpowers" / "plans" / "2026-09-25-comms-v0.3-d39-pre.md"
EVIDENCE = ROOT / "docs" / "verification" / "comms-v0.3.md"
SMOKE_MAP = ROOT / "docs" / "verification" / "comms-v0.3-smoke-map.json"

EXIT_CHECKLIST: dict[str, tuple[str, ...]] = {
    "E0 The owner's rulings, the host permission rules, and doc corrections": (
        "tests/security/test_host_permissions.py",
    ),
    "E1 The state layout and `comms keys provision`": ("tests/runtime/test_provision.py",),
    "E2 `open_comms_state`, a fail-closed open": ("tests/runtime/test_state.py",),
    "E3 Daemon settings": ("tests/runtime/test_settings.py",),
    "E4 One composition root": (
        "tests/runtime/test_assemble.py",
        "tests/security/test_one_composition_root.py",
    ),
    "E5 Startup recovery and background workers": (
        "tests/runtime/test_workers.py",
        "tests/integration/test_adapter_registry.py",
    ),
    "E6 The daemon serves the comms surface": ("tests/integration/test_comms_daemon.py",),
    "E7 Operator commands I — keys, audit, cutover": (
        "tests/runtime/operator/test_keys_audit_cutover.py",
    ),
    "E8 Operator commands II — credentials, transport, retention, backup": (
        "tests/runtime/operator/test_credentials.py",
        "tests/runtime/operator/test_backup_retention.py",
        "tests/runtime/test_serve_reload.py",
        "tests/cli/test_cli_operator.py",
    ),
    "E9 `comms doctor` and the completeness guard": (
        "tests/runtime/operator/test_doctor_cli.py",
        "tests/cli/test_operator_completeness.py",
        "tests/core/test_doctor.py",
    ),
    "E10 The release blockers": (
        "tests/transports/telegram_user/test_telethon_thread.py",
        "tests/unit/test_telethon_session.py",
        "tests/telegram/test_update_rpcs.py",
        "tests/runtime/test_serve_updates.py",
        "tests/integration/test_comms_daemon_telethon.py",
        "tests/transports/whatsapp_webhooks/test_archive.py",
        "tests/services/test_context_whatsapp.py",
        "tests/security/test_v03_egress_d39.py",
        "tests/security/test_ai_boundary.py",
        "tests/core/backup/test_import_commit.py",
    ),
    "E11 D39-A — runtime acceptance, and the tranche evidence": (
        "tests/security/test_smoke_map.py",
        "tests/core/test_groups_mapping.py",
        "tests/runtime/test_facades.py",
        "tests/security/test_d39_pre_exit.py::test_the_real_daemon_checks_are_pinned",
    ),
}
DAEMON_CHECKS = 25


def test_the_checklist_is_the_plan_task_list_in_full():
    titles = re.findall(r"^### Task (E\d+): (.+)$", PLAN.read_text(encoding="utf-8"), re.MULTILINE)
    assert [f"{n} {t}" for n, t in titles] == list(EXIT_CHECKLIST)


def test_the_real_daemon_checks_are_pinned():
    added = json.loads(SMOKE_MAP.read_text(encoding="utf-8"))["added_in_v0_3"]
    daemon = [c for c in added if c.startswith("phase_v03_daemon::")]
    assert len(daemon) == DAEMON_CHECKS


def test_the_evidence_names_what_d39_pre_requires():
    text = EVIDENCE.read_text(encoding="utf-8")
    section = text[text.index("## D39-PRE") :]
    for needle in ("D39-A", "phase_v03_daemon", "R-A20", "R-E17", "Follow-ups", "Waiting on the owner",
                   "comms-v0.3-d39-pre"):  # fmt: skip
        assert needle in section, needle


def test_d39_pre_exit_checklist():
    ids = sorted(
        {i for group in EXIT_CHECKLIST.values() for i in group}
        - {"tests/security/test_d39_pre_exit.py::test_the_real_daemon_checks_are_pinned"}
    )
    for target in ids:
        assert (ROOT / target.split("::")[0]).is_file(), target
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
    assert not re.search(r"\d+ (failed|skipped|errors?|deselected)", done.stdout), tail
