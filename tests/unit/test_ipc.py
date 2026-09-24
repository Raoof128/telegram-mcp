"""Task 8: lease wire edges, admin socket behavior, tunnel pins."""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import stat
from pathlib import Path

import pytest

from comms.transports.telegram.ipc.admin import (
    ADMIN_COMMANDS,
    AdminRouter,
    serve_admin,
    verify_peer,
)
from comms.transports.telegram.ipc.framing import (
    MAX_FRAME_BYTES,
    FrameError,
    decode_json_frame,
    encode_json_frame,
    read_frame,
)
from comms.transports.telegram.ipc.leases import (
    LEASE_AUDIENCE,
    LeaseError,
    mint_lease,
    verify_lease,
)
from comms.transports.telegram.ipc.tunnel import (
    TunnelPinError,
    add_pin,
    current_pin,
    pin_history,
    rotate_binding,
    spki_digest,
    verify_pin,
)

SEED = b"\x03" * 32
CLIENT = "tcl_" + "d" * 26
OTHER = "tcl_" + "e" * 26
RUNTIME_A = b"\x0a" * 16
RUNTIME_B = b"\x0b" * 16
NONCE = b"\x07" * 16


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _forge(payload_json: str, *, seed: bytes = SEED, runtime_id=None, mac_bytes: int = 32) -> str:
    payload_b64 = _b64(payload_json.encode("utf-8"))
    key = seed
    if runtime_id is not None:
        key = hmac.new(
            seed, b"telegram-mcp-lease-runtime/v1\0" + runtime_id, hashlib.sha256
        ).digest()
    mac = hmac.new(
        key, b"telegram-mcp-local-lease/v1\0" + payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    return f"tgml1.{payload_b64}.{_b64(mac[:mac_bytes])}"


def _payload(**over) -> str:
    fields = {
        "aud": LEASE_AUDIENCE,
        "cid": CLIENT,
        "exp": 1_000_060,
        "iat": 1_000_000,
        "nonce": _b64(NONCE),
        "sec": 7,
        "v": 1,
    }
    fields.update(over)
    return json.dumps(fields, sort_keys=True, separators=(",", ":"))


# --- the pinned round trip --------------------------------------------------


def test_lease_round_trip_and_shape():
    token = mint_lease(seed=SEED, client=CLIENT, epoch=7, now=1_000_000)
    assert token.startswith("tgml1.") and len(token) <= 1024
    claims = verify_lease(token, seeds={CLIENT: SEED}, epoch=7, now=1_000_010)
    assert claims.client.endswith("d" * 26)
    assert claims.aud == LEASE_AUDIENCE
    assert claims.exp == claims.iat + 60


def test_payload_is_canonical_sorted_json_with_exactly_seven_keys():
    token = mint_lease(seed=SEED, client=CLIENT, epoch=7, now=1_000_000, nonce=NONCE)
    payload_b64 = token.split(".")[1]
    raw = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)).decode()
    assert raw == _payload()
    assert "=" not in payload_b64


# --- lifetime and skew edges ------------------------------------------------


def test_lifetime_boundary_accepts_sixty_and_rejects_sixty_one():
    verify_lease(_forge(_payload(exp=1_000_060)), seeds={CLIENT: SEED}, epoch=7, now=1_000_000)
    with pytest.raises(LeaseError):
        verify_lease(_forge(_payload(exp=1_000_061)), seeds={CLIENT: SEED}, epoch=7, now=1_000_000)


def test_equal_iat_and_exp_is_rejected():
    with pytest.raises(LeaseError):
        verify_lease(_forge(_payload(exp=1_000_000)), seeds={CLIENT: SEED}, epoch=7, now=1_000_000)


@pytest.mark.parametrize("now", [999_995, 1_000_065])
def test_skew_of_exactly_five_seconds_is_accepted(now):
    verify_lease(_forge(_payload()), seeds={CLIENT: SEED}, epoch=7, now=now)


@pytest.mark.parametrize("now", [999_994, 1_000_066])
def test_skew_of_six_seconds_is_rejected(now):
    with pytest.raises(LeaseError):
        verify_lease(_forge(_payload()), seeds={CLIENT: SEED}, epoch=7, now=now)


# --- strict decoding --------------------------------------------------------


