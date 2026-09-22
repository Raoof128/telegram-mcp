"""The twelve-step disclosure transaction (design §2, §6.5)."""


from telegram_mcp.disclosure.coordinator import DISCLOSURE_STEPS


def test_the_twelve_steps_are_frozen_in_order():
    assert DISCLOSURE_STEPS == (
        "freeze_arguments",
        "snapshot_authority",
        "estimate_exposure",
        "consent_issue",
        "consent_consume",
        "reserve_budget",
        "retrieve",
        "revalidate_authority",
        "transform_egress",
        "measure_and_prepare_proof",
        "commit_disclosure",
        "refresh_anchor",
    )


def test_retrieval_never_happens_before_consent_is_consumed():
    # The security barrier: steps 1-6 must precede any adapter call.
    assert DISCLOSURE_STEPS.index("retrieve") > DISCLOSURE_STEPS.index("consent_consume")
    assert DISCLOSURE_STEPS.index("retrieve") > DISCLOSURE_STEPS.index("reserve_budget")


def test_the_anchor_is_the_last_step():
    assert DISCLOSURE_STEPS[-1] == "refresh_anchor"
    assert (
        DISCLOSURE_STEPS.index("commit_disclosure") == DISCLOSURE_STEPS.index("refresh_anchor") - 1
    )
