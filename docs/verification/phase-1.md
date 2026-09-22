# Phase 1 verification — synthetic protocol and contract foundation

**Status:** Complete as a synthetic-only foundation. NOT production. No Telegram
login, real account data, session file, consent signature, disclosure receipt,
exposure ledger, audit anchor, privileged service, tunnel or installed-client
acceptance is claimed.

## Reproducibility

```bash
uv sync --locked
uv run python scripts/extract_contracts.py --check
uv run pytest tests/unit tests/contract tests/integration tests/security -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src/telegram_mcp
uv build
```

Fresh-environment smoke (from outside the repo, locked wheel install):

```bash
uv venv /tmp/phase1-wheeltest/venv -p 3.12
uv pip install --python /tmp/phase1-wheeltest/venv/bin/python \
  dist/telegram_mcp-0.1.10-py3-none-any.whl
/tmp/phase1-wheeltest/venv/bin/python -c \
  "from telegram_mcp.contract import load_contracts; assert len(load_contracts()) == 10"
```

## Actual results (2026-09-22, Australia/Sydney)

- `uv sync --locked`: 67 packages. `importlib.metadata`: `mcp==2.2.0`, `telethon==1.45.0`.
- Runtime: `uv run python` 3.12.2 (conda-forge); `uv` 0.11.29; `hatchling==1.32.4`.
- `extract --check`: 23 files OK. Wheel: 39 files, 23 contract JSON, no session/secret artifacts.
- Full suite: **168 passed** (`tests/unit` + `tests/contract` + `tests/integration` + `tests/security`).
- `ruff check`: clean. `ruff format --check`: clean. `mypy src/telegram_mcp`: clean (12 files).
- Raw wire (installed `mcp==2.2.0`, modern `2026-07-28`): `server/discover`,
  `tools/list` (exact 10 names), status success, domain error, unknown-tool
  error, `private, no-store` caching, no `mcp-session-id`, JSON content type.
- Legacy handshake-era (`2025-11-25`) via the same SDK: catalogue identical
  (10 tools), status call succeeds.
- Protocol errors stay SDK errors (400/413/421 shapes); framed tool failures
  stay bounded `isError=true` results. Duplicate request keys are rejected at
  the protocol level by a bounded strict-JSON preflight (the installed SDK
  demonstrably last-wins duplicates, so the plan's conditional wrapper applies).
- Host/Origin canaries: absent from responses and captured logs (SDK
  transport-security warnings redacted through public logging configuration).

## Hashes

- Spec: `36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a`
- `uv.lock`: `01a2a106e3ad8e63cf0f9d2cba9feec2da6e4227be958777e151051f5d6204f4`
- Wheel (`telegram_mcp-0.1.10`): `5b600b55282ab823de57c2c760c79ff644789fc2a9510df654886e0cdc1be850`

## Final review fix pass (2026-09-22)

Fresh-reviewer findings closed with RED→GREEN tests each: 512 B envelope
reserve (measured 79 B overhead; a 1024 B cap now honestly yields
`RESPONSE_LIMIT` for status), deepcopy contract cache, explicit-null vs
omission via `model_fields_set`, union error-code set, `INVALID_TIME` only
for `date-time`, `OTEL_`-prefix guard, regex Host/Origin redaction, strict
manifest decoding. Suite 158 → 168. Five style-only minors deferred in the
plan ledger.

## Correction ledger (roadmap C1–C7)

- C1 (gate ordering): Phase-1 claims foundation evidence only; final A–R follow all phases.
- C2 (excerpt NULL): deferred to Phase 2 (no SQLite in this slice).
- C3 (E.4/E.5 root refs): fixed by `assemble_output` `$defs` hoisting; non-empty fixtures guard it.
- C4 (membership overlap): deferred to Phase 2 (input-level `uniqueItems` only).
- C5 (OAuth conditional): direct-HTTPS OAuth tests are conditional on a future
  approved profile. V0.1.10 refuses `direct_https` with real data
  (`UNSUPPORTED_RELEASE_PROFILE`); this binary only offers `safe_demo`.
- C6 (version): release labels and package version are `0.1.10`.
- C7 (ingress wording): the tunnel mTLS ingress is only the *remote*
  production route; the separately authenticated coding-client loopback ingress
  is retained for later phases.

## Gate ledger (foundation evidence only — never PASS)

| Gate | State | Evidence |
|---|---|---|
| A (hygiene/lock/advisory) | partial | lockfile, advisory review in `dependencies.md`, lint/type/format clean |
| B (contracts/catalogue) | partial | 10 tools, schemas, wire parity; pagination needs a database (later) |
| I (read-only/transport) | partial | `openWorld=false`, JSON/stateless flags, Host/Origin, unknown-tool-first, no OTel |
| K (multi-client) | partial | modern + legacy wire parity; real clients untried |
| L (review/finish) | partial | this record; full review at release gauntlet |

Consent, production auth, real clients and Telegram behavior are untested by design.

## Unresolved external gates (roadmap register)

macOS native-consent signing identity/hardware provider; host privilege-separation
service identities; OpenAI tunnel mTLS/entitlement/tool-scan; Codex/Claude exact
builds and ≥75s timeouts; Telegram Test-DC isolated credentials. None provisioned.

## Audit rulings applied inline (Approach A)

pydantic floor `>=2.12,<3`; explicit `mcp-types==2.2.0` with `mcp.types` import
style; httpx-vs-httpx2 split; full E-headings with word-boundary tokens;
`ed25519:sha256` over 32 raw public bytes; wire tolerance for 2026 additions
(`serverInfo`/`resultType`/cache hints); `type: object` on assembled output for
legacy wire-Tool compatibility (semantically equivalent to E.10);
leap-second → `INVALID_TIME` (reviewer sign-off pending).
