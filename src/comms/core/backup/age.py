"""The age-v1 X25519 subset (https://age-encryption.org/v1), for comms backups (Task B22).

Binary format only; X25519 recipients only; other stanza types are parsed and ignored. The
header MAC is verified before any payload byte is decrypted, and the payload is STREAM:
64 KiB ChaCha20-Poly1305 chunks with a big-endian counter and a last-chunk flag.
Every error falls in one of four classes, as the C2SP testkit names them: a header failure,
no matching identity, an HMAC failure, or a payload failure. Built only on ``cryptography``.
"""

from __future__ import annotations

import base64
import hmac
import re
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

__all__ = [
    "AgeError",
    "AgeHeaderError",
    "AgeHmacError",
    "AgeNoMatch",
    "AgePayloadError",
    "decrypt",
    "encrypt",
    "generate_identity",
    "recipient_of",
]

_VERSION = b"age-encryption.org/v1"
_X25519_INFO = b"age-encryption.org/v1/X25519"
_CHUNK = 64 * 1024
_TAG = 16
_COLUMNS = 64
_B64 = re.compile(rb"[A-Za-z0-9+/]*")
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


class AgeError(Exception):
    """An age file was refused. Fixed messages; never key material or plaintext."""


class AgeHeaderError(AgeError):
    pass


class AgeNoMatch(AgeError):
    pass


class AgeHmacError(AgeError):
    pass


class AgePayloadError(AgeError):
    pass


# -- encodings ----------------------------------------------------------------


def _b64decode(text: bytes) -> bytes:
    """Canonical, unpadded standard base64 (age's header encoding)."""
    if _B64.fullmatch(text) is None or len(text) % 4 == 1:
        raise AgeHeaderError("header base64 refused")
    raw = base64.b64decode(text + b"=" * (-len(text) % 4), validate=True)
    if base64.b64encode(raw).rstrip(b"=") != text:
        raise AgeHeaderError("header base64 is not canonical")
    return raw


def _b64encode(raw: bytes) -> bytes:
    return base64.b64encode(raw).rstrip(b"=")


def _polymod(values: list[int]) -> int:
    generator = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    check = 1
    for value in values:
        top = check >> 25
        check = (check & 0x1FFFFFF) << 5 ^ value
        for i in range(5):
            check ^= generator[i] if (top >> i) & 1 else 0
    return check


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convert(data: list[int], source: int, target: int, *, pad: bool) -> list[int]:
    acc = bits = 0
    out = []
    maxv = (1 << target) - 1
    for value in data:
        acc = (acc << source) | value
        bits += source
        while bits >= target:
            bits -= target
            out.append((acc >> bits) & maxv)
    if pad and bits:
        out.append((acc << (target - bits)) & maxv)
    elif not pad and (bits >= source or (acc << (target - bits)) & maxv):
        raise ValueError("non-zero padding")
    return out


def _bech32_decode(text: str, hrp: str) -> bytes:
    if text != text.lower() and text != text.upper():
        raise ValueError("mixed case")
    text = text.lower()
    split = text.rfind("1")
    if text[:split] != hrp or split + 7 > len(text):
        raise ValueError("wrong type")
    data = [_BECH32.index(c) for c in text[split + 1 :]]
    if _polymod(_hrp_expand(hrp) + data) != 1:
        raise ValueError("bad checksum")
    return bytes(_convert(data[:-6], 5, 8, pad=False))


def _bech32_encode(hrp: str, raw: bytes) -> str:
    data = _convert(list(raw), 8, 5, pad=True)
    check = _polymod(_hrp_expand(hrp) + data + [0] * 6) ^ 1
    return (
        hrp
        + "1"
        + "".join(_BECH32[d] for d in data + [(check >> 5 * (5 - i)) & 31 for i in range(6)])
    )


def _identity_key(identity: str) -> X25519PrivateKey:
    try:
        raw = _bech32_decode(identity, "age-secret-key-")
    except (ValueError, IndexError):
        raise AgeError("identity refused") from None
    if len(raw) != 32:
        raise AgeError("identity refused")
    return X25519PrivateKey.from_private_bytes(raw)


def generate_identity() -> str:
    return _bech32_encode("age-secret-key-", secrets.token_bytes(32)).upper()


def recipient_of(identity: str) -> str:
    public = _identity_key(identity).public_key().public_bytes_raw()
    return _bech32_encode("age", public)


def _recipient_key(recipient: str) -> X25519PublicKey:
    try:
        raw = _bech32_decode(recipient, "age")
    except (ValueError, IndexError):
        raise AgeError("recipient refused") from None
    if len(raw) != 32 or recipient != recipient.lower():
        raise AgeError("recipient refused")
    return X25519PublicKey.from_public_bytes(raw)


# -- keys -----------------------------------------------------------------------


