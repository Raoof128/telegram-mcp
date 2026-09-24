# Dependencies — Phase 1 review (2026-09-22)

Reviewed baselines (exact pins, per spec §7.1–7.2):
- `mcp==2.2.0` — PyPI live, released 2026-09-07, latest in 2.x line. Sources: https://pypi.org/project/mcp/2.2.0/, SDK source at v2.2.0 tag (lowlevel/server.py, streamable_http_manager.py). Disposition: PIN EXACT. Advisory review: GHSA-jpw9-pfvf-9f58/CVE-2026-52869 fixed ≤1.27.2 (inherits fix); GHSA-vj7q-gjh5-988w N/A (WebSocket removed v2); GHSA-9h52-p55h-vw2f fixed 1.23.0 (TransportSecuritySettings present); GHSA-hvrp-rf83-w775 N/A (no tasks). Open issues #3356/#3257/#3102/#2707 are legacy SSE/GET only — plan avoids via json_response=True, no listen/subscriptions.
- `Telethon==1.45.0` — PyPI live, released 2026-09-10, latest (prior 1.44.0 2026-06-15). Requires Python >=3.5. Disposition: PIN EXACT, UNUSED in Phase 1 (no import). Note: v1 maintenance mode, layers still updated.
- `pydantic>=2.12,<3` (audit-corrected floor; SDK mcp 2.2.0 requires pydantic>=2.12.0 — plan's >=2,<3 was too loose). Exact resolved version pinned by `uv lock`.
- `mcp-types==2.2.0` — explicit direct dep (audit ruling) so `mcp_types`/`mcp.types` import does not rely on transitive accidental import. SDK pyproject depends on mcp-types exact; app code will prefer `mcp.types` alias — verification at Task 5/6.
- `jsonschema[format]` — JSON Schema 2020-12 validation with FormatChecker. Exact pin via lock.
- `cryptography` — Ed25519 synthetic fixture key (test-only in Phase 1). Exact pin via lock.
- `uvicorn` + `starlette` (exact direct dep — SDK transport integration, not transitive). Exact pins via lock.
- Dev: `pytest`, `pytest-asyncio`, `httpx` (Starlette TestClient only — NOT MCP client paths; SDK runtime uses `httpx2>=2.5.0`), `ruff`, `mypy`, `build`, `hatchling` (build backend, exact version recorded in [build-system].requires after lock).

Runtime: Python 3.12+ (uv run python 3.12.2 verified; system 3.14.7; .python-version=3.12).
MCP revision: 2026-07-28 normative. HTTP: json_response=True + stateless_http=True.

2.2.0 knobs reviewed-but-unused in stateless demo: session_idle_timeout default 1800, max_sessions 10k/503, same-origin redirect MCPError, OAuth issuer validation. No code change.

Unresolved external gates (roadmap register): macOS native consent signing identity/HW provider, host privilege separation service identities, OpenAI tunnel mTLS/entitlement/tool-scan, Codex/Claude exact builds + ≥75s timeouts, Telegram Test-DC isolated credentials. None provisioned in Phase 1.

## Actual Phase-1 Task-1 run evidence
- `uv` 0.11.29 (aarch64-apple-darwin), `uv run python` 3.12.2 (conda-forge, Clang 16.0.6), system python3 3.14.7.
- `importlib.metadata`: mcp 2.2.0, telethon 1.45.0 confirmed after `uv sync --locked` (67 packages).
- `hatchling==1.32.4` pinned in `[build-system].requires`.
- `uv.lock` SHA-256: `01a2a106e3ad8e63cf0f9d2cba9feec2da6e4227be958777e151051f5d6204f4`.
- Audit deltas applied: pydantic floor `>=2.12,<3`, explicit `mcp-types==2.2.0`, `starlette==1.6.0` direct, httpx-vs-httpx2 split documented.

## 2026-09-24 — comms 5b-2: WhatsVault runtime dependencies (spec §7 review)

Added to the root project, pinned to the exact versions WhatsVault's 539-test baseline ran with:
`apscheduler==3.11.3`, `keyring==25.7.0`, `python-ulid==4.0.1`, `sqlcipher3==0.6.2`.
Existing pins already satisfy WhatsVault's remaining ranges (`cryptography` 50.0.1 ⊨ `>=43`,
`mcp` 2.2.0 ⊨ `>=2.1,<3`, `uvicorn` 0.53.0 ⊨ `>=0.30`).

`uv lock` **added** exactly these entries and changed none (`git diff uv.lock` has 0 removed lines):
apscheduler 3.11.3, jaraco-classes 3.4.0, jaraco-context 6.1.2, jaraco-functools 4.6.0,
jeepney 0.9.0, keyring 25.7.0, more-itertools 11.1.0, python-ulid 4.0.1, pywin32-ctypes 0.2.3,
secretstorage 3.5.0, sqlcipher3 0.6.2, tzlocal 5.4.4.

Advisory review: `uvx pip-audit -r <the 12 pins> --no-deps` → **No known vulnerabilities found**
(2026-09-24). `sqlcipher3` 0.6.2 ships a `cp312` macOS arm64 wheel bundling SQLCipher 4.12.0
community; see `docs/provenance/whatsvault.md`.
