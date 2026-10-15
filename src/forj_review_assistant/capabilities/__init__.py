"""能力契约、注册表、子图和页面动作的公共入口。"""

from forj_review_assistant.capabilities.page_actions import (
    PageActionHandler,
    PageActionRegistry,
    PageActionSpec,
)
from forj_review_assistant.capabilities.registry import CapabilityRegistry
from forj_review_assistant.capabilities.specs import (
    CapabilityBinding,
    CapabilityHandler,
    CapabilitySpec,
    ExternalCheckSpec,
    ReviewExecutionContext,
)
from forj_review_assistant.contracts.review import CapabilityPlanEntry, CapabilityResult

__all__ = [
    "CapabilityBinding",
    "CapabilityHandler",
    "CapabilityPlanEntry",
    "CapabilityRegistry",
    "CapabilityResult",
    "CapabilitySpec",
    "ExternalCheckSpec",
    "PageActionHandler",
    "PageActionRegistry",
    "PageActionSpec",
    "ReviewExecutionContext",
]