def _hkdf(ikm: bytes, salt: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(ikm)


def _wrap_key(shared: bytes, share: bytes, recipient: bytes) -> bytes:
    if shared == bytes(32):
        raise AgeHeaderError("X25519 shared secret is all zero")
    return _hkdf(shared, share + recipient, _X25519_INFO)


def _mac(file_key: bytes, header: bytes) -> bytes:
    return hmac.new(_hkdf(file_key, b"", b"header"), header, "sha256").digest()


# -- header ---------------------------------------------------------------------


def _parse_header(data: bytes) -> tuple[list[tuple[list[bytes], bytes]], bytes, bytes, int]:
    """(stanzas, the MAC'd header bytes, the MAC, the payload offset)."""
    position = 0

    def line() -> bytes:
        nonlocal position
        end = data.find(b"\n", position)
        if end < 0:
            raise AgeHeaderError("header is truncated")
        text, position = data[position:end], end + 1
        return text

    if line() != _VERSION:
        raise AgeHeaderError("unsupported version")
    stanzas: list[tuple[list[bytes], bytes]] = []
    while True:
        start = position
        text = line()
        if text.startswith(b"---"):
            if not text.startswith(b"--- "):
                raise AgeHeaderError("malformed MAC line")
            mac = _b64decode(text[4:])
            if len(mac) != 32:
                raise AgeHeaderError("malformed MAC")
            if len(data) - position < 16:
                raise AgeHeaderError("payload nonce is missing")  # the testkit's classification
            return stanzas, data[: start + 3], mac, position
        if not text.startswith(b"-> "):
            raise AgeHeaderError("malformed stanza")
        arguments = text[3:].split(b" ")
        if any(not arg or any(b < 33 or b > 126 for b in arg) for arg in arguments):
            raise AgeHeaderError("malformed stanza argument")
        body = b""
        while True:
            chunk = line()
            if len(chunk) > _COLUMNS:
                raise AgeHeaderError("stanza body line too long")
            body += chunk
            if len(chunk) < _COLUMNS:
                break
        stanzas.append((arguments, _b64decode(body)))


def _unwrap(stanzas: list[tuple[list[bytes], bytes]], identity: X25519PrivateKey) -> bytes:
    recipient = identity.public_key().public_bytes_raw()
    for arguments, body in stanzas:
        if arguments[0] != b"X25519":
            continue  # another recipient type: not ours to open
        if len(arguments) != 2:
            raise AgeHeaderError("X25519 stanza takes one argument")
        share = _b64decode(arguments[1])
        if len(share) != 32 or len(body) != 32:
            raise AgeHeaderError("X25519 stanza is malformed")
        try:
            shared = identity.exchange(X25519PublicKey.from_public_bytes(share))
        except ValueError:  # cryptography refuses an all-zero result (a low-order share)
            raise AgeHeaderError("X25519 shared secret is all zero") from None
        key = _wrap_key(shared, share, recipient)
        try:
            return ChaCha20Poly1305(key).decrypt(bytes(12), body, None)
        except InvalidTag:
            continue
    raise AgeNoMatch("no identity matched")


# -- payload --------------------------------------------------------------------


def _nonce(counter: int, last: bool) -> bytes:
    return counter.to_bytes(11, "big") + (b"\x01" if last else b"\x00")


def _open_payload(payload: bytes, file_key: bytes) -> bytes:
    aead = ChaCha20Poly1305(_hkdf(file_key, payload[:16], b"payload"))
    body = payload[16:]
    if not body:
        raise AgePayloadError("payload has no chunks")
    size = _CHUNK + _TAG
    chunks = [body[i : i + size] for i in range(0, len(body), size)]
    plaintext = bytearray()
    for counter, chunk in enumerate(chunks):
        last = counter == len(chunks) - 1
        if len(chunk) < _TAG:
            raise AgePayloadError("payload chunk is short")
        try:
            opened = aead.decrypt(_nonce(counter, last), chunk, None)
        except InvalidTag:
            raise AgePayloadError("payload chunk does not authenticate") from None
        if last and not opened and counter > 0:
            raise AgePayloadError("final chunk is empty")
        plaintext += opened
    return bytes(plaintext)


def decrypt(ciphertext: bytes, identity: str) -> bytes:
    key = _identity_key(identity)
    stanzas, header, mac, offset = _parse_header(ciphertext)
    file_key = _unwrap(stanzas, key)
    if not hmac.compare_digest(_mac(file_key, header), mac):
        raise AgeHmacError("header MAC does not verify")
    return _open_payload(ciphertext[offset:], file_key)


def encrypt(plaintext: bytes, recipient: str) -> bytes:
    public = _recipient_key(recipient)
    file_key = secrets.token_bytes(16)
    ephemeral = X25519PrivateKey.generate()
    share = ephemeral.public_key().public_bytes_raw()
    key = _wrap_key(ephemeral.exchange(public), share, public.public_bytes_raw())
    body = _b64encode(ChaCha20Poly1305(key).encrypt(bytes(12), file_key, None))
    lines = [body[i : i + _COLUMNS] for i in range(0, len(body), _COLUMNS)]
    if not lines or len(lines[-1]) == _COLUMNS:
        lines.append(b"")
    header = b"\n".join([_VERSION, b"-> X25519 " + _b64encode(share), *lines, b"---"])
    out = bytearray(header + b" " + _b64encode(_mac(file_key, header)) + b"\n")
    nonce = secrets.token_bytes(16)
    out += nonce
    aead = ChaCha20Poly1305(_hkdf(file_key, nonce, b"payload"))
    pieces = [plaintext[i : i + _CHUNK] for i in range(0, len(plaintext), _CHUNK)] or [b""]
    for counter, piece in enumerate(pieces):
        out += aead.encrypt(_nonce(counter, counter == len(pieces) - 1), piece, None)
    return bytes(out)