@pytest.mark.parametrize(
    "payload_json",
    [
        _payload(exp=True),
        _payload(iat=True),
        _payload(sec=True),
        _payload(v=2),
        _payload(v=True),
        _payload(aud="telegram-mcp-tunnel"),
        _payload(nonce=_b64(b"\x07" * 15)),
        _payload(nonce="not base64!!"),
        _payload(cid="tcl_short"),
        _payload(cid=12),
        '{"aud":"telegram-mcp-loopback","cid":"' + CLIENT + '","exp":1000060,"extra":1,'
        '"iat":1000000,"nonce":"' + _b64(NONCE) + '","sec":7,"v":1}',
        '{"aud":"telegram-mcp-loopback","cid":"' + CLIENT + '","exp":1000060,'
        '"iat":1000000,"nonce":"' + _b64(NONCE) + '","sec":7,"sec":7,"v":1}',
        '{"aud":"telegram-mcp-loopback","cid":"' + CLIENT + '","iat":1000000,'
        '"nonce":"' + _b64(NONCE) + '","sec":7,"v":1}',
    ],
)
def test_malformed_payloads_are_rejected(payload_json):
    with pytest.raises(LeaseError):
        verify_lease(_forge(payload_json), seeds={CLIENT: SEED}, epoch=7, now=1_000_000)


def test_truncated_mac_is_rejected():
    with pytest.raises(LeaseError):
        verify_lease(_forge(_payload(), mac_bytes=31), seeds={CLIENT: SEED}, epoch=7, now=1_000_000)


def test_padded_or_whitespaced_base64url_is_rejected():
    token = mint_lease(seed=SEED, client=CLIENT, epoch=7, now=1_000_000)
    prefix, payload, mac = token.split(".")
    for broken in (
        f"{prefix}.{payload}=.{mac}",
        f"{prefix}.{payload} .{mac}",
        f"{prefix}.{payload}.{mac}=",
    ):
        with pytest.raises(LeaseError):
            verify_lease(broken, seeds={CLIENT: SEED}, epoch=7, now=1_000_010)


def test_oversized_token_is_rejected_before_decoding():
    token = mint_lease(seed=SEED, client=CLIENT, epoch=7, now=1_000_000)
    with pytest.raises(LeaseError):
        verify_lease(token + "A" * 1024, seeds={CLIENT: SEED}, epoch=7, now=1_000_010)


def test_non_token_shapes_are_rejected():
    for bad in ("", "tgml1", "tgml1.a", "tgml2.a.b", "tgml1.a.b.c", "tgml1.ä.b"):
        with pytest.raises(LeaseError):
            verify_lease(bad, seeds={CLIENT: SEED}, epoch=7, now=1_000_010)


# --- identity and epoch binding --------------------------------------------


def test_security_epoch_must_match_the_current_epoch():
    token = mint_lease(seed=SEED, client=CLIENT, epoch=7, now=1_000_000)
    with pytest.raises(LeaseError):
        verify_lease(token, seeds={CLIENT: SEED}, epoch=8, now=1_000_010)


def test_unknown_client_slot_is_rejected():
    token = mint_lease(seed=SEED, client=CLIENT, epoch=7, now=1_000_000)
    with pytest.raises(LeaseError):
        verify_lease(token, seeds={OTHER: SEED}, epoch=7, now=1_000_010)


def test_cid_cannot_select_another_clients_key_slot():
    """A token MAC'd with one client's seed but naming another must fail."""
    forged = _forge(_payload(cid=OTHER), seed=SEED)
    with pytest.raises(LeaseError):
        verify_lease(forged, seeds={OTHER: b"\x04" * 32, CLIENT: SEED}, epoch=7, now=1_000_000)


def test_restart_invalidates_every_previous_lease():
    token = mint_lease(seed=SEED, client=CLIENT, epoch=7, now=1_000_000, runtime_id=RUNTIME_A)
    claims = verify_lease(token, seeds={CLIENT: SEED}, epoch=7, now=1_000_010, runtime_id=RUNTIME_A)
    assert claims.client == CLIENT
    with pytest.raises(LeaseError):
        verify_lease(token, seeds={CLIENT: SEED}, epoch=7, now=1_000_010, runtime_id=RUNTIME_B)
    # an unbound verifier is not a way around the binding either
    with pytest.raises(LeaseError):
        verify_lease(token, seeds={CLIENT: SEED}, epoch=7, now=1_000_010)


