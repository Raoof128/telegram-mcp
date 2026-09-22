import socket

from telegram_mcp.runtime.bootstrap import bootstrap_status


def test_off_means_nothing_listens():
    status = bootstrap_status()
    assert status["state"] == "OFF"
    for port in (8766, 8767):
        with socket.socket() as sock:
            sock.settimeout(1)
            try:
                sock.connect(("127.0.0.1", port))
            except OSError:
                continue
            raise AssertionError(f"port {port} accepts while OFF")
