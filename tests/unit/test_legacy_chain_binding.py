"""comms v0.3 Task A5: the legacy chain module is a thin binding of the core engine; one write_tx."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEGACY_CHAIN = (
    ROOT / "src" / "comms" / "transports" / "telegram" / "disclosure" / "audit" / "chain.py"
)


def test_legacy_chain_module_defines_no_mac_logic():
    tree = ast.parse(LEGACY_CHAIN.read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"hmac", "hashlib"}
    calls = {getattr(n.func, "attr", None) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "sign" not in calls and "new" not in calls


def test_immediate_transaction_is_gone():
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert "immediate_transaction" not in defined, path
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names
        }
        assert "immediate_transaction" not in names, path


def test_legacy_domain_literals_live_once_in_the_profile():
    from comms.transports.telegram.disclosure.audit import chain, profile

    assert chain.EVENT_DOMAIN is profile.LEGACY_TELEGRAM.event_domain
    src = LEGACY_CHAIN.read_text(encoding="utf-8")
    assert "telegram-mcp-audit-v1" not in src and "telegram-mcp-checkpoint-v1" not in src


def test_legacy_public_api_is_bound_to_the_legacy_profile():
    from comms.core.audit import chain as core
    from comms.transports.telegram.disclosure.audit import chain

    assert chain.genesis_mac(1) == core.genesis_mac(chain.LEGACY_TELEGRAM, 1)
    assert chain.ChainError is core.ChainError
    assert chain.APPEND_GUARD is core.append_guard(chain.LEGACY_TELEGRAM)
