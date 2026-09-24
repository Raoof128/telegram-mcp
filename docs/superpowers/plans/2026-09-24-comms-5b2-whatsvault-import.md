# Comms 5b-2 — Import WhatsVault Intact Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring WhatsVault into this repository at exactly `b6fd51ac83d91018cb2d7fe37f0bce669c7317aa` with its full history, untouched under `transports/whatsapp/`. Make it importable as `whatsvault` in the one Python 3.12 environment, and prove both suites are green: Telegram 1515 passed / 10 skipped, and WhatsVault 539 passed.

**Architecture:** `git subtree` is not installed here (Homebrew git lacks the contrib script), so the import uses the classic subtree merge it wraps:

1. fetch the full SHA from GitHub;
2. `git merge -s ours --no-commit --allow-unrelated-histories`;
3. `git read-tree --prefix=transports/whatsapp -u`;
4. commit.

The result is the same: full history, and a prefix tree hash equal to the source tree. The root `pyproject.toml` adds WhatsVault's runtime dependencies, pinned to the exact versions its 539-test baseline ran with, and packages `transports/whatsapp/src/whatsvault`. WhatsVault's tests run under **their own** pytest configuration (`--strict-config`, `filterwarnings = error`), from their own directory, with the shared venv.

**Tech Stack:** git, uv, pytest, hatchling. New runtime dependencies (the §7 review is in Task 3): `apscheduler==3.11.3`, `keyring==25.7.0`, `python-ulid==4.0.1`, `sqlcipher3==0.6.2`.

**Spec:** [`comms-consolidation-design.md`](../specs/2026-09-24-comms-consolidation-design.md) rev 2, §1 and §3.

**Branch:** `comms-5b2`, cut from `main` at the commit carrying this plan.

## Global Constraints

- **Nothing inside `transports/whatsapp/` is edited in 5b-2.** The tree must equal `abdbcdc775ff62c4eab6d0527e7081128c133ba7` at every commit on this branch.
- **No semantic change on either side** (design §1). Presence behaviour is frozen; WhatsVault's `INV-APPROVAL` stands.
- **No existing `uv.lock` entry changes.** The lock may only **add** packages (the spike measured 183 insertions and 0 deletions).
- **Fail closed:** a check that cannot run reports a skip with a reason.
- **Commit only on a green gate**, run fail-fast. The RED-test commit in Task 1 is the one recorded exception.
- The full gate is the Telegram gate (`uv sync --locked`, contracts, `pytest`, smoke, formal, ruff, format, `mypy src/comms src/telegram_mcp`, build) **plus** the WhatsVault gate:
  `(cd transports/whatsapp && ../../.venv/bin/python -m pytest -p no:randomly -p no:cacheprovider)` → `539 passed`.

## Review Focus

1. **Someone edits a file inside the subtree during 5b-2.** Tree-hash equality must fail. *Test: Task 1, `test_the_subtree_is_the_measured_tree`, which checks the committed tree **and** a clean working tree under the prefix.*
2. **`whatsvault` resolves to a stale copy elsewhere**, such as `~/Desktop/Raouf/WhatsVault/.venv`. *Test: Task 1, `test_whatsvault_imports_from_the_subtree`.*
3. **The native SQLCipher substrate drifts** while `uv.lock` stays identical. *Test: Task 1, `test_the_native_substrate_is_recorded`, which pins `sqlcipher3` 0.6.2, SQLCipher `4.12.0`, and the `cp312` macOS arm64 wheel tag.*
4. **The Telegram suite picks up WhatsVault's `tests` package**, since both are named `tests`. The two suites run as separate pytest invocations with separate rootdirs, and neither `testpaths` includes the other.
5. **WhatsVault's `--strict-config` / `filterwarnings = error` trips on a root-environment plugin**, such as `pytest-asyncio` or `pytest-randomly`. The WhatsVault gate disables `randomly` and the cache provider. The spike measured 539 passed and 0 warnings-as-errors.

---

### Task 1: Provenance and substrate tests (RED before the import)

**Files:**
- Create: `tests/integration/test_whatsvault_provenance.py`

- [ ] **Step 1: Write the tests**

