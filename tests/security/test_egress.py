"""comms v0.3 Task C1: httpx at runtime (already locked) and the host-pinned egress client."""

import ast
import re
import subprocess
from pathlib import Path

import httpx
import pytest

from comms.transports.net import EgressRefused, pinned_client

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "comms"
NETWORK_MODULES = {
    "transports/net.py",
    "transports/telegram/bot/http.py",
    "transports/whatsapp/cloud/http.py",
    "transports/whatsapp/cloud/media.py",
    "transports/telegram/telegram/telethon_adapter.py",
    "mcp/stdio_proxy.py",  # comms v0.3 D29: loopback to the daemon's /mcp only (it refuses others)
}


def _packages(lock: str) -> set[str]:
    return set(re.findall(r'^name = "([^"]+)"$', lock, re.MULTILINE))


def test_uv_lock_adds_no_new_package():
    before = subprocess.run(
        ["git", "show", "comms-v0.3-part-b:uv.lock"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert _packages((ROOT / "uv.lock").read_text()) == _packages(before)


def test_only_adapter_network_modules_import_httpx_or_telethon():
    for path in SRC.rglob("*.py"):
        names = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        if names & {"httpx", "telethon"}:
            assert path.relative_to(SRC).as_posix() in NETWORK_MODULES, path


def _mock(record):
    def handler(request):
        record.append(str(request.url))
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "https://evil.example/x"})
        return httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(handler)


def test_pinned_client_refuses_other_hosts_schemes_ports_and_redirects():
    record: list[str] = []
    client = pinned_client("https://api.telegram.org", timeout=5, transport=_mock(record))
    assert client.get("https://api.telegram.org/bot/getMe").status_code == 200
    for url in (
        "https://evil.example/bot/getMe",
        "http://api.telegram.org/bot/getMe",
        "https://api.telegram.org:8443/bot/getMe",
        "https://api.telegram.org.evil.example/bot",
    ):
        with pytest.raises(EgressRefused):
            client.get(url)
    response = client.get("https://api.telegram.org/redirect")
    assert response.status_code == 302 and len(record) == 2  # never followed


def test_the_origin_must_be_https_with_no_path():
    for origin in ("http://api.telegram.org", "https://api.telegram.org/bot", "api.telegram.org"):
        with pytest.raises(ValueError):
            pinned_client(origin, timeout=5)
