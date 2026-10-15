"""Registry for reversible browser page actions.

Page actions are intentionally separate from review decision capabilities. A
handler may propose an action, but the browser adapter decides whether and
how to execute it after reviewer authorization.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from forj_review_assistant.contracts.review import PageFillAction


class PageActionHandler(Protocol):
    async def __call__(self, actions: list[PageFillAction]) -> dict[str, object]: ...


@dataclass(frozen=True)
class PageActionSpec:
    action_id: str
    reversible: bool = True
    requires_authorization: bool = True
    writable_fields: tuple[str, ...] = ()


class PageActionRegistry:
    def __init__(
        self,
        handlers: Mapping[str, PageActionHandler] | None = None,
        specs: Mapping[str, PageActionSpec] | None = None,
    ) -> None:
        self.handlers = dict(handlers or {})
        self.specs = dict(specs or {})

    def register(self, action_id: str, handler: PageActionHandler) -> None:
        if action_id in self.handlers:
            raise ValueError(f"页面动作已注册：{action_id}")
        self.handlers[action_id] = handler
        self.specs.setdefault(action_id, PageActionSpec(action_id))

    def contains(self, action_id: str) -> bool:
        return action_id in self.handlers

    def validate(self, action_ids: tuple[str, ...] | list[str]) -> None:
        duplicates = len(action_ids) != len(set(action_ids))
        if duplicates:
            raise ValueError("Profile 页面动作标识重复")
        unknown = set(action_ids) - set(self.handlers)
        if unknown:
            raise ValueError(f"未注册页面动作：{', '.join(sorted(unknown))}")

    def describe(self) -> tuple[PageActionSpec, ...]:
        return tuple(self.specs[key] for key in sorted(self.specs))
