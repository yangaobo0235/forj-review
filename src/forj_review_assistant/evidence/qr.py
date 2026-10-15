"""Project QR website evidence using business-owned field bindings."""

from forj_review_assistant.businesses.profiles import BusinessProfile
from forj_review_assistant.contracts.review import FieldObservation, FieldStatus, QrCheck
from forj_review_assistant.evidence.fields.normalize import normalize_value


def append_qr_observations(
    observations: list[FieldObservation], qr_checks: list[QrCheck], profile: BusinessProfile,
) -> None:
    pack = profile.pack_declaration
    bindings = {b.page_field: b for b in pack.qr_field_bindings} if pack else {}
    for check in qr_checks:
        for page_field, value in check.page_fields.items():
            binding = bindings.get(page_field)
            if binding is None:
                continue
            observations.append(FieldObservation(
                field=binding.field, source_type="qr_page", source_id=f"qr-{check.image_index}",
                image_index=check.image_index, value=value,
            ))


def mark_qr_conflicts(
    observations: list[FieldObservation], qr_checks: list[QrCheck], profile: BusinessProfile,
) -> bool:
    pack = profile.pack_declaration
    bindings = {b.page_field: b for b in pack.qr_field_bindings} if pack else {}
    has_conflict = False
    for check in qr_checks:
        for page_field, page_value in check.page_fields.items():
            binding = bindings.get(page_field)
            if binding is None:
                continue

            def comparable(value, declaration=binding):
                normalized = normalize_value(declaration.field, value) or ""
                if declaration.comparison == "alphanumeric":
                    return "".join(c for c in normalized.upper() if c.isalnum())
                return normalized

            candidates = [
                item.value for item in observations
                if item.image_index == check.image_index and item.source_type == "image"
                and item.field == binding.field and item.value not in (None, "")
            ]
            if any(comparable(value) != comparable(page_value) for value in candidates):
                check.status = FieldStatus.CONFLICT
                check.message = "二维码网页字段与图片识别结果冲突"
                has_conflict = True
    return has_conflict
