"""comms v0.3 Task B23: age negatives (Phase-5 §4.3) and bidirectional interop with age(1)."""

import base64
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from comms.core.backup import age

AGE = Path("/opt/homebrew/bin/age")
IDENTITY = age.generate_identity()
RECIPIENT = age.recipient_of(IDENTITY)
PLAINTEXT = b"backup body " * 9000  # two chunks


def _parts(blob: bytes):
    header_end = blob.index(b"\n--- ") + 1
    mac_end = blob.index(b"\n", header_end) + 1
    return blob[:header_end], blob[header_end:mac_end], blob[mac_end:]


def _stanza_lines(blob: bytes) -> list[bytes]:
    return blob[: blob.index(b"\n--- ")].split(b"\n")


def _with_lines(blob: bytes, lines: list[bytes]) -> bytes:
    _header, mac, payload = _parts(blob)
    return b"\n".join(lines) + b"\n" + mac + payload


def test_non_canonical_base64():
    blob = age.encrypt(PLAINTEXT, RECIPIENT)
    lines = _stanza_lines(blob)
    share = lines[1].split(b" ")[2]
    last = share[-1:]
    alphabet = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    tweaked = alphabet[
        alphabet.index(last) ^ 1 : alphabet.index(last) ^ 1 + 1
    ]  # flips a padding bit
    lines[1] = b"-> X25519 " + share[:-1] + tweaked
    with pytest.raises(age.AgeHeaderError):
        age.decrypt(_with_lines(blob, lines), IDENTITY)


def test_wrong_stanza_length():
    blob = age.encrypt(PLAINTEXT, RECIPIENT)
    lines = _stanza_lines(blob)
    body = base64.b64decode(lines[2] + b"=" * (-len(lines[2]) % 4))
    lines[2] = base64.b64encode(body[:31]).rstrip(b"=")
    with pytest.raises(age.AgeHeaderError):
        age.decrypt(_with_lines(blob, lines), IDENTITY)


def test_all_zero_shared_secret():
    blob = age.encrypt(PLAINTEXT, RECIPIENT)
    lines = _stanza_lines(blob)
    lines[1] = b"-> X25519 " + base64.b64encode(bytes(32)).rstrip(b"=")  # the zero point
    with pytest.raises(age.AgeHeaderError):
        age.decrypt(_with_lines(blob, lines), IDENTITY)


def test_header_mac_altered():
    header, mac, payload = _parts(age.encrypt(PLAINTEXT, RECIPIENT))
    raw = base64.b64decode(mac[4:-1] + b"=")
    forged = b"--- " + base64.b64encode(bytes([raw[0] ^ 1]) + raw[1:]).rstrip(b"=") + b"\n"
    with pytest.raises(age.AgeHmacError):
        age.decrypt(header + forged + payload, IDENTITY)


def test_stanza_altered():
    blob = age.encrypt(PLAINTEXT, RECIPIENT)
    lines = _stanza_lines(blob)
    body = bytearray(base64.b64decode(lines[2] + b"=" * (-len(lines[2]) % 4)))
    body[0] ^= 1
    lines[2] = base64.b64encode(bytes(body)).rstrip(b"=")
    with pytest.raises(age.AgeNoMatch):
        age.decrypt(_with_lines(blob, lines), IDENTITY)


def test_chunk_altered():
    blob = bytearray(age.encrypt(PLAINTEXT, RECIPIENT))
    blob[-100] ^= 1
    with pytest.raises(age.AgePayloadError):
        age.decrypt(bytes(blob), IDENTITY)


def test_truncated_final_chunk():
    with pytest.raises(age.AgePayloadError):
        age.decrypt(age.encrypt(PLAINTEXT, RECIPIENT)[:-10], IDENTITY)


def _handmade(file_key: bytes, chunks: list[tuple[bytes, bool]]) -> bytes:
    """A valid header for ``file_key``, then chunks sealed with the given last-chunk flags."""
    ephemeral = X25519PrivateKey.generate()
    share = ephemeral.public_key().public_bytes_raw()
    public = age._recipient_key(RECIPIENT)
    wrap = age._wrap_key(ephemeral.exchange(public), share, public.public_bytes_raw())
    body = age._b64encode(ChaCha20Poly1305(wrap).encrypt(bytes(12), file_key, None))
    header = b"\n".join(
        [b"age-encryption.org/v1", b"-> X25519 " + age._b64encode(share), body, b"---"]
    )
    nonce = secrets.token_bytes(16)
    aead = ChaCha20Poly1305(age._hkdf(file_key, nonce, b"payload"))
    out = header + b" " + age._b64encode(age._mac(file_key, header)) + b"\n" + nonce
    for counter, (piece, last) in enumerate(chunks):
        out += aead.encrypt(age._nonce(counter, last), piece, None)
    return out


def test_missing_last_chunk_flag():
    key = secrets.token_bytes(16)
    assert age.decrypt(
        _handmade(key, [(b"x" * 65536, False), (b"y", True)]), IDENTITY
    )  # the control
    with pytest.raises(age.AgePayloadError):
        age.decrypt(_handmade(key, [(b"x" * 65536, False), (b"y", False)]), IDENTITY)


def test_trailing_bytes():
    with pytest.raises(age.AgePayloadError):
        age.decrypt(age.encrypt(PLAINTEXT, RECIPIENT) + b"\x00", IDENTITY)


def test_wrong_identity():
    with pytest.raises(age.AgeNoMatch):
        age.decrypt(age.encrypt(PLAINTEXT, RECIPIENT), age.generate_identity())


needs_age = pytest.mark.skipif(
    shutil.which(str(AGE)) is None,
    reason="age(1) not installed at /opt/homebrew/bin/age: interop not run",
)


@needs_age
def test_interop_encrypt_then_age_decrypt(tmp_path):
    (tmp_path / "id.txt").write_text(IDENTITY + "\n")
    (tmp_path / "in.age").write_bytes(age.encrypt(PLAINTEXT, RECIPIENT))
    done = subprocess.run(
        [str(AGE), "-d", "-i", str(tmp_path / "id.txt"), str(tmp_path / "in.age")],
        capture_output=True,
        check=True,
        timeout=60,
    )
    assert done.stdout == PLAINTEXT


@needs_age
def test_interop_age_encrypt_then_decrypt(tmp_path):
    (tmp_path / "plain").write_bytes(PLAINTEXT)
    subprocess.run(
        [str(AGE), "-r", RECIPIENT, "-o", str(tmp_path / "out.age"), str(tmp_path / "plain")],
        check=True,
        timeout=60,
    )
    assert age.decrypt((tmp_path / "out.age").read_bytes(), IDENTITY) == PLAINTEXT
