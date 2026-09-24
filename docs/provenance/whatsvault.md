# Provenance — WhatsVault under `transports/whatsapp/`

| Field | Value |
|---|---|
| Source URL | `https://github.com/Raoof128/whatsvault.git` |
| Source commit | `b6fd51ac83d91018cb2d7fe37f0bce669c7317aa` (full SHA; the remote refuses fetch-by-abbreviation) |
| Source tree | `abdbcdc775ff62c4eab6d0527e7081128c133ba7` |
| Import merge commit | `3202855` (branch `comms-5b2`) |
| Prefix | `transports/whatsapp` |
| Method | `git merge -s ours --allow-unrelated-histories` then `git read-tree --prefix=transports/whatsapp -u` — the subtree merge `git subtree add` wraps. `git subtree` is not installed on this host (Homebrew git ships without the contrib script). Full history, no squash: 100 WhatsVault commits are ancestors of the import. |
| Tree proof | `git rev-parse HEAD:transports/whatsapp` == source tree; pinned by `tests/integration/test_whatsvault_provenance.py` while the subtree is untouched |
| Baseline at source | 539 passed, 0 skipped (its own venv, Python 3.14.7) |
| Baseline in comms | 539 passed (its own pytest config, root venv, Python 3.12.2) |

## Native substrate (design §3.3 — superseded assumption)

The design expected Homebrew SQLCipher. Measured instead: `sqlcipher3` 0.6.2 installs as a
`cp312-cp312-macosx_11_0_arm64` **wheel with SQLCipher bundled**; `PRAGMA cipher_version`
returns `4.12.0 community`. The wheel is hash-pinned in `uv.lock`, so the native library is
pinned by the lock itself — stronger than recording a Homebrew formula. No Homebrew SQLCipher is
installed or used. Python 3.12.2, arm64.

## What 5b-2 did not do

Nothing inside the prefix was edited. WhatsVault's `apps/` is not installed into the root
environment (its tests import it via their own `pythonpath`); exposing a top-level `apps` and a
second top-level `tests` would collide with Telegram's `tests`. Its console scripts
(`whatsvault`, `whatsvault-mcp`) are not installed from the root project in 5b-2.