```python
# tests/integration/test_whatsvault_provenance.py
"""comms design rev 2 §3.1 and §3.3: the imported tree is the measured tree.

The tree-hash tests hold only while the subtree is untouched. They are
retired, with a ledger note, at 5b-2's first deliberate seam change (§3.4).
"""

import importlib.metadata
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "transports/whatsapp"
SOURCE_URL = "https://github.com/Raoof128/whatsvault.git"
SOURCE_COMMIT = "b6fd51ac83d91018cb2d7fe37f0bce669c7317aa"
SOURCE_TREE = "abdbcdc775ff62c4eab6d0527e7081128c133ba7"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)


def test_the_subtree_is_the_measured_tree():
    committed = _git("rev-parse", f"HEAD:{PREFIX}")
    assert committed.returncode == 0, committed.stderr
    assert committed.stdout.strip() == SOURCE_TREE
    dirty = _git("status", "--porcelain", "--", PREFIX)
    assert dirty.stdout == "", "the subtree must be untouched in 5b-2"


def test_the_source_commit_and_its_history_are_present():
    assert _git("merge-base", "--is-ancestor", SOURCE_COMMIT, "HEAD").returncode == 0
    count = _git("rev-list", "--count", SOURCE_COMMIT)
    assert int(count.stdout.strip()) > 1  # full history, not a squash


def test_whatsvault_imports_from_the_subtree():
    import whatsvault

    location = Path(whatsvault.__file__).resolve()
    assert location.is_relative_to(ROOT / PREFIX / "src" / "whatsvault"), location


def test_the_native_substrate_is_recorded():
    import sqlcipher3

    assert importlib.metadata.version("sqlcipher3") == "0.6.2"
    cipher = sqlcipher3.connect(":memory:").execute("PRAGMA cipher_version").fetchone()[0]
    assert cipher.startswith("4.12.0"), cipher
    if sys.platform == "darwin" and platform.machine() == "arm64":
        dist = importlib.metadata.distribution("sqlcipher3")
        wheel = (Path(dist._path) / "WHEEL").read_text()  # type: ignore[attr-defined]
        assert "cp312-cp312-macosx_11_0_arm64" in wheel
```

- [ ] **Step 2: Run to verify they fail for the right reason**

Run: `uv run pytest tests/integration/test_whatsvault_provenance.py -q -p no:randomly`
Expected: 4 failed. The tree test fails on `HEAD:transports/whatsapp` (no such path), the history test on an unknown commit, and the other two on `ModuleNotFoundError: whatsvault` / `sqlcipher3`.

- [ ] **Step 3: Commit the RED tests** (the recorded exemption; ruff and format are green)

```bash
uv run ruff check tests && uv run ruff format --check tests
git add tests/integration/test_whatsvault_provenance.py
git commit -m "test: 5b-2 provenance and native-substrate tests (RED until the import)"
```

---

### Task 2: The history-preserving import

- [ ] **Step 1: Import**

```bash
git fetch https://github.com/Raoof128/whatsvault.git b6fd51ac83d91018cb2d7fe37f0bce669c7317aa
test "$(git rev-parse FETCH_HEAD)" = b6fd51ac83d91018cb2d7fe37f0bce669c7317aa
git merge -s ours --no-commit --allow-unrelated-histories FETCH_HEAD
git read-tree --prefix=transports/whatsapp -u FETCH_HEAD
git commit -m "merge: import WhatsVault b6fd51a intact under transports/whatsapp (full history)

Source: https://github.com/Raoof128/whatsvault.git
Commit: b6fd51ac83d91018cb2d7fe37f0bce669c7317aa
Tree:   abdbcdc775ff62c4eab6d0527e7081128c133ba7 (= HEAD:transports/whatsapp)
Method: merge -s ours + read-tree --prefix (git subtree is not installed; same result)"
```

- [ ] **Step 2: Prove it**

```bash
test "$(git rev-parse HEAD:transports/whatsapp)" = abdbcdc775ff62c4eab6d0527e7081128c133ba7
git rev-list --count HEAD   # previous count + 100 WhatsVault commits
```

Expected: the tree test passes. The two import tests still fail until Task 3, because the package is not installed yet.

---

### Task 3: One environment, reviewed dependencies, provenance record

**Files:**
- Modify: `pyproject.toml`, `uv.lock`
- Create: `docs/provenance/whatsvault.md`
- Modify: `docs/verification/dependencies.md` (append the §7 review)

- [ ] **Step 1: Add the pinned runtime dependencies and the package**

In `pyproject.toml`, insert into `dependencies`, keeping alphabetical order:

