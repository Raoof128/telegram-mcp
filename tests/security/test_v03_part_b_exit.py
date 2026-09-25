"""comms v0.3 Task B33: the Part B exit gate — the plan's list, every row by its owning tests.

The gate re-runs exactly the owning tests in a fresh process and requires every one to be
collected and to pass; none may fail, be skipped or be deselected.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "superpowers" / "plans" / "2026-09-24-comms-v0.3.md"
EVIDENCE = ROOT / "docs" / "verification" / "comms-v0.3.md"

EXIT_CHECKLIST: dict[str, tuple[str, ...]] = {
    "epochs": ("tests/core/audit/test_epochs.py", "tests/core/audit/test_chain_epochs_verify.py"),
    "the root rule": (
        "tests/core/audit/test_truncation_root.py",
        "tests/core/maintenance/test_retention_chain.py",
    ),
    "anchor per event (N2 recorded)": (
        "tests/core/audit/test_writer.py::test_append_is_anchored_to_the_exact_committed_head",
        "tests/core/keys/test_rotate.py::test_every_rotation_is_anchored",
    ),
    "the inventory": ("tests/core/keys/test_purposes.py", "tests/core/keys/test_secret_store.py"),
    "the commitment": ("tests/core/test_campaign_commitment.py",),
    "revoke and recovery": (
        "tests/core/keys/test_rotate.py",
        "tests/core/keys/test_rotate_audit_key.py",
        "tests/core/keys/test_rotate_signers_and_cursor.py",
        "tests/core/keys/test_backup_signer_states.py",
        "tests/core/keys/test_retired_hmac.py",
        "tests/core/test_rekey.py",
        "tests/core/test_credentials.py",
        "tests/telegram/test_session_revoke.py",
        "tests/telegram/test_error_mapping.py",
        "tests/telegram/test_login_recovery.py",
        "tests/core/audit/test_repair.py",
        "tests/core/test_part_b_crash_tables.py",
    ),
    "retention": (
        "tests/core/maintenance/test_retention_legacy.py",
        "tests/core/maintenance/test_body_redaction.py",
        "tests/core/maintenance/test_retention_identities_keys.py",
        "tests/core/maintenance/test_retention_failures.py",
    ),
    "backup": (
        "tests/core/backup/test_age_vectors.py",
        "tests/core/backup/test_age_negatives.py",
        "tests/core/backup/test_signature.py",
        "tests/core/backup/test_payload.py",
        "tests/core/backup/test_transfer.py",
        "tests/core/backup/test_import_stage.py",
        "tests/core/backup/test_import_commit.py",
    ),
    "doctor": ("tests/core/test_doctor.py",),
    "runbooks": ("tests/security/test_runbooks.py",),
    "the two models": (
        "tests/formal/test_audit_model.py",
        "tests/formal/test_audit_model_mutations.py",
        "tests/core/audit/test_audit_differential_walk.py",
        "tests/formal/test_keys_model.py",
        "tests/formal/test_keys_model_mutations.py",
    ),
}


def test_the_checklist_is_the_plan_list_in_full():
    text = PLAN.read_text(encoding="utf-8")
    line = next(line for line in text.splitlines() if "The exit test pins: epochs;" in line)
    listed = [item.strip().rstrip(".") for item in line.split("pins:", 1)[1].split(";")]
    assert listed == list(EXIT_CHECKLIST)


def test_n2_is_recorded_in_the_evidence():
    text = EVIDENCE.read_text(encoding="utf-8")
    part_b = text[text.index("## Part B") :]
    assert "0.21 ms" in part_b and "F_FULLFSYNC" in part_b


def test_part_b_exit_checklist():
    ids = sorted({i for group in EXIT_CHECKLIST.values() for i in group})
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
