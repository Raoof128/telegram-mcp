"""Hostile-ZIP guard (spec §8, ledger #31).

Rejects path traversal (../, backslash, absolute POSIX/Windows), symlink entries,
over-count, per-file over-size, compression-ratio bombs, and enforces a STREAMING
expanded-byte cap (counts bytes actually decompressed, so a lying size header is
still caught). Files are classified by EXTENSION ONLY — this guard does not sniff
content types, and does not claim to."""

import os
import re
import stat
import zipfile

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class HostileZip(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


class AmbiguousTranscript(Exception):
    pass


def _is_unsafe_name(name: str) -> bool:
    if not name:
        return True
    if name.startswith(("/", "\\")) or "\\" in name:
        return True
    if _DRIVE_RE.match(name):
        return True
    return ".." in name.split("/")


def safe_extract(
    zip_path: str, dest_dir: str, *, max_files: int, max_total_bytes: int, max_ratio: int, max_file_bytes: int
) -> list[str]:
    os.makedirs(dest_dir, exist_ok=True)
    os.chmod(dest_dir, 0o700)
    dest_real = os.path.realpath(dest_dir)
    extracted: list[str] = []
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > max_files:
            raise HostileZip("TOO_MANY", f"{len(infos)} entries > {max_files}")
        for info in infos:
            name = info.filename
            if stat.S_ISLNK(info.external_attr >> 16):
                raise HostileZip("SYMLINK", name)
            if _is_unsafe_name(name):
                raise HostileZip("TRAVERSAL", name)
            target = os.path.realpath(os.path.join(dest_dir, name))
            if target != dest_real and not target.startswith(dest_real + os.sep):
                raise HostileZip("TRAVERSAL", name)
            if name.endswith("/"):
                continue
            if info.file_size > max_file_bytes:
                raise HostileZip("FILE_TOO_LARGE", name)
            if info.compress_size > 0 and info.file_size / info.compress_size > max_ratio:
                raise HostileZip("RATIO", name)
            os.makedirs(os.path.dirname(target) or dest_dir, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                while True:
                    chunk = src.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_total_bytes:
                        raise HostileZip("TOO_LARGE", name)
                    dst.write(chunk)
            extracted.append(target)
    return extracted


def find_transcript(names: list[str]) -> str:
    txts = [n for n in names if n.lower().endswith(".txt")]
    if len(txts) != 1:
        raise AmbiguousTranscript(f"expected exactly one .txt transcript, found {len(txts)}")
    return txts[0]