def test_mint_rejects_an_overlong_lifetime_and_bad_client():
    with pytest.raises(LeaseError):
        mint_lease(seed=SEED, client=CLIENT, epoch=7, now=1_000_000, lifetime_s=61)
    with pytest.raises(LeaseError):
        mint_lease(seed=SEED, client="nope", epoch=7, now=1_000_000)
    with pytest.raises(LeaseError):
        mint_lease(seed=b"\x01" * 16, client=CLIENT, epoch=7, now=1_000_000)


# --- admin socket -----------------------------------------------------------


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    """Short relative socket directory: AF_UNIX paths cap near 104 bytes."""
    monkeypatch.chdir(tmp_path)
    return Path("run")


CHALLENGE_KEY = b"\x01" * 32
RUNTIME_ID = b"\x02" * 16


def _router(**over):
    calls = []

    def lock(args):
        calls.append(("lock", args))
        return {"security_epoch": 2, "locked": True}

    handlers = {"lock": lock, "lock status": lambda args: {"locked": False}}
    handlers.update(over)
    router = AdminRouter(handlers)
    return router, calls


async def _call(socket_path, payload_bytes):
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        writer.write(len(payload_bytes).to_bytes(4, "big") + payload_bytes)
        await writer.drain()
        return decode_json_frame(await read_frame(reader, idle_s=5))
    finally:
        writer.close()


async def test_admin_command_is_served_for_an_authorized_peer(run_dir):
    router, calls = _router()
    server = await serve_admin(run_dir / "admin.sock", router)
    try:
        response = await _call(
            run_dir / "admin.sock",
            encode_json_frame({"cmd": "lock", "args": {}}),
        )
        assert response == {"ok": True, "data": {"security_epoch": 2, "locked": True}}
        assert calls and calls[0][0] == "lock"
        assert stat.S_IMODE((run_dir / "admin.sock").stat().st_mode) == 0o660
        assert stat.S_IMODE((run_dir).stat().st_mode) == 0o770
    finally:
        server.close()
        await server.wait_closed()


async def test_admin_rejects_a_foreign_uid(run_dir):
    router, _ = _router()
    server = await serve_admin(run_dir / "admin.sock", router, allow_uid=os.geteuid() + 12345)
    try:
        response = await _call(run_dir / "admin.sock", encode_json_frame({"cmd": "lock status"}))
        assert response["ok"] is False
        assert response["code"] == "PERMISSION_DENIED"
    finally:
        server.close()
        await server.wait_closed()


async def test_admin_refuses_non_utf8_payloads_without_logging_them(run_dir, caplog):
    router, _ = _router()
    socket_path = run_dir / "admin.sock"
    server = await serve_admin(socket_path, router)
    try:
        with caplog.at_level("WARNING"):
            response = await _call(socket_path, b"\xff\xfe\xfd\xfc")
        assert response["ok"] is False
        assert response["code"] == "MALFORMED_REQUEST"
        assert "\\xff" not in caplog.text and "fffe" not in caplog.text
    finally:
        server.close()
        await server.wait_closed()


async def test_admin_rejects_oversized_frames_before_decoding(run_dir):
    router, _ = _router()
    socket_path = run_dir / "admin.sock"
    server = await serve_admin(socket_path, router)
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        try:
            writer.write((MAX_FRAME_BYTES + 1).to_bytes(4, "big"))
            await writer.drain()
            response = decode_json_frame(await read_frame(reader, idle_s=5))
        finally:
            writer.close()
        assert response["code"] == "MALFORMED_REQUEST"
    finally:
        server.close()
        await server.wait_closed()


