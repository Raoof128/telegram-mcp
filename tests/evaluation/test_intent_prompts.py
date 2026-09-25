"""comms v0.3 Task D38: the P §80 intent prompts, pinned as data for owner-run acceptance.

The gate proves only that the data is well formed: every P §80 prompt appears once, verbatim,
and every expected tool and argument exists in the catalog. Whether a client picks these tools
is evidence the owner records (docs/runbooks/clients-*.md).
"""

import json
import re
from pathlib import Path

from comms.mcp.catalog import TOOL_CATALOG

ROOT = Path(__file__).resolve().parents[2]
DATA = json.loads(
    (ROOT / "tests" / "evaluation" / "intent_prompts.json").read_text(encoding="utf-8")
)
SPECS = {spec.name: spec for spec in TOOL_CATALOG}


def _proposal_prompts():
    text = (ROOT / "docs" / "provenance" / "comms-v0.3-proposal.md").read_text(encoding="utf-8")
    section = text.split("# 80. Tests: LLM Intent", 1)[1].split("\n# 81.", 1)[0]
    block = re.search(r"```text\n(.*?)```", section, re.DOTALL).group(1)
    return [line.strip().strip('"') for line in block.splitlines() if line.strip()]


def test_every_p80_prompt_is_pinned_once_verbatim():
    assert [p["prompt"] for p in DATA["prompts"]] == _proposal_prompts()


def test_every_expected_tool_and_argument_exists():
    for entry in DATA["prompts"]:
        assert entry["expect"] in DATA["expect"], entry["prompt"]
        assert entry["tools"] and all(tool in SPECS for tool in entry["tools"]), entry["prompt"]
        for tool, arguments in entry.get("arguments", {}).items():
            assert tool in entry["tools"], entry["prompt"]
            properties = SPECS[tool].input_schema["properties"]
            for name, value in arguments.items():
                assert name in properties, (entry["prompt"], name)
                allowed = properties[name].get("enum")
                assert allowed is None or value in allowed, (entry["prompt"], name, value)


def test_a_clarify_prompt_ends_in_a_write_that_needs_refs():
    for entry in DATA["prompts"]:
        if entry["expect"] == "clarify":
            final = SPECS[entry["tools"][-1]]
            assert final.requires_request_id, entry["prompt"]
            targets = [k for k in ("group", "recipient") if k in final.input_schema["properties"]]
            assert targets and all(
                "pattern" in final.input_schema["properties"][k] for k in targets
            ), entry["prompt"]
