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
