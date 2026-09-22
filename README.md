# Telegram MCP Gateway (development foundation)

Read-only Telegram MCP gateway — **Phase-1 synthetic foundation plus the
Phase-2a authority foundation**. This build serves a disconnected synthetic
`telegram_status`, advertises the exact ten-tool contracts, and refuses all
nine sensitive calls. Phase 2a adds the privileged-runtime machinery behind
that surface — lifecycle and lock, key store and pairing pins, the consent
broker and its frozen challenge wire, the central authority engine, cursors
and epochs, the 19-table metadata schema, the admin socket, bearer leases, the
RV-1 consent rendezvous, tunnel pins, `doctor` and the install plans — all
exercised headlessly against fakes and a stub signer.

It performs no Telegram login and holds no session. No service account,
LaunchAgent, certificate or socket has been installed on any host, and the
Phase-2J join gate against the real signed consent agent has not run.
Production deployment requires Gates A–R (see the engineering specification).

## Setup

```bash
uv sync --locked
```

## Run the synthetic demo

```bash
uv run telegram-mcp demo
# listening on 127.0.0.1:8766/mcp (synthetic build)
```

Expected: `telegram_status` returns `connected=false`, `authorised=false` with
gateway metadata and no disclosure. The nine sensitive tools return bounded
errors (`POLICY_UNCONFIGURED` for valid inputs). Stop with Ctrl-C.

Only `safe_demo` on a literal loopback address is accepted. Any Telegram
credential environment, non-loopback bind or telemetry-exporter configuration
fails startup with a fixed message.

## Phase-2a verbs

```bash
uv run telegram-mcp status                       # works while OFF
uv run telegram-mcp doctor                       # add --production for the release gate
uv run telegram-mcp keys provision --store-dir <dir>
uv run telegram-mcp admin lock status            # proxied to the admin socket
```

`start` and `stop` drive the three launchd jobs through interactive
escalation and need the one-time install (`scripts/install_service_users.sh`,
`scripts/install_paths.sh`); both installers are idempotent and print their
plan with `--dry-run`. `doctor --production` fails by design in this phase and
names which gate checks cannot yet be verified.

## Verify

```bash
uv run python scripts/extract_contracts.py --check
uv run pytest tests/unit tests/contract tests/integration tests/security -q
uv run pytest -q --run-platform-gated            # opt-in host probes (macOS + admin)
```

Evidence: `docs/verification/phase-1.md` and
`docs/verification/phase-2a.md`. Reviewed pins: `mcp==2.2.0`,
`Telethon==1.45.0` (pinned but unused in these phases).
