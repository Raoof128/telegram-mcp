"""The single source of the 5b-1 move (comms design rev 2, §2.2).

Everything the rewriter and the equivalence checker know about the move
lives here, so the two tools cannot disagree.
"""

from __future__ import annotations

OLD = "telegram_mcp"
NEW = "comms.transports.telegram"
OLD_DIR = "src/telegram_mcp"
NEW_DIR = "src/comms/transports/telegram"

# Package-resource anchors: string arguments that name the package so that
# importlib.resources finds the data files. They must follow the files.
# (path relative to the package, old text, new text)
RESOURCE_ANCHORS: tuple[tuple[str, str, str], ...] = (
    (
        "server.py",
        'resources.files("telegram_mcp")',
        'resources.files("comms.transports.telegram")',
    ),
    (
        "contract.py",
        'resources.files("telegram_mcp")',
        'resources.files("comms.transports.telegram")',
    ),
)

# Frozen identifiers (design §2.1): literals containing "telegram_mcp" that
# are NOT module paths and must keep their bytes. (repo-relative file, value)
KEEP_LITERALS: frozenset[tuple[str, str]] = frozenset(
    {
        ("src/comms/transports/telegram/cli.py", "telegram_mcp"),
        ("src/comms/transports/telegram/observability/logging.py", "telegram_mcp"),
        ("src/comms/transports/telegram/audit_seam.py", "telegram_mcp.audit"),
        ("src/comms/transports/telegram/sensitive_dispatch.py", "telegram_mcp.sensitive"),
        ("src/comms/transports/telegram/runtime/daemon.py", "telegram_mcp.daemon"),
        ("src/comms/transports/telegram/ipc/rendezvous.py", "telegram_mcp.rendezvous"),
        ("src/comms/transports/telegram/ipc/admin.py", "telegram_mcp.admin"),
        ("src/comms/transports/telegram/ipc/handlers/_wrapper.py", "telegram_mcp.admin"),
        ("src/comms/transports/telegram/runtime/ingress.py", "telegram_mcp_principal"),
        ("src/comms/transports/telegram/telegram/telethon_adapter.py", "telegram_mcp_operation"),
        ("tests/unit/test_gate.py", "telegram_mcp.audit"),
        ("tests/security/test_redaction.py", "telegram_mcp"),
        ("tests/security/test_redaction.py", "telegram_mcp.test.filter.probe"),
        ("tests/telegram/recorder.py", "telegram_mcp_rpc_phase"),
    }
)

# The only new .py files the move may add under src/comms.
NEW_FILES: frozenset[str] = frozenset(
    {
        "src/comms/__init__.py",
        "src/comms/core/__init__.py",
        "src/comms/transports/__init__.py",
    }
)


def rewrite_module(name: str) -> str:
    if name == OLD or name.startswith(OLD + "."):
        return NEW + name[len(OLD) :]
    return name
