"""comms v0.3 Task D27: cml1 local leases, full bounds; client seeds split daemon/helper (A33, G17)."""

import ast
import base64
import hashlib
import hmac
import json
import logging
import os
import stat
from datetime import timedelta
from pathlib import Path

import pytest

from comms.core import domains
from comms.core.auth import clients, lease_format, leases
from comms.core.auth.leases import LeaseRefused
from comms.core.canonical import jcs_dumps
from comms.core.keys.slots import KeySlotStore
from comms.core.security import bump_security_epoch, security_epoch
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def world(tmp_path):
    conn = fx.migrated(tmp_path)
    store = KeySlotStore(tmp_path / "slots")
    helper = tmp_path / "helper" / "seed"
    helper.parent.mkdir(mode=0o700)
    cli = clients.add_client(conn, store, "claude-code", now=NOW, helper_path=helper)
    return {"conn": conn, "store": store, "cli": cli, "helper": helper, "tmp": tmp_path}


def _seed(world):
    return lease_format.read_helper(world["helper"])[1]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _forge(world, payload, *, seed=None, raw=None):
    """A token over an arbitrary payload, MACed like a real one."""
    body = raw if raw is not None else jcs_dumps(payload)
    mac = hmac.new(seed or _seed(world), domains.LOCAL_LEASE + body, hashlib.sha256).digest()
    return f"cml1.{_b64(body)}.{_b64(mac)}"


def _payload(world, **over):
    iat = int(NOW.timestamp())
    base = {
        "aud": domains.LOCAL_LEASE_AUDIENCE,
        "cid": world["cli"],
        "exp": iat + 60,
        "iat": iat,
        "nonce": _b64(os.urandom(16)),
        "sec": security_epoch(world["conn"]),
        "v": 1,
    }
    return {**base, **over}


def _verify(world, token, *, now=NOW, source="loopback"):
    return leases.verify(world["conn"], world["store"], token, now=now, source=source)


def test_valid_lease_accepted(world):
    token = leases.mint(_seed(world), world["cli"], security_epoch(world["conn"]), now=NOW)
    assert token.startswith("cml1.") and len(token) <= 1024
    assert _verify(world, token) == world["cli"]
    assert _verify(world, token, source="admin_peer") == world["cli"]


def test_helper_copy_is_written_once_0600(world):
    mode = stat.S_IMODE(world["helper"].stat().st_mode)
    assert mode == 0o600 and len(_seed(world)) == 32
    assert lease_format.read_helper(world["helper"])[0] == world["cli"]
    with pytest.raises(clients.ClientError):  # never overwritten
        clients.add_client(world["conn"], world["store"], "x", now=NOW, helper_path=world["helper"])


def test_lifetime_over_60s_refused(world):
    token = _forge(world, _payload(world, exp=int(NOW.timestamp()) + 61))
    with pytest.raises(LeaseRefused):
        _verify(world, token)


@pytest.mark.parametrize("seconds", [31, -91])
def test_skew_beyond_30s_refused(world, seconds):
    token = leases.mint(_seed(world), world["cli"], security_epoch(world["conn"]), now=NOW)
    with pytest.raises(LeaseRefused):
        _verify(world, token, now=NOW - timedelta(seconds=seconds))


def test_short_nonce_refused(world):
    token = _forge(world, _payload(world, nonce=_b64(os.urandom(15))))
    with pytest.raises(LeaseRefused):
        _verify(world, token)


def test_duplicate_json_key_refused(world):
    payload = _payload(world)
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    duplicated = body[:-1] + ',"v":1}'
    with pytest.raises(LeaseRefused):
        _verify(world, _forge(world, None, raw=duplicated.encode()))


def test_extra_or_missing_keys_refused(world):
    for payload in (
        {**_payload(world), "admin": True},
        {k: v for k, v in _payload(world).items() if k != "sec"},
    ):
        with pytest.raises(LeaseRefused):
            _verify(world, _forge(world, payload))


