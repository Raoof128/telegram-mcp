import os
import stat
import zipfile

import pytest

from whatsvault.importers import zip_guard as Z

_LIM = {"max_files": 100, "max_total_bytes": 10_000, "max_ratio": 500, "max_file_bytes": 10_000}


def _zip(path, entries):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)


def test_normal_extract_dir_is_0700_and_find(tmp_path):
    zp = tmp_path / "a.zip"
    _zip(zp, [("chat.txt", "13/04/2026, 5:32 pm - Mona: hi\n"), ("img.jpg", "x")])
    dest = tmp_path / "out"
    files = Z.safe_extract(str(zp), str(dest), **_LIM)
    assert any(f.endswith("chat.txt") for f in files)
    assert oct(stat.S_IMODE(os.stat(dest).st_mode)) == "0o700"
    names = [os.path.basename(f) for f in files]
    assert Z.find_transcript(names).endswith("chat.txt")


def test_traversal_rejected(tmp_path):
    zp = tmp_path / "t.zip"
    _zip(zp, [("../escape.txt", "x")])
    with pytest.raises(Z.HostileZip):
        Z.safe_extract(str(zp), str(tmp_path / "o1"), **_LIM)


def test_backslash_windows_path_rejected(tmp_path):
    zp = tmp_path / "w.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("..\\evil.txt", "x")
    with pytest.raises(Z.HostileZip):
        Z.safe_extract(str(zp), str(tmp_path / "o2"), **_LIM)


def test_symlink_rejected(tmp_path):
    zp = tmp_path / "s.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zi = zipfile.ZipInfo("link")
        zi.external_attr = 0o120777 << 16  # S_IFLNK
        zf.writestr(zi, "/etc/passwd")
    with pytest.raises(Z.HostileZip):
        Z.safe_extract(str(zp), str(tmp_path / "o3"), **_LIM)


def test_streaming_byte_cap_rejects_oversize(tmp_path):
    zp = tmp_path / "big.zip"
    _zip(zp, [("chat.txt", "A" * 5000)])
    with pytest.raises(Z.HostileZip):
        Z.safe_extract(
            str(zp),
            str(tmp_path / "o4"),
            max_files=100,
            max_total_bytes=1000,
            max_ratio=100_000,
            max_file_bytes=1_000_000,
        )


def test_two_transcripts_ambiguous():
    with pytest.raises(Z.AmbiguousTranscript):
        Z.find_transcript(["a.txt", "b.txt", "c.jpg"])


def test_zero_transcripts_ambiguous():
    with pytest.raises(Z.AmbiguousTranscript):
        Z.find_transcript(["only.jpg"])
