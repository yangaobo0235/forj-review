"""Project QR website evidence using business-owned field bindings."""

from forj_review_service.businesses.profiles import BusinessProfile
from forj_review_service.contracts.review import FieldObservation, FieldStatus, QrCheck


def _verified(check: QrCheck) -> bool:
    return (
        check.status is FieldStatus.MATCH
        and check.accessible is not False
        and check.domain_valid is not False
    )


def append_qr_observations(
    observations: list[FieldObservation],
    qr_checks: list[QrCheck],
    profile: BusinessProfile,
) -> None:
    """官网值进入统一字段比对，字段差异不改变二维码官网核验结论。"""
    pack = profile.pack_declaration
    bindings = {b.page_field: b for b in pack.qr_field_bindings} if pack else {}
    for check in qr_checks:
        if not _verified(check):
            continue
        for page_field, value in check.page_fields.items():
            binding = bindings.get(page_field)
            if binding is None:
                continue
            observations.append(
                FieldObservation(
                    field=binding.field,
                    source_type="qr_page",
                    source_id=f"qr-{check.image_index}",
                    image_index=check.image_index,
                    value=value,
                )
            )