def test_oversize_token_refused(world):
    token = leases.mint(_seed(world), world["cli"], security_epoch(world["conn"]), now=NOW)
    with pytest.raises(LeaseRefused):
        _verify(world, token + "A" * (1025 - len(token)))


def test_wrong_aud_refused(world):
    with pytest.raises(LeaseRefused):
        _verify(world, _forge(world, _payload(world, aud="remote-audience")))


def test_stale_security_epoch_refused(world):
    token = leases.mint(_seed(world), world["cli"], security_epoch(world["conn"]), now=NOW)
    bump_security_epoch(world["conn"])
    with pytest.raises(LeaseRefused):
        _verify(world, token)


def test_rotated_seed_version_refused(world):
    old = leases.mint(_seed(world), world["cli"], security_epoch(world["conn"]), now=NOW)
    fresh = world["tmp"] / "helper" / "seed2"
    clients.rotate_client(world["conn"], world["store"], world["cli"], now=NOW, helper_path=fresh)
    with pytest.raises(LeaseRefused):
        _verify(world, old)
    new = leases.mint(
        lease_format.read_helper(fresh)[1], world["cli"], security_epoch(world["conn"]), now=NOW
    )
    assert _verify(world, new) == world["cli"]


def test_disabled_client_refused(world):
    token = leases.mint(_seed(world), world["cli"], security_epoch(world["conn"]), now=NOW)
    clients.disable_client(world["conn"], world["cli"])
    with pytest.raises(LeaseRefused):
        _verify(world, token)


@pytest.mark.parametrize("source", ["tcp", "remote", "", None])
def test_non_loopback_source_refused(world, source):
    token = leases.mint(_seed(world), world["cli"], security_epoch(world["conn"]), now=NOW)
    with pytest.raises(LeaseRefused):
        _verify(world, token, source=source)


def test_a_forged_mac_is_refused(world):
    with pytest.raises(LeaseRefused):
        _verify(world, _forge(world, _payload(world), seed=os.urandom(32)))


def test_tgml1_token_refused_by_cml1_verifier(world):
    token = leases.mint(_seed(world), world["cli"], security_epoch(world["conn"]), now=NOW)
    with pytest.raises(LeaseRefused):
        _verify(world, "tgml1." + token.removeprefix("cml1."))


def test_mac_compare_is_constant_time():
    source = (ROOT / "src" / "comms" / "core" / "auth" / "leases.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = {
        f"{n.func.value.id}.{n.func.attr}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
    }
    assert "hmac.compare_digest" in calls
    compares = [n for n in ast.walk(tree) if isinstance(n, ast.Compare)]
    for node in compares:  # no == over anything named mac
        names = {x.id for x in ast.walk(node) if isinstance(x, ast.Name)}
        assert not {"mac", "expected", "given"} & names, ast.unparse(node)


def test_seed_never_logged(world, caplog):
    caplog.set_level(logging.DEBUG)
    seed = _seed(world)
    token = leases.mint(seed, world["cli"], security_epoch(world["conn"]), now=NOW)
    _verify(world, token)
    with pytest.raises(LeaseRefused):
        _verify(world, token, source="tcp")
    fresh = world["tmp"] / "helper" / "seed3"
    clients.rotate_client(world["conn"], world["store"], world["cli"], now=NOW, helper_path=fresh)
    text = caplog.text + repr(clients) + repr(world["store"])
    for secret in (seed, lease_format.read_helper(fresh)[1]):
        assert secret.hex() not in text and _b64(secret) not in text


def test_refusals_carry_one_fixed_message(world):
    messages = set()
    for token in ("", "cml1.x.y", "tgml1.a.b", _forge(world, _payload(world, v=2))):
        with pytest.raises(LeaseRefused) as refused:
            _verify(world, token)
        messages.add(str(refused.value))
    assert messages == {"lease refused"}