```toml
    "apscheduler==3.11.3",
    "keyring==25.7.0",
    "python-ulid==4.0.1",
    "sqlcipher3==0.6.2",
```

Then set `[tool.hatch.build.targets.wheel] packages = ["src/comms", "src/telegram_mcp", "transports/whatsapp/src/whatsvault"]`.

These are the exact versions from WhatsVault's measured environment. The ranges already satisfied by existing pins are not re-declared: `cryptography` (50.0.1 meets `>=43`), `mcp` (2.2.0 meets `>=2.1,<3`) and `uvicorn` (0.53.0 meets `>=0.30`). WhatsVault's `apps/` package is **not** installed into the root environment in 5b-2. Its tests import it through their own `pythonpath = ["src", "."]`, and exposing a top-level `apps` (and WhatsVault's `tests`) on the root path would collide with Telegram's `tests` package.

- [ ] **Step 2: Lock, and prove the lock only added entries**

```bash
uv lock
test "$(git diff uv.lock | grep -c '^-[^-]')" = 0   # no existing line removed or changed
uv sync --locked
```

- [ ] **Step 3: Record provenance and the dependency review**

`docs/provenance/whatsvault.md` records:
- the source URL and commit (full SHA);
- the source tree hash and the import merge commit;
- the prefix, and the method (and why not `git subtree`);
- the measured baseline (539 passed at the source, and 539 passed in the combined environment);
- the native substrate: Python 3.12.2 arm64; `sqlcipher3` 0.6.2, `cp312-cp312-macosx_11_0_arm64` wheel with SQLCipher 4.12.0 community bundled, hash-pinned in `uv.lock`, so **no Homebrew SQLCipher is involved**. This supersedes the design §3.3 assumption, and is recorded as a deviation.

The `dependencies.md` append lists every package the lock added (from `git diff uv.lock`), with its version and source, and notes they match WhatsVault's measured environment. It also records that the advisory review was run with `uvx pip-audit`, or, if that is unavailable, says so explicitly as **not run**.

- [ ] **Step 4: Run the full gate**

Fail-fast, one command at a time: the Telegram gate, then the WhatsVault gate.

Expected:
- Telegram `1515 passed, 10 skipped`: the WhatsApp→Telegram isolation guard goes from skip to pass, and the 4 provenance tests pass;
- smoke 60/60; formal 624/18; ruff, format and mypy clean; the build wheel contains `whatsvault/`;
- WhatsVault `539 passed`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock docs/provenance/whatsvault.md docs/verification/dependencies.md
git commit -m "build: one Python 3.12 environment for comms: WhatsVault importable, lock additions only"
```

---

### Task 4: Evidence and audit trail

- [ ] **Step 1:** `CLAUDE.md`:
  - the Verify block gains the WhatsVault gate command with `# 539 passed`;
  - the pytest count becomes `1519 passed, 10 skipped`;
  - the status paragraph notes that 5b-2 is imported and names the next step (seam consolidation per design §3.4, or 5b-3).
- [ ] **Step 2:** Write `docs/verification/comms-5b2.md` covering the import proof, both gates' output lines, the lock-additions proof, the native substrate, and the rulings.
- [ ] **Step 3:** Append `**Raouf:**` entries to `AGENT.md` and `CHANGELOG.md`. Re-run the full gate, then commit.

## Spike evidence (this plan was executed in a throwaway worktree before being written)

| Step | Measured |
|---|---|
| Fetch the exact commit by full SHA | OK. An abbreviated SHA is refused by the remote ("couldn't find remote ref"), so the plan uses the full SHA. |
| `merge -s ours` + `read-tree --prefix` | prefix tree `abdbcdc7…` == source tree `abdbcdc7…`; history 160 → 260 commits |
| `uv lock` after adding the 4 pins | `Added` only (sqlcipher3, secretstorage, tzlocal, …); `uv.lock` +183 / −0 |
| `import whatsvault` | resolves to `transports/whatsapp/src/whatsvault` |
| SQLCipher on 3.12 | `cp312-cp312-macosx_11_0_arm64` wheel, `PRAGMA cipher_version` = `4.12.0 community` |
| WhatsVault suite, its own config, shared venv | `539 passed` |
| Telegram gate | `1515 passed, 10 skipped`; smoke 60/60; formal 624/18; ruff, format and mypy clean; build OK; wheel has 80 `whatsvault/` entries |
