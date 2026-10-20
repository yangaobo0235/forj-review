"""能力契约、注册表、子图和页面动作的公共入口。"""

from forj_review_service.capabilities.page_actions import (
    PageActionHandler,
    PageActionRegistry,
    PageActionSpec,
)
from forj_review_service.capabilities.registry import CapabilityRegistry
from forj_review_service.capabilities.specs import (
    CapabilityBinding,
    CapabilityHandler,
    CapabilitySpec,
    ReviewExecutionContext,
)
from forj_review_service.contracts.review import CapabilityPlanEntry, CapabilityResult

__all__ = [
    "CapabilityBinding",
    "CapabilityHandler",
    "CapabilityPlanEntry",
    "CapabilityRegistry",
    "CapabilityResult",
    "CapabilitySpec",
    "PageActionHandler",
    "PageActionRegistry",
    "PageActionSpec",
    "ReviewExecutionContext",
]
