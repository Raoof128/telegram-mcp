"""The 5b-1 migration tools on synthetic inputs (comms design §2.2-§2.4)."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from migration import ast_equivalence, move_map, rewrite


def test_rewrite_module_maps_only_the_package_prefix():
    assert move_map.rewrite_module("telegram_mcp") == "comms.transports.telegram"
    assert (
        move_map.rewrite_module("telegram_mcp.ipc.admin") == "comms.transports.telegram.ipc.admin"
    )
    assert move_map.rewrite_module("telegram_mcp_extra") == "telegram_mcp_extra"
    assert move_map.rewrite_module("os.path") == "os.path"


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_imports_are_rewritten_and_logger_names_are_not(tmp_path):
    path = _write(
        tmp_path,
        "src/telegram_mcp/ipc/admin.py",
        "import logging\n"
        "from telegram_mcp.ipc.framing import (\n    read_frame,\n)\n"
        "import telegram_mcp.opaque as opaque\n"
        '_logger = logging.getLogger("telegram_mcp.admin")\n',
    )
    assert rewrite.rewrite_file(path, repo_root=tmp_path) is True
    text = path.read_text()
    assert "from comms.transports.telegram.ipc.framing import (" in text
    assert "import comms.transports.telegram.opaque as opaque" in text
    assert 'logging.getLogger("telegram_mcp.admin")' in text  # a frozen identifier


def test_test_literals_follow_the_classification_rules(tmp_path):
    path = _write(
        tmp_path,
        "tests/security/test_x.py",
        'SRC = ROOT / "src" / "telegram_mcp"\n'
        'PACKAGE = "telegram_mcp"\n'
        'TARGET = "telegram_mcp.telegram.reads.run_page"\n'
        'FILE = "src/telegram_mcp/contracts/a.json"\n'
        'PROBE = "import sys, telegram_mcp.server; print(1)"\n',
    )
    rewrite.rewrite_file(path, repo_root=tmp_path)
    text = path.read_text()
    assert 'ROOT / "src" / "comms" / "transports" / "telegram"' in text
    assert 'PACKAGE = "comms.transports.telegram"' in text
    assert '"comms.transports.telegram.telegram.reads.run_page"' in text
    assert '"src/comms/transports/telegram/contracts/a.json"' in text
    assert '"import sys, comms.transports.telegram.server; print(1)"' in text


def test_an_unclassified_literal_refuses(tmp_path):
    path = _write(tmp_path, "tests/unit/test_y.py", 'odd = f(x="telegram_mcp")\n')
    with pytest.raises(rewrite.UnclassifiedLiteral):
        rewrite.rewrite_file(path, repo_root=tmp_path)
    assert path.read_text() == 'odd = f(x="telegram_mcp")\n'  # nothing written on refusal


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _write(tmp_path, "src/telegram_mcp/__init__.py", '"""pkg."""\n')
    _write(
        tmp_path,
        "src/telegram_mcp/server.py",
        "from importlib import resources\n"
        "from telegram_mcp.opaque import mint\n"
        'M = resources.files("telegram_mcp") / "contracts"\n'
        "def f():\n    return mint()\n",
    )
    _write(tmp_path, "src/telegram_mcp/opaque.py", "def mint():\n    return 1\n")
    _write(tmp_path, "src/telegram_mcp/contracts/a.json", '{"a": 1}\n')
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _move(root: Path) -> None:
    (root / "src/comms/transports").mkdir(parents=True)
    _git(root, "mv", "src/telegram_mcp", "src/comms/transports/telegram")
    for rel in (
        "src/comms/__init__.py",
        "src/comms/core/__init__.py",
        "src/comms/transports/__init__.py",
    ):
        _write(root, rel, '"""pkg."""\n')
    for path in sorted((root / "src/comms").rglob("*.py")):
        rewrite.rewrite_file(path, repo_root=root)


def test_a_faithful_move_is_equivalent(repo):
    _move(repo)
    report = ast_equivalence.check("HEAD", repo_root=repo)
    assert report.ok, report.problems
    assert report.modules == 3


def test_a_semantic_edit_is_caught(repo):
    _move(repo)
    target = repo / "src/comms/transports/telegram/opaque.py"
    target.write_text("def mint():\n    return 2\n")
    report = ast_equivalence.check("HEAD", repo_root=repo)
    assert not report.ok
    assert any("opaque.py" in p for p in report.problems)


def test_a_changed_data_file_is_caught(repo):
    _move(repo)
    (repo / "src/comms/transports/telegram/contracts/a.json").write_text('{"a": 2}\n')
    report = ast_equivalence.check("HEAD", repo_root=repo)
    assert not report.ok and any("a.json" in p for p in report.problems)


def test_an_extra_module_is_caught(repo):
    _move(repo)
    _write(repo, "src/comms/core/helpers.py", "def g():\n    return 0\n")
    report = ast_equivalence.check("HEAD", repo_root=repo)
    assert not report.ok and any("helpers.py" in p for p in report.problems)


def test_protocol_constants_are_collected(tmp_path):
    _write(
        tmp_path,
        "m.py",
        'A = b"telegram-mcp-audit-v1"\nB = "tg-mcp-grant/v1"\nC = "tgml1"\nD = "other"\n',
    )
    assert ast_equivalence.protocol_constants(tmp_path) == [
        "'tg-mcp-grant/v1'",
        "'tgml1'",
        "b'telegram-mcp-audit-v1'",
    ]
