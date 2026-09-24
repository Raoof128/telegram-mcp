# Contributing to WhatsVault

Thanks for your interest. This project holds people's private messages, so the
bar for changes is deliberately higher than a typical repository — especially for
anything touching a security boundary.

## Getting set up

**Requirements:** macOS, Python 3.11+, and SQLCipher (`brew install sqlcipher`).
`sqlcipher3` compiles from source against it; there is no macOS arm64 wheel.

```bash
git clone https://github.com/Raoof128/whatsvault.git
cd whatsvault
make install                 # .venv + the project with dev extras
.venv/bin/pre-commit install # run the same gates CI does, before each commit
make check                   # lint + format check + full suite
```

`make help` lists every target.

## The workflow

1. **Open an issue first** for anything non-trivial, so the approach can be
   agreed before you invest in it.
2. **Branch from `main`.** `main` is protected; a pre-commit hook refuses direct
   commits to it.
3. **Write the failing test first.** This codebase is test-driven throughout, and
   a change that arrives without a test that would have caught the bug is
   incomplete.
4. **Keep `make check` green.** Lint, format, and the whole suite.
5. **Open a pull request** describing what changed and, more importantly, *why* —
   and what you verified rather than assumed.

## Test-driven, specifically

The expectation is stronger than "please include tests":

- **Write the test before the fix, and watch it fail.** A test that has never
  failed has not been shown to test anything.
- **Assert the behaviour, not the implementation.** In `tests/adversarial/`,
  tests are written as the *attacker's goal* so that a refactor cannot quietly
  reopen a hole while keeping the test green.
- **Test the failure path.** Most defects found in this project lived in error
  handling: an audit record written before the call it described, a limit that a
  negative value escaped, a daemon that reported success while doing nothing.
- **Prefer a real boundary over a mock.** The MCP transport tests boot a real
  socket, because an in-process call cannot exercise the ASGI lifespan and would
  have passed while the server was unusable.

## Style

Formatting and linting are enforced by [ruff](https://docs.astral.sh/ruff/); run
`make format`. Beyond that:

- **Comments explain *why*, never *what*.** The code already says what it does.
  A comment earns its place by recording a constraint, a rejected alternative, or
  a non-obvious consequence.
- **Name the invariant.** Where a line exists to uphold a documented invariant or
  a corrections-ledger item, say so (`# INV-ACK: …`, `# ledger #23`). This is how
  a future reader knows the line is load-bearing.
- **Never suppress a lint rule silently.** Every `# noqa` carries its reason, and
  every entry in the `pyproject.toml` ignore list is annotated. If you cannot
  articulate why the rule is wrong here, it probably is not.
- **Match the surrounding code.** This codebase is deliberately dense in places —
  SQL literals, table-like fixtures. Consistency beats personal preference.

## Changes that touch a security boundary

If your change affects any of the following, say so explicitly in the pull
request and expect closer review:

- The MCP tool surface, its authentication, redaction, or the `LOCAL_ONLY` fence
- The OAuth authorization server, the request router in front of it, or anything
  that decides which paths bypass the bearer gate
- The approval chain: drafts, nonces, signatures, device enrolment, policy
- Sealed envelopes, key handling, or anything reading from the Keychain
- The audit log
- The ingest ACK path

For these, the pull request should state:

- **Which invariant** the change preserves or affects (see [SECURITY.md](SECURITY.md))
- **What you tried in order to break it**, and what happened
- **A regression test** in `tests/adversarial/` if the change closes a hole

Adding a tool to the MCP surface requires a corresponding update to the
negative-surface assertion. A pull request that introduces a write verb to that
surface will not be accepted — it is the property the whole design protects.

## Things that will be rejected

- Anything that puts a credential in an environment variable, a plist, a log, or
  a config file. Keys live in the Keychain; this is not negotiable.
- Anything that logs message content, or a field that could carry it.
- Anything that returns a full phone number, or an identifier that trivially
  decodes to one, through the MCP surface.
- A "temporary" bypass of the approval chain, for any reason.
- Committing a real export, database, or anything from `imports/`.

## Reporting bugs

Use the issue templates. For a **security** issue, do not open a public issue —
follow [SECURITY.md](SECURITY.md) instead.

A good bug report includes the command you ran, what you expected, what happened,
the output of `whatsvault doctor`, and your macOS and Python versions. Please
redact message content and phone numbers from anything you paste.

## Code of Conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
