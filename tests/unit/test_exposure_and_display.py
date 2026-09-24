# tests/unit/test_exposure_and_display.py
"""The five-field consent display (§9.8 step 6); removed with the consent package in Task 9."""

import pytest

from comms.transports.telegram.consent.display import ACTION_DISPLAY, build_display
from comms.transports.telegram.disclosure.budget import GLOBAL, PROJECT, BucketKey, Usage
from comms.transports.telegram.disclosure.measure import RECORD_ELEMENT

G = BucketKey(1, GLOBAL, "a" * 64)
P = BucketKey(1, PROJECT, "b" * 64)


def test_every_sensitive_tool_has_an_action_label():
    assert set(ACTION_DISPLAY) == set(RECORD_ELEMENT)


def test_display_shows_egress_and_current_and_projected_budget():
    display = build_display(
        tool_name="telegram_list_projects",
        client_kind="codex_local",
        project_names=[],
        peer_name=None,
        egress_level="metadata_only",
        tier="elevated",
        current=Usage(1200, 90_000),
        projected=Usage(1210, 91_000),
    )
    assert set(display) == {
        "action_display",
        "client_display",
        "peer_display",
        "project_display",
        "risk_class",
    }
    assert display["client_display"] == "Codex"
    assert "metadata_only" in display["risk_class"]
    assert "1200" in display["risk_class"] and "1210" in display["risk_class"]
    assert "ELEVATED" in display["risk_class"]
    assert len(display["risk_class"]) <= 160


def test_unknown_client_kind_is_refused():
    with pytest.raises(ValueError):
        build_display(
            tool_name="telegram_list_projects",
            client_kind="curl",
            project_names=[],
            peer_name=None,
            egress_level="metadata_only",
            tier="normal",
            current=Usage(0, 0),
            projected=Usage(1, 10),
        )


def test_a_cross_project_prompt_warns_about_context_insertion():
    from comms.transports.telegram.consent.display import build_display
    from comms.transports.telegram.disclosure.budget import Usage

    display = build_display(
        tool_name="telegram_cross_project_search",
        client_kind="codex_local",
        project_names=["Alpha", "Beta"],
        peer_name=None,
        egress_level="full_text",
        tier="elevated",
        current=Usage(1200, 1_400_000),
        projected=Usage(1250, 1_450_000),
    )
    assert display["risk_class"].startswith("results may enter this AI session's context")
    assert "ELEVATED" in display["risk_class"] and len(display["risk_class"]) <= 160
    assert display["project_display"] == ["Alpha", "Beta"]
