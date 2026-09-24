"""The documentation must describe the code that exists.

Doc drift here has not been cosmetic. The README once listed four CLI verbs that
had never been implemented; USAGE.md printed `acc_01J…` as though it were a value
a user could obtain, when nothing could produce one; and `.env.example` documented
`WHATSVAULT_MCP_HOST` and `WHATSVAULT_MCP_PORT`, which no code path has ever read
— a reader could set them, get no effect, and have no way to tell why.

Each was found by a human reading carefully, which does not scale and did not
catch them promptly. These assertions run in CI instead.

Scope is deliberately the *live* docs. `docs/internal/` is explicitly historical —
plans and findings kept as they were written — and is excluded, because rewriting
a record to match later code would defeat the point of keeping it.
"""

import re
from pathlib import Path

import pytest

from whatsvault.cli import commands

ROOT = Path(__file__).resolve().parents[1]
LIVE_DOCS = [
    "README.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "docs/ARCHITECTURE.md",
    "docs/MCP.md",
    "docs/USAGE.md",
    ".env.example",
]


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def docs():
    return {name: _read(name) for name in LIVE_DOCS}


# ---- commands the docs tell a user to run must exist ---------------------------
def test_every_documented_cli_verb_exists(docs):
    blob = "\n".join(docs.values())
    # `whatsvault http://…` appears inside a `claude mcp add` line, where the URL
    # follows the server name rather than a verb.
    shown = {v for v in re.findall(r"whatsvault ([a-z][a-z-]+)", blob) if v != "http"}
    unknown = sorted(v for v in shown if v not in commands.COMMANDS)
    assert unknown == [], f"documented but not implemented: {unknown}"


def test_no_documented_verb_is_a_forbidden_one(docs):
    blob = "\n".join(docs.values())
    shown = set(re.findall(r"whatsvault ([a-z][a-z-]+)", blob))
    assert shown.isdisjoint(commands.FORBIDDEN_VERBS)


# ---- configuration the docs offer must be configuration the code reads ---------
def test_every_documented_env_var_is_read_somewhere(docs):
    """`.env.example` is a promise that setting something has an effect."""
    documented = set(re.findall(r"^(WHATSVAULT_[A-Z_]+)=", docs[".env.example"], re.M))
    assert documented, ".env.example documents nothing"
    source = "\n".join(
        p.read_text(encoding="utf-8") for d in ("src", "apps") for p in (ROOT / d).rglob("*.py")
    )
    dead = sorted(v for v in documented if v not in source)
    assert dead == [], f".env.example documents variables no code reads: {dead}"


# ---- claims about the OAuth deployment switch ----------------------------------
def test_docs_do_not_promise_a_scope_the_server_will_not_issue(docs):
    from whatsvault.mcp import oauth

    assert oauth.READ_ONLY_SCOPE in docs["docs/MCP.md"]
    for word in ("whatsvault.write", "whatsvault.send", "whatsvault.admin"):
        assert word not in "\n".join(docs.values()), f"docs mention a scope that does not exist: {word}"


# ---- links -------------------------------------------------------------------
def test_relative_links_in_the_live_docs_resolve(docs):
    broken = []
    for name, text in docs.items():
        base = (ROOT / name).parent
        for label, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            path = target.split("#")[0]
            if path and not (base / path).exists():
                broken.append(f"{name}: [{label}]({target})")
    assert broken == [], f"broken relative links: {broken}"
