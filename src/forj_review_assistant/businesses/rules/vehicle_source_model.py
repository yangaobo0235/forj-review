"""材料只确定搜索条件；具体销售版本始终由操作人员选择。"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from forj_review_assistant.capabilities.specs import ReviewExecutionContext, RuleExecutionResult
from forj_review_assistant.contracts.checks import CheckResult, CheckResultValue
from forj_review_assistant.contracts.evidence import FieldObservation
from forj_review_assistant.contracts.review import ReviewRequest
from forj_review_assistant.contracts.vehicle_models import model_code
from forj_review_assistant.evidence.comparison.evidence_values import (
    observation_evidence,
    trusted_page_value,
)
from forj_review_assistant.evidence.fields.readability import readable_value

LABELS = {"vehicle_license": "行驶证", "registration_certificate": "登记证书",
          "vehicle_nameplate": "铭牌"}
AUXILIARY = {"registration_certificate", "vehicle_nameplate"}


def _model_part(value: object) -> str:
    match = re.search(r"[A-Z0-9][A-Z0-9.\-]*$", model_code(value))
    return match.group() if match else ""


@dataclass(frozen=True)
class MaterialReading:
    value: str = ""
    issues: tuple[str, ...] = ()


def _resolve_reading(
    items: Sequence[FieldObservation], label: str,
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


def _vin_conflict(observations: Sequence[FieldObservation]) -> bool:
    def vins(documents: set[str]) -> set[str]:
        return {model_code(item.value) for item in observations
                if item.document_type in documents and item.field == "vehicle.vin"
                and not item.uncertain and readable_value(item.value)}

    license_vins, auxiliary_vins = vins({"vehicle_license"}), vins(AUXILIARY)
    return bool(license_vins and auxiliary_vins and len(license_vins | auxiliary_vins) != 1)


def _values(items: Sequence[FieldObservation]) -> list[CheckResultValue]:
    return [CheckResultValue(
        source=LABELS.get(item.document_type, "材料"), value=item.value,
        source_id=item.source_id, image_id=item.image_id, image_index=item.image_index,
        document_type=item.document_type, uncertain=item.uncertain,
    ) for item in items]


def _brand_check(
    request: ReviewRequest, materials: Sequence[FieldObservation],
    desired: str, issues: Sequence[str],
) -> CheckResult:
    status, reason = "INSUFFICIENT", "品牌型号未取得可靠材料值，请核对原图"
    if desired:
        page_value = trusted_page_value(request, "vehicle.brand_model")
        status = "MATCH" if model_code(page_value) == model_code(desired) else "CONFLICT"
        reason = "品牌型号与材料一致" if status == "MATCH" else f"品牌型号应为{desired}，请回填完整值"
    elif issues:
        reason = "；".join(issues)
    values = _values(materials)
    if desired:
        values.append(CheckResultValue(source="完整品牌型号", value=desired,
                                       derived_from="行驶证品牌文字＋完整整车型号"))
    return CheckResult(
        check_id="VEHICLE-SOURCE-BRAND-MODEL", label="品牌型号", status=status,
        reason=reason, values=values, evidence=observation_evidence(materials),
        details={"permitted_fill_values": [desired] if desired else []},
    )


def run_vehicle_source_model(context: ReviewExecutionContext) -> RuleExecutionResult:
    observations = [item for item in context.observations
                    if item.source_type == "image" and item.business_scope == "vehicle"]
    licenses = [item for item in observations if item.document_type == "vehicle_license"
                and item.field == "vehicle.brand_model"]
    auxiliary = [item for item in observations if item.document_type in AUXILIARY
                 and item.field in {"vehicle.announcement_model", "vehicle.engine_model"}]
    announcements = [item for item in auxiliary if item.field == "vehicle.announcement_model"]
    engines = [item for item in auxiliary if item.field == "vehicle.engine_model"]

    license_brand = _resolve_reading(licenses, "行驶证品牌型号", model_code)
    license_model = _model_part(license_brand.value)
    announcement = _resolve_reading(announcements, "整车型号")
    engine = _resolve_reading(engines, "发动机型号")
    identity_conflict = _vin_conflict(observations)
    incompatible = bool(announcement.value and license_model
                        and not announcement.value.startswith(license_model))
    brand_issues = [*license_brand.issues, *announcement.issues]
    if identity_conflict:
        brand_issues.append("行驶证与辅助材料VIN不一致，请确认材料属于同一辆车")
    if incompatible:
        brand_issues.append("行驶证型号与辅助材料完整型号不兼容，不能自动补全")

    # 根本未提供辅助型号字段时才能回退；冲突、空值、不确定均不可回退。
    full_model = announcement.value if announcements else license_model
    desired = (license_brand.value[:-len(license_model)] + full_model
               if license_brand.value and license_model and full_model and not brand_issues else "")
    # 发动机的不确定性只影响搜索，不影响独立的品牌型号补全。
    inputs = {"announcement_model": "" if identity_conflict or incompatible else full_model,
              "engine_model": "" if identity_conflict else engine.value,
              "issues": [*brand_issues, *engine.issues]}
    return RuleExecutionResult(checks=(
        _brand_check(context.request, [*licenses, *announcements], desired, brand_issues),
        CheckResult(check_id="VEHICLE-SOURCE-MODEL", label="车型", status="INSUFFICIENT",
                    reason="按整车型号和发动机型号搜索候选，选择具体车型后回填规格参数",
                    values=_values(auxiliary), evidence=observation_evidence(auxiliary),
                    details={"model_search": inputs}),
    ))
