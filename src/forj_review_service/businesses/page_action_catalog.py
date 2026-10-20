"""Registered browser action proposals and their backend declarations."""

from collections.abc import Mapping
from typing import Literal

from forj_review_service.capabilities.page_actions import (
    PageActionHandler,
    PageActionRegistry,
    PageActionSpec,
)

PageActionId = Literal["fill_review_fields", "verify_invoice", "set_affiliation"]

PAGE_ACTION_SPECS = (
    PageActionSpec("verify_invoice", reversible=False, requires_authorization=False),
    PageActionSpec(
        "set_affiliation", requires_authorization=False,
        writable_fields=("old_vehicle.affiliation", "new_vehicle.affiliation"),
    ),
    PageActionSpec("fill_review_fields"),
)


async def propose_page_action(actions: list[object]) -> dict[str, object]:
    return {"status": "PROPOSED", "count": len(actions)}


def build_page_actions(
    additional: Mapping[str, PageActionHandler] | None = None,
) -> PageActionRegistry:
    specs = {spec.action_id: spec for spec in PAGE_ACTION_SPECS}
    handlers = dict.fromkeys(specs, propose_page_action)
    for action_id, handler in (additional or {}).items():
        if action_id in handlers:
            raise ValueError(f"不能覆盖内置页面动作：{action_id}")
        handlers[action_id] = handler
        specs[action_id] = PageActionSpec(action_id)
    return PageActionRegistry(handlers=handlers, specs=specs)
