"""comms v0.3 C23 (found defect): campaign content renders to one message text for every transport."""

import pytest

from comms.core.campaigns.render import rendered_text

MEDIA = {"sha256": "a" * 64, "mime": "image/png", "name": "x.png", "size": 1}


@pytest.mark.parametrize(
    "content,text",
    [
        ({"canonical": "Happy Nowruz"}, "Happy Nowruz"),
        ({"canonical": "Happy Nowruz", "fa": "نوروز", "en": "Nowruz"}, "Happy Nowruz"),
        (
            {"canonical": "Join us", "links": ["https://a.example", "https://b.example"]},
            "Join us\n\nhttps://a.example\nhttps://b.example",
        ),
        ({"canonical": "Join us", "links": []}, "Join us"),
        ({"canonical": "Join us", "media": []}, "Join us"),
    ],
)
def test_the_canonical_body_and_its_links(content, text):
    assert rendered_text(content) == text


@pytest.mark.parametrize(
    "content",
    [
        {},
        {"canonical": ""},
        {"canonical": "   "},
        {"fa": "only"},
        {"canonical": "x", "media": [MEDIA]},
        {"canonical": 5},
    ],
)
def test_nothing_to_send_or_media_is_none(content):
    assert rendered_text(content) is None
