import os
import stat

from whatsvault.ops import fsperms, paths


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_from_env_honours_home(tmp_path):
    p = paths.from_env({"WHATSVAULT_HOME": str(tmp_path / "wv")})
    assert p.home == str(tmp_path / "wv")
    assert p.vault_db.endswith("vault.db") and p.control_db.endswith("control.db")


def test_ensure_dir_is_0700_under_permissive_umask(tmp_path):
    old = os.umask(0o022)
    try:
        d = str(tmp_path / "run")
        fsperms.ensure_dir(d)
        assert _mode(d) == 0o700
    finally:
        os.umask(old)


def test_ensure_secret_file_is_0600(tmp_path):
    f = str(tmp_path / "secret.bin")
    fsperms.ensure_secret_file(f)
    assert _mode(f) == 0o600


def test_check_flags_loosened_dir(tmp_path):
    p = paths.from_env({"WHATSVAULT_HOME": str(tmp_path / "wv")})
    fsperms.ensure_dir(p.home)
    fsperms.ensure_dir(p.blobs_dir)
    os.chmod(p.blobs_dir, 0o755)  # loosen
    findings = fsperms.check(p)
    assert any(not f["ok"] and p.blobs_dir in f["detail"] for f in findings)
