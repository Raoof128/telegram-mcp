"""The v0.2 overlay on the frozen meta contract: released receipts are v2 (5b-3 design §2.1)."""

import json
from pathlib import Path

import jsonschema
import pytest

from comms.transports.telegram.disclosure import receipts

META = Path(__file__).parents[2] / "src/comms/transports/telegram/contracts/meta.json"
COMMON = {
    "disclosure_ref": "tdr_" + "a" * 26,
    "principal_ref": "prn_" + "a" * 26,
    "client_ref": "tcl_" + "a" * 26,
    "account_ref": "tga_" + "a" * 26,
    "tool_name": "telegram_get_messages",
    "security_epoch": 1,
    "policy_epoch": 1,
    "project_scope_digest": "hmac-sha256:" + "0" * 64,
    "project_count": 1,
    "effective_egress_level": "full_text",
    "records_disclosed": 1,
    "bytes_disclosed": 10,
    "partial": False,
    "committed_at": "2026-09-24T00:00:00Z",
    "canonical_result_provenance_digest": "3" * 64,
    "canonical_coverage_digest": None,
}


def _payload_schema():
    meta = json.loads(META.read_text(encoding="utf-8"))
    return meta["properties"]["disclosure"]["oneOf"][1]["properties"]["proof_payload"]


def test_a_v2_payload_is_the_released_shape():
    payload = receipts.build_proof_payload_v2(**COMMON, soft_threshold_exceeded=True)
    jsonschema.validate(payload, _payload_schema())
    assert set(_payload_schema()["required"]) == set(receipts.APPENDIX_K_FIELDS_V2)


@pytest.mark.parametrize(
    "payload",
    [
        receipts.build_proof_payload(
            **COMMON, consent_key_id="p256:sha256:" + "1" * 64, consent_challenge_digest="2" * 64
        ),
        {
            **receipts.build_proof_payload_v2(**COMMON, soft_threshold_exceeded=False),
            "consent_verified": True,
        },
        {
            **receipts.build_proof_payload_v2(**COMMON, soft_threshold_exceeded=False),
            "authorization_mode": "consent",
        },
    ],
    ids=["v1", "v2-claiming-consent", "v2-not-owner-direct"],
)
def test_the_gateway_never_releases_another_shape(payload):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, _payload_schema())
