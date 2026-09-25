"""Retired identifiers are tombstoned permanently (comms spec v0.2 §Tombstones; 5b-3 design §6).

"Retired protocol identifiers are tombstoned permanently. They MUST NOT be
reassigned to new structures or semantics." Each identifier below left
production with the consent subsystem; none may reappear under ``src/`` and
every one must be listed in the normative tombstone section.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
SPEC = ROOT / "docs" / "comms-spec-v0.2.md"

# Byte or string domains: must not occur anywhere in source text.
DOMAINS = (
    "telegram-mcp-display-v1",
    "telegram-mcp-rendezvous/v1",
    "tg-mcp-exposure-snapshot/v1",
    "telegram-mcp-admin-request/v1",
    "telegram-mcp-admin-secret/v1",
    "telegram-mcp-sentinel/v1",
)
# Names: must not be defined or used as an identifier or string constant.
NAMES = ("ApprovalEnvelope", "PROMPT", "APPROVAL", "DENIAL", "PRESENCE_REQUIRED")
OTHER = ("tgu_", "telegram-mcp-agent", "consent status", "consent approve", "RV-1")


def _sources():
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


@pytest.mark.parametrize("domain", DOMAINS)
def test_no_retired_domain_appears_in_source(domain):
    for path in _sources():
        assert domain not in path.read_text(encoding="utf-8"), path


@pytest.mark.parametrize("name", NAMES)
def test_no_retired_name_is_reused_in_source(name):
    for path in _sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            used = (
                (isinstance(node, ast.Name) and node.id == name)
                or (isinstance(node, ast.Attribute) and node.attr == name)
                or (isinstance(node, ast.ClassDef | ast.FunctionDef) and node.name == name)
                or (isinstance(node, ast.Constant) and node.value == name)
            )
            assert not used, (path, name)


def test_the_retired_ref_prefix_no_longer_validates():
    from comms.transports.telegram.authority.refs import REF_PREFIXES, validate_ref_format

    assert "tgu_" not in REF_PREFIXES
    with pytest.raises(ValueError):
        validate_ref_format("tgu_" + "a" * 26)


@pytest.mark.parametrize("identifier", DOMAINS + NAMES + OTHER)
def test_every_tombstone_is_listed_in_the_normative_spec(identifier):
    text = SPEC.read_text(encoding="utf-8")
    section = text[text.index("## Tombstones") :]
    assert f"`{identifier}`" in section, identifier


# ---- comms v0.3 (spec A2): the 16 tool names, tgml1 issuance, the policy-bundle ids ----
MANIFEST = ROOT / "docs" / "comms-v0.3-supersession.json"
T = "comms.transports.telegram."


def _v03_tombstones():
    from comms.core.strict_json import strict_json_loads

    return strict_json_loads(MANIFEST.read_text(encoding="utf-8"))["tombstoned_identifiers"]


def _string_constants(path):
    return {
        node.value
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_v03_tool_names_are_tombstoned():
    from tests.security.import_closure import closure, module_file

    names = [n for n in _v03_tombstones() if "/" not in n and n != "tgml1"]
    assert len(names) == 16
    telegram = {n for n in names if n.startswith("telegram_")}
    assert len(telegram) == 10
    # The legacy chain's event vocabulary and the receipts name them, for verification only.
    verification = closure(T + "legacy_verify")
    for module in closure(T + "cli", T + "runtime.daemon") - verification:
        assert not (_string_constants(module_file(module)) & telegram), module
    # The six WhatsVault tools were registered in exactly one place, now gone (R-A16).
    whatsapp = ROOT / "transports" / "whatsapp"
    assert not (whatsapp / "apps" / "mcp").exists()
    for path in whatsapp.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not any(
            isinstance(n, ast.FunctionDef) and n.name == "build_tool_handlers"
            for n in ast.walk(tree)
        ), path


def test_tgml1_is_tombstoned_for_new_leases():
    from comms.transports.telegram.ipc.admin import (
        ADMIN_COMMANDS,
        RETIRED_ADMIN_COMMANDS,
        AdminRouter,
    )
    from comms.transports.telegram.ipc.leases import LEASE_PREFIX, mint_lease, verify_lease
    from tests.security.import_closure import closure, module_file

    assert LEASE_PREFIX == "tgml1" and callable(verify_lease) and callable(mint_lease)
    assert "auth headers" in RETIRED_ADMIN_COMMANDS and "auth headers" not in ADMIN_COMMANDS
    refused = AdminRouter({}).dispatch({"cmd": "auth headers", "args": {}})
    assert refused["code"] == "RETIRED_IN_V0_3"
    for module in closure(T + "cli", T + "runtime.daemon"):
        for node in ast.walk(ast.parse(module_file(module).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                assert called != "mint_lease", module


@pytest.mark.parametrize("identifier", ["tg-mcp-policy-bundle/v1", "tg-mcp-policy-signature/v1"])
def test_policy_bundle_ids_never_appear(identifier):
    assert identifier in _v03_tombstones()
    roots = (
        SRC,
        ROOT / "transports" / "whatsapp" / "src",
        ROOT / "transports" / "whatsapp" / "apps",
    )
    for root in roots:
        for path in root.rglob("*.py"):
            if "__pycache__" not in path.parts:
                assert identifier not in path.read_text(encoding="utf-8"), path
