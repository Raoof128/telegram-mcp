import glob
import plistlib

from whatsvault.ops import launchd


def test_all_shipped_plists_validate():
    plists = glob.glob("apps/launchd/*.plist")
    assert len(plists) >= 4
    for p in plists:
        f = {x["check"]: x["ok"] for x in launchd.validate(p)}
        assert all(f.values()), (p, f)


def test_inline_secret_is_flagged(tmp_path):
    bad = tmp_path / "bad.plist"
    with bad.open("wb") as fh:
        plistlib.dump(
            {
                "Label": "x",
                "ProgramArguments": ["a"],
                "RunAtLoad": True,
                "KeepAlive": True,
                "StandardOutPath": "/x/logs/x.log",
                "StandardErrorPath": "/x/logs/x.log",
                "EnvironmentVariables": {"META_TOKEN": "abcdef0123456789abcdef0123456789"},
            },
            fh,
        )
    f = {x["check"]: x["ok"] for x in launchd.validate(str(bad))}
    assert f["no_inline_secret"] is False


def test_missing_keepalive_flagged(tmp_path):
    bad = tmp_path / "nokeepalive.plist"
    with bad.open("wb") as fh:
        plistlib.dump(
            {
                "Label": "x",
                "ProgramArguments": ["a"],
                "RunAtLoad": True,
                "StandardOutPath": "/logs/x",
                "StandardErrorPath": "/logs/x",
            },
            fh,
        )
    f = {x["check"]: x["ok"] for x in launchd.validate(str(bad))}
    assert f["keepalive_true"] is False
