"""comms v0.3 Task D38: the Part D exit gate — design D.1–D.11, every item by its owning tests.

The plan's D38 says only that the exit test pins "the Part D list"; the list is the design's
Part D (D.1–D.11), read from the design itself so the two cannot drift. The gate re-runs exactly
the owning tests in a fresh process and requires every one to be collected and to pass; none
may fail, be skipped or be deselected.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "docs" / "superpowers" / "specs" / "2026-09-24-comms-v0.3-design.md"
EVIDENCE = ROOT / "docs" / "verification" / "comms-v0.3.md"
RUNBOOKS = ROOT / "docs" / "runbooks"

EXIT_CHECKLIST: dict[str, tuple[str, ...]] = {
    "D.1 The service layer": (
        "tests/services/test_registry.py",
        "tests/services/test_capability.py",
        "tests/services/test_groups_membership.py",
        "tests/services/test_groups_admin.py",
        "tests/services/test_messages.py",
        "tests/services/test_campaigns.py",
        "tests/services/test_directory.py",
        "tests/services/test_templates_media_account.py",
        "tests/services/test_errors.py",
        "tests/runtime/test_facades.py",
        "tests/security/test_ai_boundary.py",
    ),
    "D.2 Operation records": (
        "tests/core/test_mutations_schema.py",
        "tests/services/test_mutations.py",
        "tests/services/test_mutations_crashes.py",
        "tests/services/test_operations_differential_walk.py",
        "tests/formal/test_operations_model.py",
        "tests/formal/test_operations_model_mutations.py",
    ),
    "D.3 The context engine": (
        "tests/services/test_context.py",
        "tests/services/test_context_telegram.py",
        "tests/services/test_context_whatsapp.py",
        "tests/services/test_ctx_handles.py",
        "tests/services/test_cursors.py",
        "tests/security/test_v03_egress.py",
    ),
    "D.4 Refs": (
        "tests/core/test_refs_v03.py",
        "tests/services/test_resolve.py",
        "tests/evaluation/test_ambiguity_deterministic.py",
    ),
    "D.5 The catalog": (
        "tests/mcp/test_catalog_skeleton.py",
        "tests/mcp/test_catalog_context.py",
        "tests/mcp/test_catalog_messages.py",
        "tests/mcp/test_catalog_groups.py",
        "tests/mcp/test_catalog_admin.py",
        "tests/mcp/test_catalog_campaigns.py",
        "tests/mcp/test_catalog_directory.py",
        "tests/mcp/test_catalog_account.py",
        "tests/mcp/test_catalog_pin.py",
        "tests/mcp/test_dispatch.py",
        "tests/evaluation/test_intent_prompts.py",
    ),
    "D.6 `comms mcp`": (
        "tests/mcp/test_stdio_proxy.py",
        "tests/mcp/test_http.py",
        "tests/core/auth/test_cml1.py",
    ),
    "D.7 The CLI": (
        "tests/cli/test_cli_operations.py",
        "tests/cli/test_cli_operator.py",
        "tests/runtime/test_comms_runtime.py",
    ),
    "D.8 Ingress": ("tests/integration/test_ingress_separation.py",),
    "D.9 Remote OAuth": (
        "tests/mcp/oauth/test_authorization_server.py",
        "tests/mcp/oauth/test_resource_server.py",
    ),
    "D.10 The smoke test moves over": ("tests/security/test_smoke_map.py",),
    "D.11 Client runbooks": (
        "tests/security/test_runbooks.py",
        "tests/security/test_v03_part_d_exit.py::test_every_client_runbook_records_the_d11_fields",
    ),
}
CLIENTS = ("claude-code", "codex", "chatgpt")
D11_FIELDS = (
    "Client and version",
    "Negotiated MCP protocol version",
    "Catalog digest",
    "Auth",
    "Visible tools",
    "A read",
    "A write",
    "A destructive call",
    "An ambiguity refusal",
    "A request-id replay",
)


def test_the_checklist_is_the_design_part_d_list_in_full():
    text = DESIGN.read_text(encoding="utf-8")
    part_d = text[text.index("**D.1 ") : text.index("Part D ends with")]
    listed = [m.rstrip(".") for m in re.findall(r"^\*\*(D\.\d+ .+?)\*\*", part_d, re.MULTILINE)]
    assert listed == list(EXIT_CHECKLIST)


def test_every_client_runbook_records_the_d11_fields():
    for client in CLIENTS:
        text = (RUNBOOKS / f"clients-{client}.md").read_text(encoding="utf-8")
        for field in D11_FIELDS:
            assert f"| {field}" in text, (client, field)
        assert "PENDING OWNER" in text  # owner-run: never filled by the gate


def test_the_part_d_evidence_names_what_d38_requires():
    text = EVIDENCE.read_text(encoding="utf-8")
    part_d = text[text.index("## Part D") :]
    for needle in (
        "Catalog inventory",
        "Capability inventory",
        "Versions",
        "Counts",
        "Canaries",
        "audit verify --all",
        "P §88",
        "PENDING OWNER",
        "Claim boundary",
        "Rulings",
    ):
        assert needle in part_d, needle


def test_part_d_exit_checklist():
    ids = sorted(
        {i for group in EXIT_CHECKLIST.values() for i in group}
        - {
            "tests/security/test_v03_part_d_exit.py::test_every_client_runbook_records_the_d11_fields"
        }
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
    assert not re.search(r"\d+ (failed|skipped|errors?|deselected)", done.stdout), (
        tail
    )  # fail closed
