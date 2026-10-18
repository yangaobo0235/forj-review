"""过户材料的特殊核验：旧车跨材料 VIN。"""

import re
from dataclasses import dataclass

from forj_review_assistant.businesses.packs.transfer import (
    USED_PREFIX,
    USED_SCOPE,
)
from forj_review_assistant.capabilities.specs import ReviewExecutionContext, RuleExecutionResult
from forj_review_assistant.contracts.checks import CheckResult, CheckResultValue
from forj_review_assistant.contracts.evidence import FieldObservation
from forj_review_assistant.evidence.comparison.evidence_values import (
    observation_evidence,
    trusted_page_value,
)
from forj_review_assistant.evidence.fields.normalize import normalize_value
from forj_review_assistant.evidence.fields.readability import readable_value


@dataclass(frozen=True)
class Reading:
    value: str
    issue: str = ""


def _read(items: list[FieldObservation], field: str) -> Reading:
    if not items:
        return Reading("", "缺少可读材料值")
    if any(item.uncertain or not readable_value(item.value) for item in items):
        return Reading("", "材料读值不确定")
    values = {normalize_value(field, item.value) for item in items} - {None, ""}
    if len(values) != 1:
        return Reading("", "同类材料存在不同读值")
    return Reading(next(iter(values)))


def _items(context, field, kind, scope):
    return [item for item in context.observations if item.field == field
            and item.document_type == kind and item.business_scope == scope
            and item.source_type == "image"]


def _complete_vin(value):
    normalized = normalize_value(f"{USED_PREFIX}.vin", value)
    return bool(normalized and re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", normalized))


def _check(check_id, label, status, reason, items):
    # 回填是审核员逐条选择的操作，不要求另一份材料也识别完整或双方一致。
    # 片段和不确定读值保留展示，审核员可查看原图后通过人工输入补全。
    permitted = list(dict.fromkeys(
        str(item.value) for item in items if not item.uncertain and _complete_vin(item.value)
    ))
    return CheckResult(
        check_id=check_id, label=label, status=status, reason=reason,
        values=[CheckResultValue(
            source="登记证书1、2页" if item.document_type == "registration_certificate"
            else "二手车发票" if item.document_type == "used_car_invoice" else "新车发票",
            value=item.value, source_id=item.source_id, image_id=item.image_id,
            image_index=item.image_index, document_type=item.document_type,
            uncertain=item.uncertain, evidence_region=item.evidence_region,
        ) for item in items],
        evidence=observation_evidence(items),
        details={"permitted_fill_values": list(permitted)},
    )


def _old_vin(context):
    field = f"{USED_PREFIX}.vin"
    invoice = _items(context, field, "used_car_invoice", USED_SCOPE)
    registration = _items(context, "transfer_registration.vin", "registration_certificate", USED_SCOPE)
    left, right = _read(invoice, field), _read(registration, field)
    all_items = [*invoice, *registration]
    page = normalize_value(field, trusted_page_value(context.request, field))
    if left.issue or right.issue:
        return _check("TRANSFER-OLD-VIN", "识别车架号", "INSUFFICIENT",
                      "二手车发票或登记证书VIN缺失、不确定或有多个读值；"
                      "仍逐份与页面比对，可核对原图后选择完整识别值回填",
                      all_items)
    incomplete = [name for name, reading in (("二手车发票", left), ("登记证书", right))
                  if not _complete_vin(reading.value)]
    if incomplete:
        return _check("TRANSFER-OLD-VIN", "识别车架号", "INSUFFICIENT",
                      f"{'、'.join(incomplete)}VIN未读取为完整17位，需人工核对补全；"
                      "其他材料仍正常比对，可选择完整识别值回填", all_items)
    if left.value != right.value:
        return _check("TRANSFER-OLD-VIN", "识别车架号", "CONFLICT",
                      "二手车发票与登记证书VIN不一致；请核对原图与车辆归属，"
                      "由审核员选择正确识别值回填", all_items)
    status = "MATCH" if page == left.value else "CONFLICT" if page else "INSUFFICIENT"
    return _check("TRANSFER-OLD-VIN", "识别车架号", status,
                  "页面、二手车发票与登记证书VIN一致" if status == "MATCH"
                  else "两份材料旧车VIN一致，页面值不同或未填写，可按二手车发票回填",
                  all_items)


def run_transfer_documents(context: ReviewExecutionContext) -> RuleExecutionResult:
    check = _old_vin(context)
    field = f"{USED_PREFIX}.vin"
    page = normalize_value(field, trusted_page_value(context.request, field))
    # 整体结论包含跨材料核验；候选旁的“与页面一致/不一致”必须逐值比较，
    # 不能因为两份材料冲突就将实际上等于页面的那份也标成与页面不一致。
    for value in check.values:
        normalized = normalize_value(field, value.value)
        comparable = bool(page and normalized and not value.uncertain
                          and re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", normalized))
        value.comparison_status = (
            ("MATCH" if normalized == page else "CONFLICT") if comparable else "INSUFFICIENT"
        )
        value.conflicting = normalized != page if comparable else None
    return RuleExecutionResult(checks=(check,))
