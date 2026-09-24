"""comms v0.3 Task C3: OperationSemantics — one machine-readable idempotency table (A27, A41, O7)."""

from comms.core.providers.capability import Capability as C
from comms.core.providers.semantics import READS, SEMANTICS, SUPPORT, is_write

ACTORS = ("telegram_bot", "telegram_user", "whatsapp_cloud", "whatsapp_webhooks")


def test_every_write_capability_has_semantics_for_every_supporting_actor():
    assert set(SUPPORT) == set(C)  # every capability is offered by at least one actor
    assert all(SUPPORT[c] for c in C)
    for capability, actors in SUPPORT.items():
        for actor in actors:
            semantics = SEMANTICS[(capability, actor)]
            assert (semantics.retry_class == "READ") == (not is_write(capability))
    assert set(SEMANTICS) == {(c, a) for c, actors in SUPPORT.items() for a in actors}
    assert {actor for _c, actor in SEMANTICS} == set(ACTORS)
    assert READS < set(C) and C.MESSAGE_MARK_READ not in READS  # marking read changes state


def test_message_send_idempotent_only_over_mtproto():
    sends = {k: v for k, v in SEMANTICS.items() if v.retry_class == "MESSAGE_SEND"}
    assert sends
    for (_capability, actor), semantics in sends.items():
        if actor == "telegram_user":
            assert (semantics.idempotency_strategy, semantics.ambiguity_policy) == (
                "provider_random_id",
                "retry_same_key",
            )
        else:
            assert (semantics.idempotency_strategy, semantics.ambiguity_policy) == (
                "none",
                "resolve_only",
            )


def test_create_is_resolve_only():
    creates = [v for v in SEMANTICS.values() if v.retry_class == "CREATE"]
    assert creates and all(
        v.ambiguity_policy == "resolve_only" and v.idempotency_strategy == "none" for v in creates
    )


def test_set_exact_admin_rights_is_set_state():
    for actor in ("telegram_bot", "telegram_user"):
        semantics = SEMANTICS[(C.ADMIN_PROMOTE, actor)]
        assert (semantics.retry_class, semantics.idempotency_strategy) == ("SET_STATE", "natural")


def test_bot_member_remove_is_a_two_step_saga_of_set_state_steps():
    saga = SEMANTICS[(C.MEMBER_REMOVE, "telegram_bot")]
    assert saga.steps == (C.MEMBER_BAN, C.MEMBER_UNBAN)
    assert all(SEMANTICS[(step, "telegram_bot")].retry_class == "SET_STATE" for step in saga.steps)
    assert saga.step_args[1] == {"only_if_banned": True}


def test_group_delete_is_destructive_nonidempotent_resolve_only():
    for key, semantics in SEMANTICS.items():
        if key[0] in (C.GROUP_DELETE, C.GROUP_MIGRATE):
            assert (semantics.retry_class, semantics.ambiguity_policy) == (
                "DESTRUCTIVE_NONIDEMPOTENT",
                "resolve_only",
            )


def test_every_saga_step_has_its_own_semantics_entry():
    for (capability, actor), semantics in SEMANTICS.items():
        for step in semantics.steps:
            assert (step, actor) in SEMANTICS, (capability, actor, step)
            assert not SEMANTICS[(step, actor)].steps  # sagas are one level deep
        assert len(semantics.step_args) in (0, len(semantics.steps))
