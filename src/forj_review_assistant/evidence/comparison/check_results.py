"""检查结果去重和旧二维码响应到统一检查的兼容转换。"""

from collections.abc import Iterable

from forj_review_assistant.contracts.checks import CheckResultValue
from forj_review_assistant.contracts.recognition import CheckResult
from forj_review_assistant.contracts.review import FieldStatus, ImageInput, QrCheck


def unique_checks(checks: Iterable[CheckResult]) -> list[CheckResult]:
    """同 ID 只展示一次；重复报告矛盾时保留更保守的结论。"""
    results: dict[str, CheckResult] = {}
    severity = {"MATCH": 0, "INSUFFICIENT": 1, "CONFLICT": 2}
    for check in checks:
        previous = results.get(check.check_id)
        if previous is None or severity[check.status] > severity[previous.status]:
            results[check.check_id] = check
    return list(results.values())


def qr_review_checks(
    checks: Iterable[QrCheck], images: Iterable[ImageInput] = ()
) -> list[CheckResult]:
    by_index: dict[int, list[ImageInput]] = {}
    for image in images:
        by_index.setdefault(image.index, []).append(image)

    def image_identity(check: QrCheck) -> dict:
        matches = by_index.get(check.image_index, [])
        if len(matches) != 1 or not matches[0].image_id:
            return {}
        image = matches[0]
        return {
            "image_id": image.image_id,
            "source_id": image.image_id,
            "document_type": "scrap_certificate",
            "business_scope": image.business_scope,
            "group_title": image.group_title,
            "group_order": image.group_order,
        }

    return [
        CheckResult(
            check_id=f"QR-{index}",
            label="二维码官网核验",
            status="MATCH"
            if check.status is FieldStatus.MATCH
            else "CONFLICT"
            if check.status is FieldStatus.CONFLICT
            else "INSUFFICIENT",
            reason=check.message or "二维码核验未返回说明",
            reason_codes=([] if check.status is FieldStatus.MATCH else ["VALUE_MISMATCH"]
                          if check.status is FieldStatus.CONFLICT else ["EXTERNAL_UNAVAILABLE"]),
            values=[
                CheckResultValue(source="二维码内容", value=check.raw_value),
                CheckResultValue(source="二维码官网", value=check.url),
            ],
            evidence=[
                {
                    "source": "二维码官网字段",
                    **image_identity(check),
                    "image_index": check.image_index,
                    "detail": check.url,
                    "value": check.page_fields,
                }
            ],
        )
        for index, check in enumerate(checks, start=1)
    ]