async def test_admin_disconnects_an_idle_peer(run_dir):
    router, _ = _router()
    socket_path = run_dir / "admin.sock"
    server = await serve_admin(socket_path, router, idle_s=0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        try:
            response = decode_json_frame(await read_frame(reader, idle_s=5))
            assert response["code"] == "MALFORMED_REQUEST"
            assert "idle" in response["reason"]
        finally:
            writer.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_duplicate_admin_keys_never_dispatch(run_dir):
    router, calls = _router()
    socket_path = run_dir / "admin.sock"
    server = await serve_admin(socket_path, router)
    try:
        response = await _call(socket_path, b'{"cmd": "lock", "cmd": "lock status"}')
        assert response["ok"] is False
        assert response["code"] == "MALFORMED_REQUEST"
        assert calls == []
    finally:
        server.close()
        await server.wait_closed()


def test_every_spec_command_is_routed_and_unknown_names_are_refused():
    router, _ = _router()
    assert len(ADMIN_COMMANDS) == 49  # §33's 51 less the two consent commands (comms spec v0.2)
    for retired in ("consent status", "consent approve"):
        assert router.dispatch({"cmd": retired})["code"] == "UNKNOWN_COMMAND"
    for command in ADMIN_COMMANDS:
        response = router.dispatch({"cmd": command, "args": {}})
        assert response["ok"] is True or response["code"] == "NOT_AVAILABLE_IN_PHASE"
    for bad in ("", "telegram-mcp lock", "rm -rf", "LOCK", "lock  status"):
        response = router.dispatch({"cmd": bad})
        assert response["ok"] is False
        assert response["code"] in ("UNKNOWN_COMMAND", "MALFORMED_REQUEST")


def test_router_rejects_malformed_requests_and_unknown_fields():
    router, _ = _router()
    assert router.dispatch({})["code"] == "MALFORMED_REQUEST"
    assert router.dispatch({"cmd": "lock status", "args": []})["code"] == "MALFORMED_REQUEST"
    assert router.dispatch({"cmd": "lock status", "extra": 1})["code"] == "MALFORMED_REQUEST"


def test_router_refuses_handlers_outside_the_spec_surface():
    with pytest.raises(ValueError):
        AdminRouter({"drop database": lambda args: {}})


def test_router_maps_handler_failures_to_fixed_codes():
    def boom(args):
        raise RuntimeError("internal detail that must not leak")

    def denied(args):
        raise PermissionError

    router = AdminRouter({"doctor": boom, "release verify": denied})
    failure = router.dispatch({"cmd": "doctor"})
    assert failure["code"] == "INTERNAL_ERROR"
    assert "internal detail" not in failure["reason"]
    assert router.dispatch({"cmd": "release verify"})["code"] == "PERMISSION_DENIED"


def test_verify_peer_accepts_the_running_euid_only_by_default():
    assert verify_peer(os.geteuid(), os.getegid()) is True
    assert verify_peer(os.geteuid() + 12345, os.getegid() + 12345) is False
    assert verify_peer(os.geteuid() + 12345, 5150, allow_gids=(5150,)) is True


# --- tunnel pins ------------------------------------------------------------


def _store(tmp_path):
    root = tmp_path / "keys"
    root.mkdir(mode=0o700)
    return root


def test_tunnel_pin_add_verify_and_expiry(tmp_path):
    root = _store(tmp_path)
    pin = spki_digest(b"public-der-bytes")
    with pytest.raises(TunnelPinError):
        verify_pin(root, pin, now=1_000)
    record = add_pin(root, pin, now=1_000)
    assert verify_pin(root, pin, now=1_000 + 89 * 86_400).spki == pin
    with pytest.raises(TunnelPinError):
        verify_pin(root, pin, now=record.expires_at)
    with pytest.raises(TunnelPinError):
        verify_pin(root, spki_digest(b"other"), now=1_000)


def test_tunnel_rotate_binding_retains_history_and_refuses_the_old_pin(tmp_path):
    root = _store(tmp_path)
    old = spki_digest(b"old")
    new = spki_digest(b"new")
    add_pin(root, old, now=1_000)
    replacement = rotate_binding(root, new, now=2_000)
    assert replacement.spki == new
    assert current_pin(root).spki == new
    with pytest.raises(TunnelPinError):
        verify_pin(root, old, now=2_001)
    history = pin_history(root)
    assert [record.spki for record in history] == [old, new]
    assert history[0].retired_at == 2_000
    with pytest.raises(TunnelPinError):
        add_pin(root, spki_digest(b"third"), now=3_000)


def test_tunnel_pin_records_reject_malformed_digests_and_loose_permissions(tmp_path):
    root = _store(tmp_path)
    with pytest.raises(TunnelPinError):
        add_pin(root, "sha256:" + "a" * 64, now=1_000)
    add_pin(root, spki_digest(b"ok"), now=1_000)
    os.chmod(root / "tunnel-pins.jsonl", 0o644)
    with pytest.raises(TunnelPinError):
        pin_history(root)


def test_frame_helpers_reject_oversized_and_non_object_payloads():
    with pytest.raises(FrameError):
        decode_json_frame(b"[1,2,3]")
    with pytest.raises(FrameError):
        decode_json_frame(b"\xff")
