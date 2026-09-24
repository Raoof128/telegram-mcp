"""Filesystem hardening (ledger #52): dirs 0700, secret files 0600, umask 077.
chmod is applied AFTER creation so the result is independent of the process umask."""

import os


def harden_umask() -> int:
    return os.umask(0o077)


def ensure_dir(path: str, mode: int = 0o700) -> None:
    os.makedirs(path, exist_ok=True)
    os.chmod(path, mode)


def ensure_secret_file(path: str, mode: int = 0o600) -> None:
    if not os.path.exists(path):
        fd = os.open(path, os.O_CREAT | os.O_WRONLY, mode)
        os.close(fd)
    os.chmod(path, mode)


def check(paths) -> list:
    findings = []
    for d in paths.all_dirs():
        if os.path.isdir(d):
            m = os.stat(d).st_mode & 0o777
            findings.append({"check": "dir_mode", "ok": m == 0o700, "detail": f"{d} mode {oct(m)}"})
    for f in paths.secret_files():
        if os.path.isfile(f):
            m = os.stat(f).st_mode & 0o777
            findings.append({"check": "file_mode", "ok": m == 0o600, "detail": f"{f} mode {oct(m)}"})
    return findings
