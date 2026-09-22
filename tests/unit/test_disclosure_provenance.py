"""Provenance vector and digest (design §3.2; frozen spec §23A.2A)."""

from telegram_mcp.disclosure.provenance import provenance_digest, provenance_vector

_A, _B = "tpr_" + "a" * 26, "tpr_" + "b" * 26


def _data(texts):
    return {
        "results": [
            {
                "message_ref": f"tgm_{chr(97 + i) * 26}",
                "origin_project_refs": [_B, _A],  # deliberately unsorted
                "text": text,
                "text_truncated": text is not None,
            }
            for i, text in enumerate(texts)
        ]
    }


def test_vector_carries_exactly_the_four_frozen_fields():
    vector = provenance_vector("telegram_search_messages", _data(["hello"]))
    # sorted(), not list(): the dict is insertion-ordered and JCS sorts the
    # keys when the digest is taken, so the entry's construction order is
    # not what the contract fixes -- its field set is.
    assert sorted(vector[0]) == [
        "egress_level",
        "origin_project_refs",
        "record_ref",
        "text_truncated",
    ]


def test_origin_project_refs_are_sorted_ascending():
    vector = provenance_vector("telegram_search_messages", _data(["hello"]))
    assert vector[0]["origin_project_refs"] == sorted([_A, _B])


def test_digest_never_hashes_message_text():
    one = provenance_digest("telegram_search_messages", _data(["alpha"]))
    two = provenance_digest("telegram_search_messages", _data(["omega"]))
    # Same refs, same truncation state, different bodies: same digest. The
    # digest commits to identity and provenance, never to content.
    assert one == two


def test_digest_is_sensitive_to_emitted_order():
    forward = _data(["a", "b"])
    reversed_ = {"results": list(reversed(forward["results"]))}
    assert provenance_digest("telegram_search_messages", forward) != provenance_digest(
        "telegram_search_messages", reversed_
    )


def test_digest_is_lowercase_hex_sha256():
    digest = provenance_digest("telegram_search_messages", _data(["x"]))
    assert len(digest) == 64
    assert digest == digest.lower()
    int(digest, 16)


def test_metadata_only_response_proves_no_body_was_emitted():
    data = {
        "results": [
            {
                "message_ref": "tgm_" + "a" * 26,
                "origin_project_refs": [_A],
                "text": None,
                "text_truncated": False,
            }
        ]
    }
    vector = provenance_vector("telegram_search_messages", data)
    assert vector[0]["egress_level"] == "metadata_only"
