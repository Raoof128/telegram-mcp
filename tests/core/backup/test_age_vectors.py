"""comms v0.3 Task B22: the age-v1 X25519 subset against the vendored C2SP testkit vectors."""

import hashlib
import re
import zlib
from pathlib import Path

import pytest

from comms.core.backup import age

VECTORS = Path(__file__).resolve().parents[2] / "fixtures" / "age"
EXPECT = {
    "header failure": age.AgeHeaderError,
    "payload failure": age.AgePayloadError,
    "no match": age.AgeNoMatch,
    "HMAC failure": age.AgeHmacError,
}


def _vectors():
    return sorted(p for p in VECTORS.iterdir() if p.name != "SOURCE.md")


def _load(path: Path) -> tuple[dict[str, list[str]], bytes]:
    head, _, body = path.read_bytes().partition(b"\n\n")
    fields: dict[str, list[str]] = {}
    for line in head.decode().splitlines():
        key, _, value = line.partition(": ")
        fields.setdefault(key, []).append(value)
    if fields.get("compressed") == ["zlib"]:
        body = zlib.decompress(body)
    return fields, body


def test_the_vendored_files_are_the_recorded_ones():
    recorded = dict(
        re.findall(r"\| `([^`]+)` \| `([0-9a-f]{64})` \|", (VECTORS / "SOURCE.md").read_text())
    )
    assert len(recorded) == 66
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in _vectors()} == recorded


def _decrypt(fields, body):
    last: Exception | None = None
    for identity in fields["identity"]:
        try:
            return age.decrypt(body, identity)
        except age.AgeNoMatch as exc:
            last = exc
    assert last is not None
    raise last


@pytest.mark.parametrize("path", _vectors(), ids=lambda p: p.name)
def test_every_vendored_x25519_vector(path):
    fields, body = _load(path)
    (expect,) = fields["expect"]
    if expect == "success":
        plaintext = _decrypt(fields, body)
        assert hashlib.sha256(plaintext).hexdigest() == fields["payload"][0]
    else:
        with pytest.raises(EXPECT[expect]):
            _decrypt(fields, body)


def test_round_trip_through_encrypt():
    identity = age.generate_identity()
    for size in (0, 1, 65536, 65537, 3 * 65536):
        plaintext = bytes(range(256)) * (size // 256) + b"x" * (size % 256)
        assert (
            age.decrypt(age.encrypt(plaintext, age.recipient_of(identity)), identity) == plaintext
        )
