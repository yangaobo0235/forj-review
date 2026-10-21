from typing import get_args

from forj_review_service.businesses.page_action_catalog import (
    PAGE_ACTION_SPECS,
    PageActionId,
    build_page_actions,
)
from forj_review_service.contracts.review import PageActionIntent


def test_authorable_actions_match_the_builtin_registry():
    assert set(get_args(PageActionId)) == {spec.action_id for spec in PAGE_ACTION_SPECS}


def test_only_registered_enabled_actions_reach_the_browser():
    registry = build_page_actions()
    intents = [
        PageActionIntent(action_id=action, requires_authorization=False)
        for action in ("verify_invoice", "set_affiliation", "unknown")
    ]
    assert (
        registry.permitted_intents(
            intents,
            enabled_actions=(),
            writable_fields=(),
        )
        == []
    )
    permitted = registry.permitted_intents(
        intents,
        enabled_actions=("verify_invoice", "unknown"),
        writable_fields=(),
    )
    assert [item.action_id for item in permitted] == ["verify_invoice"]
    assert permitted[0].requires_authorization is False


def test_group_action_requires_permission_for_both_targets():
    registry = build_page_actions()
    intent = PageActionIntent(action_id="set_affiliation", requires_authorization=False)
    assert (
        registry.permitted_intents(
            [intent],
            enabled_actions=(intent.action_id,),
            writable_fields=("old_vehicle.affiliation",),
        )
        == []
    )
    assert registry.permitted_intents(
        [intent],
        enabled_actions=(intent.action_id,),
        writable_fields=("old_vehicle.affiliation", "new_vehicle.affiliation"),
    ) == [intent]


def test_a_proposal_cannot_remove_the_registered_authorization_requirement():
    registry = build_page_actions()
    intent = PageActionIntent(action_id="fill_review_fields", requires_authorization=False)
    permitted = registry.permitted_intents(
        [intent],
        enabled_actions=(intent.action_id,),
        writable_fields=(),
    )
    assert permitted[0].requires_authorization is True
    assert intent.requires_authorization is False


def test_an_explicit_authorization_requirement_is_preserved():
    intent = PageActionIntent(action_id="verify_invoice", requires_authorization=True)
    assert build_page_actions().permitted_intents(
        [intent],
        enabled_actions=(intent.action_id,),
        writable_fields=(),
    ) == [intent]
