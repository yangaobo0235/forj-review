"""材料只确定搜索条件；具体销售版本始终由操作人员选择。"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from forj_review_service.capabilities.specs import ReviewExecutionContext, RuleExecutionResult
from forj_review_service.contracts.checks import CheckResult, CheckResultValue
from forj_review_service.contracts.evidence import FieldObservation
from forj_review_service.contracts.review import ReviewRequest
from forj_review_service.contracts.vehicle_models import model_code
from forj_review_service.evidence.comparison.evidence_values import (
    observation_evidence,
    trusted_page_value,
)
from forj_review_service.evidence.fields.readability import readable_value

LABELS = {
    "vehicle_license": "行驶证",
    "registration_certificate": "登记证书",
    "vehicle_nameplate": "铭牌",
}
AUXILIARY = {"registration_certificate", "vehicle_nameplate"}
_EMPTY_PAGE_VALUES = frozenset({"请选择", "请输入", "暂无", "未填写", "未采集"})


def _model_part(value: object) -> str:
    match = re.search(r"[A-Z0-9][A-Z0-9.\-]*$", model_code(value))
    return match.group() if match else ""


@dataclass(frozen=True)
class MaterialReading:
    value: str = ""
    issues: tuple[str, ...] = ()


def _resolve_reading(
    items: Sequence[FieldObservation],
    label: str,
    normalize: Callable[[object], str] = _model_part,
) -> MaterialReading:
    """任一冲突或不确定都交给人工，不通过投票或忽略坏读值获得默认值。"""
    reliable = [item for item in items if not item.uncertain and readable_value(item.value)]
    values = {normalize(item.value) for item in reliable} - {""}
    if len(values) > 1:
        return MaterialReading(issues=(f"{label}在材料中存在不同读值，请核对原图或手动输入",))
    if any(item.uncertain or not readable_value(item.value) for item in items):
        return MaterialReading(issues=(f"{label}有不确定读值，请核对原图或手动输入",))
    return MaterialReading(next(iter(values), ""))


def _values(items: Sequence[FieldObservation]) -> list[CheckResultValue]:
    return [
        CheckResultValue(
            source=LABELS.get(item.document_type, "材料"),
            value=item.value,
            source_id=item.source_id,
            image_id=item.image_id,
            image_index=item.image_index,
            document_type=item.document_type,
            uncertain=item.uncertain,
        )
        for item in items
    ]


def _brand_check(
    request: ReviewRequest,
    materials: Sequence[FieldObservation],
    desired: str,
    issues: Sequence[str],
    completion: dict[str, object],
) -> CheckResult:
    status, reason = "INSUFFICIENT", "品牌型号未取得可靠材料值，请核对原图"
    reason_codes = []
    if desired:
        page_value = trusted_page_value(request, "vehicle.brand_model")
        if not readable_value(page_value) or str(page_value).strip() in _EMPTY_PAGE_VALUES:
            reason = "品牌型号未取得可靠页面值，请核对页面；材料完整值为" + desired
            reason_codes = ["PAGE_VALUE_MISSING"]
        else:
            page_matches = model_code(page_value) == model_code(desired)
            status = "MATCH" if page_matches and not issues else "CONFLICT"
            reason = (
                "品牌型号与材料一致"
                if page_matches
                else f"页面品牌型号与材料组合值{desired}不一致，由人工判断是否回填"
            )
        if issues:
            reason = "；".join([*issues, reason])
    elif issues:
        reason = "；".join(issues)
    values = _values(materials)
    if desired:
        values.append(
            CheckResultValue(
                source="完整品牌型号", value=desired, derived_from="行驶证品牌文字＋完整整车型号"
            )
        )
    return CheckResult(
        check_id="VEHICLE-SOURCE-BRAND-MODEL",
        label="品牌型号",
        status=status,
        reason=reason,
        reason_codes=reason_codes,
        values=values,
        evidence=observation_evidence(materials),
        details={
            "permitted_fill_values": [desired] if desired else [],
            "brand_model_completion": completion,
        },
    )


def run_vehicle_source_model(context: ReviewExecutionContext) -> RuleExecutionResult:
    observations = [
        item
        for item in context.observations
        if item.source_type == "image" and item.business_scope == "vehicle"
    ]
    licenses = [
        item
        for item in observations
        if item.document_type == "vehicle_license" and item.field == "vehicle.brand_model"
    ]
    auxiliary = [
        item
        for item in observations
        if item.document_type in AUXILIARY
        and item.field in {"vehicle.announcement_model", "vehicle.engine_model"}
    ]
    announcements = [item for item in auxiliary if item.field == "vehicle.announcement_model"]
    engines = [item for item in auxiliary if item.field == "vehicle.engine_model"]

    license_brand = _resolve_reading(licenses, "行驶证品牌型号", model_code)
    license_model = _model_part(license_brand.value)
    announcement = _resolve_reading(announcements, "整车型号")
    engine = _resolve_reading(engines, "发动机型号")
    incompatible = bool(
        announcement.value and license_model and not announcement.value.startswith(license_model)
    )
    brand_issues = [*license_brand.issues, *announcement.issues]
    if incompatible:
        brand_issues.append("行驶证型号与辅助材料整车型号不一致，组合值供人工判断")

    # 根本未提供辅助型号字段时才能回退；冲突、空值、不确定均不可回退。
    full_model = announcement.value if announcements else license_model
    brand_text = license_brand.value[: -len(license_model)] if license_model else ""
    completed_value = (
        brand_text + full_model
        if brand_text
        and license_model
        and full_model
        and not license_brand.issues
        and not announcement.issues
        else ""
    )
    # 型号差异是核对结论，不取消材料组合值，也不代替审核员决定是否回填。
    desired = completed_value
    completion: dict[str, object] = {
        "license_value": license_brand.value,
        "brand_text": brand_text,
        "license_model": license_model,
        "auxiliary_model": announcement.value,
        "completed_value": completed_value,
        "status": "READY" if desired else "UNAVAILABLE",
        "basis": "行驶证品牌文字＋辅助材料完整整车型号"
        if announcements
        else "仅有行驶证型号，未提供辅助整车型号",
        "issues": brand_issues,
    }
    # 各型号仅使用自己的材料读值，不借 VIN 或其他字段阻断。
    search_issues = [*brand_issues, *engine.issues]
    inputs = {
        "announcement_model": full_model,
        "engine_model": engine.value,
        "issues": search_issues,
    }
    if not full_model and not brand_issues:
        inputs["issues"].append("未从图片取得可靠整车型号，请核对原图或手动输入")
    if not engine.value and not engine.issues:
        inputs["issues"].append("未从登记证或铭牌图片取得可靠发动机型号，请核对原图或手动输入")
    return RuleExecutionResult(
        checks=(
            _brand_check(
                context.request, [*licenses, *announcements], desired, brand_issues, completion
            ),
            CheckResult(
                check_id="VEHICLE-SOURCE-MODEL",
                label="车型",
                status="INSUFFICIENT",
                reason="按整车型号和发动机型号搜索候选，选择具体车型后回填规格参数",
                values=_values([*licenses, *auxiliary]),
                evidence=observation_evidence([*licenses, *auxiliary]),
                details={"model_search": inputs},
            ),
        )
    )
