"""报废证明二维码的材料选择；页面分区优先于材料分类提示。"""

from forj_review_assistant.businesses.profiles import BusinessProfile
from forj_review_assistant.businesses.routing import scope_hint_types
from forj_review_assistant.contracts.recognition import AgentBatchResult
from forj_review_assistant.contracts.review import (
    ImageInput,
    ReviewRequest,
)


def qr_candidates(
    request: ReviewRequest, batch: AgentBatchResult, profile: BusinessProfile
) -> tuple[list[ImageInput], list[ImageInput]]:
    pack = profile.pack_declaration
    material = pack.material("scrap_certificate") if pack else None
    if material is None:
        return [], []
    scopes = set(material.allowed_scopes)
    hints = set().union(*(scope_hint_types(pack, scope) for scope in scopes))
    recognized = {
        document.target_id
        for document in batch.recognized_documents
        if document.document_type == material.document_type
    }
    indices = {
        document.image_index
        for document in batch.recognized_documents
        if document.document_type == material.document_type
    } | {
        item.image_index
        for item in batch.observations
        if item.document_type == material.document_type
    }
    preferred, fallback = [], []
    for image in request.images:
        # 分类结果不得把新车等明确分区的图片重新解释成旧车证明。
        if image.business_scope not in scopes | {"unknown", ""}:
            continue
        is_certificate = (
            image.category_hint == material.document_type
            or image.document_type_hint == material.document_type
            or str(image.image_id or image.index) in recognized
            or image.index in indices
        )
        if is_certificate:
            preferred.append(image)
        elif (
            image.business_scope in scopes
            or image.category_hint in hints
            or image.document_type_hint in hints
        ):
            fallback.append(image)
    return preferred, fallback
