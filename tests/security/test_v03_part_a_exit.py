"""comms v0.3 Task A23: the Part A exit gate — design §A.9, every row, by its owning tests.

The checklist below is the owner's table verbatim (groups and checks); each check names
the tests that prove it. The gate re-runs exactly those tests in a fresh process and
requires every one to be collected and to pass.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "docs" / "superpowers" / "specs" / "2026-09-24-comms-v0.3-design.md"

SUP = "tests/security/test_supersession.py::"
RET = "tests/security/test_v03_retired_surfaces.py::"
CAT = "tests/mcp/test_catalog_skeleton.py::"
LV = "tests/unit/test_legacy_verify.py::"
CL = "tests/core/audit/test_cutover_legacy.py::"
CC = "tests/core/audit/test_cutover_comms.py::"
AIB = "tests/security/test_ai_boundary.py::"

EXIT_CHECKLIST: dict[str, dict[str, tuple[str, ...]]] = {
    "Spec": {
        "v0.3 normative": (SUP + "test_manifest_tombstones_match_the_spec",),
        "precedence resolved": (
            SUP + "test_the_spec_precedence_ranks_v03_first",
            "tests/security/test_v03_preflight.py::test_the_spec_and_design_are_exactly_the_last_pinned_versions",
        ),
        "manifest green": (
            SUP + "test_manifest_is_canonical_jcs",
            SUP + "test_manifest_declares_the_new_authority",
            SUP + "test_manifest_names_the_design_m9_tools",
        ),
    },
    "Legacy surface": {
        "none of the 16 names advertised": (
            CAT + "test_each_retired_name_is_tool_not_found_with_zero_effects",
            "tests/security/test_tombstones.py::test_v03_tool_names_are_tombstoned",
        ),
        "direct calls to them cause zero effects": (
            CAT + "test_each_retired_name_is_tool_not_found_with_zero_effects",
        ),
        "`telegram-mcp serve` refused": (RET + "test_telegram_mcp_serve_refuses",),
        "`apps/mcp` absent": (RET + "test_whatsvault_mcp_app_is_absent",),
    },
    "History": {
        "v1/v2 receipts verify": (
            LV + "test_v1_receipts_verify_only_as_v1",
            LV + "test_v2_receipts_verify_only_as_v2",
        ),
        "the legacy chain verifies": (
            LV + "test_the_legacy_chain_verifies_and_a_tamper_fails",
            "tests/unit/test_legacy_chain_vectors.py::test_the_legacy_modules_reproduce_the_vectors",
            RET + "test_legacy_verify_imports_no_policy_or_coordinator",
        ),
        "historical key coverage complete": (LV + "test_historical_key_coverage_is_complete",),
    },
    "Cutover": {
        "drained, sealed and anchored": (
            CL + "test_phases_advance_in_order_and_persist",
            CL + "test_after_seal_a_legacy_append_is_structurally_impossible",
        ),
        "`cut_` durable": (CL + "test_phases_advance_in_order_and_persist",),
        "genesis bound": (
            CC + "test_first_comms_event_is_system_audit_cutover_bound_to_the_legacy_digest",
            CC + "test_lineage_digest_covers_every_column",
        ),
        "`system.audit_cutover` chained and anchored": (
            CC + "test_the_comms_anchor_is_at_the_exact_head_after_genesis",
            "tests/core/audit/test_verify_all.py::test_all_green_after_run_cutover",
        ),
        "each crash boundary converges on restart": (
            "tests/core/audit/test_cutover_crashes.py::test_a_crash_at_every_boundary_converges_on_restart",
        ),
        "mismatched lineage fails closed": (
            CC + "test_conflicting_legacy_digest_for_the_same_cut_ref_fails_closed",
            "tests/core/audit/test_verify_all.py::test_genesis_without_matching_seal_is_integrity_failure",
        ),
    },
    "New surface": {
        "a catalog skeleton exists (`comms_*`, closed dispatch)": (
            CAT + "test_every_tool_is_comms_prefixed_and_unique",
            CAT + "test_unknown_name_is_tool_not_found_with_zero_effects",
        ),
        "deterministic": (CAT + "test_catalog_digest_is_stable_in_process_and_subprocess",),
        "collision-free": (
            SUP + "test_manifest_tombstones_are_disjoint_from_the_catalog",
            "tests/core/test_refs_v03.py::test_v03_prefixes_disjoint_from_telegram_whatsvault_and_5b4",
        ),
        "annotated honestly": (
            CAT + "test_annotations_are_derived_from_read_only_and_destructive",
            AIB + "test_write_tools_are_annotated_as_writes",
            AIB + "test_destructive_tools_are_annotated_destructive",
        ),
        "no raw RPC": (
            CAT + "test_no_tool_takes_a_raw_method_or_path_argument",
            AIB + "test_no_raw_rpc_tool",
        ),
        "no secrets": (CAT + "test_the_ai_surface_carries_no_secret",),
    },
}


def _design_rows() -> dict[str, list[str]]:
    text = DESIGN.read_text(encoding="utf-8")
    table = text[text.index("**A.9 The Part A exit gate**") : text.index("Test accounting follows")]
    rows = {}
    for line in table.splitlines():
        match = re.match(r"\| \*\*(.+?)\*\* \| (.+) \|$", line)
        if match:
            rows[match.group(1)] = [c.strip() for c in match.group(2).split(";")]
    return rows


def test_the_checklist_is_the_design_table_in_full():
    assert {g: list(checks) for g, checks in EXIT_CHECKLIST.items()} == _design_rows()
    assert all(ids for checks in EXIT_CHECKLIST.values() for ids in checks.values())


def test_part_a_exit_checklist():
    ids = sorted(
        {i for checks in EXIT_CHECKLIST.values() for group in checks.values() for i in group}
    )
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly", "-p", "no:cacheprovider", *ids],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    tail = done.stdout[-2000:] + done.stderr[-2000:]
    assert done.returncode == 0, tail
    assert re.search(r"\d+ passed", done.stdout), tail
    assert not re.search(r"\d+ (failed|skipped|errors?|deselected)", done.stdout), (
        tail
    )  # fail closed
