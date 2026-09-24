"""whatsvault CLI entry point. Thin argparse dispatcher over commands.COMMANDS."""

import argparse
import json
import sys

from . import commands

_OPT_FLAGS = [
    "--device-id",
    "--job-id",
    "--candidate-id",
    "--decision",
    "--dlq-id",
    "--path",
    "--conversation-id",
    "--account-id",
    "--visibility",
    "--timezone",
    "--date-format",
    "--self-label",
    "--code",
    "--subject",
    "--type",
]
_BOOL_FLAGS = ["--reveal", "--dry-run"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="whatsvault")
    sub = p.add_subparsers(dest="verb")
    for verb in sorted(commands.COMMANDS):
        sp = sub.add_parser(verb)
        for flag in _OPT_FLAGS:
            sp.add_argument(flag, default=None)
        for flag in _BOOL_FLAGS:
            sp.add_argument(flag, action="store_true", default=False)
    return p


def run(argv, ctx) -> int:
    args = build_parser().parse_args(argv)
    if not args.verb:
        return 2
    result = commands.COMMANDS[args.verb](ctx, {k.replace("-", "_"): v for k, v in vars(args).items()})
    print(json.dumps(result, default=str))
    return 0 if result.get("ok", True) else 1


def open_ctx(verb, p, ks):
    """Build the context a verb needs.

    A bootstrap verb runs on a machine that has no vault, so it is handed no
    connections at all — opening one is exactly what it cannot do yet. Opening
    them unconditionally here made `whatsvault init` fail with KeyMissing before
    dispatch, which meant the vault could never be created (see
    tests/test_cli_entry_bootstrap.py).
    """
    from ..db import connection as C

    if verb in commands.BOOTSTRAP_VERBS:
        return commands.Ctx(None, None, ks=ks, paths=p)
    return commands.Ctx(
        C.open_existing("vault", p.vault_db, ks),
        C.open_existing("control", p.control_db, ks),
        ks=ks,
        paths=p,
    )


def main(argv=None, ks=None, p=None) -> int:
    """Console-script entry point. Returns an exit code; setuptools wraps this in
    sys.exit(), so raising SystemExit here would swallow it and report success.

    ks and p are injected by the tests; production resolves the real Keychain and
    ~/.whatsvault. Keep this body thin — everything it can reach must be covered.
    """
    from ..crypto import keystore as KS
    from ..ops import fsperms, paths

    argv = sys.argv[1:] if argv is None else argv
    fsperms.harden_umask()
    p = paths.from_env() if p is None else p
    ks = KS.KeyringKeyStore() if ks is None else ks

    verb = argv[0] if argv else None
    try:
        ctx = open_ctx(verb, p, ks)
    except KS.KeyMissing as exc:
        # KeyMissing subclasses KeyStoreError, so it must be caught first: an
        # unprovisioned vault reported as a broken keystore sends the operator to
        # fix the wrong thing.
        print(json.dumps({"ok": False, "error": f"no vault yet ({exc}); run `whatsvault init` first"}))
        return 2
    except KS.KeyStoreError as exc:
        print(json.dumps({"ok": False, "error": f"cannot reach the Keychain: {exc}"}))
        return 2
    return run(argv, ctx)
