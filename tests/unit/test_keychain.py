import subprocess

import pytest

from telegram_mcp.keys.keychain import KeychainError, read_api_hash

GOOD = "0123456789abcdef0123456789abcdef"


def _runner(stdout: str, code: int = 0):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr="")

    return run, calls


def test_reads_and_validates_without_the_secret_in_argv():
    run, calls = _runner(GOOD + "\n")
    assert read_api_hash(runner=run) == GOOD
    assert calls[0][:2] == ["/usr/bin/security", "find-generic-password"]
    assert GOOD not in " ".join(calls[0])


@pytest.mark.parametrize("stdout,code", [("", 44), ("not-hex\n", 0), (GOOD[:-1] + "\n", 0)])
def test_missing_or_malformed_fails_with_a_fixed_message(stdout, code):
    run, _ = _runner(stdout, code)
    with pytest.raises(KeychainError) as exc:
        read_api_hash(runner=run)
    assert GOOD[:8] not in str(exc.value) and "not-hex" not in str(exc.value)
