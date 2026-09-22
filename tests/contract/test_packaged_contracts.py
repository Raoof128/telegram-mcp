"""Packaged catalogue loads from the wheel layout, not the checkout paths."""

import json
from pathlib import Path


def test_contract_files_are_utf8_strict_json():
    base = Path(__file__).resolve().parents[2] / "src" / "telegram_mcp" / "contracts"
    from telegram_mcp.contract import EXPECTED_TOOLS, strict_json_loads

    manifest = strict_json_loads((base / "manifest.json").read_text(encoding="utf-8"))
    assert sorted(manifest["tools"]) == sorted(EXPECTED_TOOLS)
    assert (
        manifest["source_sha256"]
        == "36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a"
    )
    for name in EXPECTED_TOOLS:
        for suffix in ("input.json", "data.json"):
            text = (base / f"{name}.{suffix}").read_text(encoding="utf-8")
            assert text.endswith("\n")
            strict_json_loads(text)
    raw = (base / "manifest.json").read_text(encoding="utf-8")
    assert json.loads(raw)["source"].endswith(".md")
