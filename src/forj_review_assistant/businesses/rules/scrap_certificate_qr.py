"""报废证明二维码的材料选择；页面分区优先于材料分类提示。"""

from forj_review_assistant.businesses.profiles import BusinessProfile
from forj_review_assistant.businesses.routing import scope_hint_types
from forj_review_assistant.contracts.recognition import AgentBatchResult
from forj_review_assistant.contracts.review import (
    FieldObservation,
    FieldStatus,
    ImageInput,
    QrCheck,
    ReviewRequest,
)
from forj_review_assistant.evidence.fields.normalize import normalize_value


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


def append_qr_observations(
    observations: list[FieldObservation],
    qr_checks: list[QrCheck],
) -> None:
    for check in qr_checks:
        for field, value in check.page_fields.items():
            mapped_field = (
                "old_vehicle.vin" if field == "vin" else "scrap_certificate.certificate_no"
            )
            observations.append(
                FieldObservation(
                    field=mapped_field,
                    source_type="qr_page",
                    source_id=f"qr-{check.image_index}",
                    image_index=check.image_index,
                    value=value,
                )
            )


def _comparable_qr_value(field_name: str, value: object) -> str:
    normalized = normalize_value(field_name, value) or ""
    # Official certificate numbers may omit the printed separators.
    if field_name == "scrap_certificate.certificate_no":
        return "".join(ch for ch in normalized.upper() if ch.isalnum())
    return normalized


def mark_qr_conflicts(
    observations: list[FieldObservation],
    qr_checks: list[QrCheck],
) -> bool:
    has_conflict = False
    for check in qr_checks:
        for page_field, page_value in check.page_fields.items():
            candidates = [
                item.value
                for item in observations
                if item.image_index == check.image_index
                and item.source_type == "image"
                and (
                    (page_field == "vin" and item.field.endswith(".vin"))
                    or (page_field == "certificate_no" and item.field.endswith("certificate_no"))
                )
                and item.value not in (None, "")
            ]
            field_name = (
                "old_vehicle.vin" if page_field == "vin" else "scrap_certificate.certificate_no"
            )
            if any(
                _comparable_qr_value(field_name, value)
                != _comparable_qr_value(field_name, page_value)
                for value in candidates
            ):
                check.status = FieldStatus.CONFLICT
                check.message = "二维码网页字段与图片识别结果冲突"
                has_conflict = True
    return has_conflict
