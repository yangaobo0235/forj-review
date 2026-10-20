"""审核完成后按上传主体材料设置报废置换页面的两个挂靠字段。"""

from forj_review_service.businesses.rules.affiliation import affiliation_material_value
from forj_review_service.capabilities.specs import (
    ReviewExecutionContext,
    RuleExecutionResult,
)
from forj_review_service.contracts.review import PageActionIntent

AFFILIATION_ACTION_ID = "set_affiliation"
AFFILIATION_FIELDS = ("old_vehicle.affiliation", "new_vehicle.affiliation")


def run_set_affiliation(context: ReviewExecutionContext) -> RuleExecutionResult:
    """提出按材料类型决定值的页面动作；实际写回由浏览器适配器完成并回读校验。"""

    if context.profile.business_type != "scrap_replacement":
        return RuleExecutionResult()
    value = affiliation_material_value(
        context.observations,
        image_document_types=(
            item
            for image in context.request.images
            for item in (image.document_type_hint, image.category_hint)
        ),
        recognized_document_types=(
            document.document_type
            for document in context.batch.recognized_documents
        ),
    )
    if value is None:
        return RuleExecutionResult()
    return RuleExecutionResult(
        page_action_intents=(
            PageActionIntent(
                action_id=AFFILIATION_ACTION_ID,
                payload={"fields": list(AFFILIATION_FIELDS), "value": value},
                requires_authorization=False,
            ),
        )
    )
