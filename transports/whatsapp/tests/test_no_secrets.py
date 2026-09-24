import re
import subprocess
from pathlib import Path

_PEM = re.compile(rb"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")
_LONG_HEX_ASSIGN = re.compile(rb"""=\s*["'][0-9a-fA-F]{64,}["']""")
_ALLOW = {"src/whatsvault/db/connection.py"}


def _tracked_files():
    out = subprocess.check_output(["git", "ls-files"], text=True)
    return [f for f in out.splitlines() if f]


def test_no_private_keys_or_key_literals_committed():
    offenders = []
    for f in _tracked_files():
        if f.endswith((".db", ".db-wal", ".db-shm")) or f == ".env":
            offenders.append(f + " (secret-bearing file type must not be tracked)")
            continue
        try:
            data = Path(f).read_bytes()
        except (IsADirectoryError, FileNotFoundError):
            continue
        if _PEM.search(data):
            offenders.append(f + " (PEM private key)")
        if _LONG_HEX_ASSIGN.search(data) and f not in _ALLOW:
            offenders.append(f + " (long hex key literal)")
    assert offenders == [], f"potential secrets committed: {offenders}"
