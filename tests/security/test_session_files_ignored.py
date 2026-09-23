import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "name", ["primary.session", "primary.session-journal", "a/b/x.session-wal", "x.session-shm"]
)
def test_git_refuses_to_track_session_files(name):
    done = subprocess.run(["git", "check-ignore", "--no-index", "-q", name], cwd=REPO, check=False)
    assert done.returncode == 0, f"{name} is not ignored"
