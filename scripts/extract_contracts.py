"""Deterministic offline extraction of tool contracts from the supplied spec.

Reads the spec as UTF-8, requires the original SHA-256, selects the first
JSON fence within each exact heading's section. `--check` fails on drift
without writing files.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

EXPECTED_SHA256 = "36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a"

WORKSPACE = Path(__file__).resolve().parents[1]
SPEC_PATH = WORKSPACE / "telegram-mcp-v0.1.10-final-engineering-spec.md"
CONTRACTS_DIR = WORKSPACE / "src" / "comms" / "transports" / "telegram" / "contracts"

# Full exact heading prefixes (trailing space prevents E.1 matching E.10-E.13).
INPUT_HEADINGS = {
    "telegram_status": "### 16.2 Input schema",
    "telegram_list_projects": "### 15.1 Tool specification:",
    "telegram_resolve_project": "### 15.2 Tool specification:",
    "telegram_list_chats": "### 17.2 Input schema",
    "telegram_resolve_peer": "### 18.2 Input schema",
    "telegram_get_messages": "### 19.2 Input schema",
    "telegram_get_context": "### 20.2 Input schema",
    "telegram_search_messages": "### 21.2 Input schema",
    "telegram_cross_project_search": "### 21A.2 Input schema",
    "telegram_get_unread": "### 22.2 Input schema",
}

OUTPUT_HEADINGS = {
    "telegram_status": "### E.1 ",
    "telegram_list_chats": "### E.2 ",
    "telegram_resolve_peer": "### E.3 ",
    "telegram_get_messages": "### E.4 ",
    "telegram_get_context": "### E.5 ",
    "telegram_search_messages": "### E.6 ",
    "telegram_get_unread": "### E.7 ",
    "telegram_list_projects": "### E.11 ",
    "telegram_resolve_project": "### E.12 ",
    "telegram_cross_project_search": "### E.13 ",
}

META_HEADING = "### E.8 "
ERROR_HEADING = "### E.9 "

DESCRIPTIONS = {
    "telegram_status": "Report gateway Telegram access and non-sensitive capability information.",
    "telegram_list_projects": "List enabled gateway project namespaces the client may read.",
    "telegram_resolve_project": "Resolve an authorised gateway project by text without auto-choosing.",
    "telegram_list_chats": "List chats in one explicit gateway project.",
    "telegram_resolve_peer": "Resolve a chat peer within one explicit gateway project.",
    "telegram_get_messages": "Retrieve bounded messages from one peer in one project.",
    "telegram_get_context": "Retrieve messages around an anchor message in one peer.",
    "telegram_search_messages": "Search messages within one project or peer.",
    "telegram_cross_project_search": "Search explicitly across two to eight gateway projects.",
    "telegram_get_unread": "List unread conversations in one explicit gateway project.",
}


def _find_heading(lines: list[str], marker: str) -> int:
    matches = [i for i, line in enumerate(lines) if line.startswith(marker)]
    # For markers without trailing space, require exact heading semantics:
    # E-number markers always carry trailing space (see OUTPUT_HEADINGS).
    if len(matches) != 1:
        raise ValueError(f"heading {marker!r}: expected 1 match, found {len(matches)}")
    return matches[0]


def _section_end(lines: list[str], start: int) -> int:
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("### "):
            return i
    return len(lines)


def _first_fence(lines: list[str], start: int, end: int, marker: str) -> str:
    fence = None
    for i in range(start, end):
        if lines[i].strip() == "```json":
            fence = i
            break
    if fence is None:
        raise ValueError(f"heading {marker!r}: no ```json fence in section")
    close = None
    for j in range(fence + 1, end):
        if lines[j].strip() == "```":
            close = j
            break
    # Fallback: fence may run to a closing ``` after section prose; search ahead.
    if close is None:
        for j in range(end, min(len(lines), end + 400)):
            if lines[j].strip() == "```":
                close = j
                break
    if close is None:
        raise ValueError(f"heading {marker!r}: unterminated ```json fence")
    return "\n".join(lines[fence + 1 : close]) + "\n"


def extract_all() -> dict[str, str]:
    text = SPEC_PATH.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if digest != EXPECTED_SHA256:
        raise ValueError(f"spec SHA-256 mismatch: {digest}")
    lines = text.splitlines()
    out: dict[str, str] = {}
    for tool, marker in INPUT_HEADINGS.items():
        head = _find_heading(lines, marker)
        end = _section_end(lines, head)
        out[f"{tool}.input.json"] = _first_fence(lines, head, end, marker)
    for tool, marker in OUTPUT_HEADINGS.items():
        head = _find_heading(lines, marker)
        end = _section_end(lines, head)
        out[f"{tool}.data.json"] = _first_fence(lines, head, end, marker)
    head = _find_heading(lines, META_HEADING)
    out["meta.json"] = _first_fence(lines, head, _section_end(lines, head), META_HEADING)
    head = _find_heading(lines, ERROR_HEADING)
    out["error.json"] = _first_fence(lines, head, _section_end(lines, head), ERROR_HEADING)
    # Manifest with source hash, descriptions and Section-15 annotations.
    annotations = {}
    for tool in INPUT_HEADINGS:
        annotations[tool] = {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
            "requiresUserInteraction": tool != "telegram_status",
        }
    manifest = {
        "source": "telegram-mcp-v0.1.10-final-engineering-spec.md",
        "source_sha256": EXPECTED_SHA256,
        "tools": sorted(INPUT_HEADINGS),
        "descriptions": {t: DESCRIPTIONS[t] for t in sorted(INPUT_HEADINGS)},
        "annotations": annotations,
        "files": sorted(out.keys()),
    }
    out["manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = extract_all()
    if args.check:
        failures = []
        for name, content in expected.items():
            path = CONTRACTS_DIR / name
            if not path.is_file():
                failures.append(f"missing {name}")
            elif path.read_text(encoding="utf-8") != content:
                failures.append(f"drift {name}")
        if failures:
            print("contract drift:\n" + "\n".join(failures), file=sys.stderr)
            return 1
        print(f"contracts check OK ({len(expected)} files)")
        return 0
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in expected.items():
        path = CONTRACTS_DIR / name
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    print(f"extracted {len(expected)} contract files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
