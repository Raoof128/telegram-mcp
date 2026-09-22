# Telegram MCP Gateway (development foundation)

Read-only Telegram MCP gateway — **Phase-1 synthetic foundation**.
This build serves a disconnected synthetic `telegram_status`, advertises the
exact ten-tool contracts, and refuses all nine sensitive calls. It performs no
Telegram login and holds no session. Production deployment requires Gates A–R
(see the engineering specification).

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

## Verify

```bash
uv run python scripts/extract_contracts.py --check
uv run pytest tests/unit tests/contract tests/integration tests/security -q
```

Evidence: `docs/verification/phase-1.md`. Reviewed pins: `mcp==2.2.0`,
`Telethon==1.45.0` (pinned but unused in this phase).
