"""结论摘要日志：保留审核状态与来源，不记录证件原值。

这一行存在的理由：没有"历史上每个字段判成什么状态、冲突来自哪两类材料"，
就无法核对业务规则变更前后的结论与人工判断是否一致。
"""

from __future__ import annotations

import logging

from forj_review_service.contracts.evidence import EvidenceFact
from forj_review_service.contracts.review import (
    FieldComparison,
    FieldStatus,
    Recommendation,
    ReviewResponse,
)
from forj_review_service.presentation.response import _log_decision


def _response(comparisons: list[FieldComparison]) -> ReviewResponse:
    return ReviewResponse(
        presentation="FIELD_WORKBENCH",
        trace_id="trace-1",
        business_type="transfer",
        region="default",
        profile_version="v1",
        recommendation=Recommendation.REVIEW_REQUIRED,
        risk_level="MEDIUM",
        summary="演示",
        comparisons=comparisons,
    )


def test_decision_line_lists_every_field_status(caplog) -> None:
    comparison = FieldComparison(
        field="transfer.plate_no",
        status=FieldStatus.CONFLICT,
        evidence=[
            EvidenceFact(source="page", document_type="page", value="鲁B12345"),
            EvidenceFact(source="img", document_type="registration_certificate", value="鲁B99999"),
        ],
    )
    with caplog.at_level(logging.INFO, logger="forj_review_service"):
        _log_decision(_response([comparison]))
    line = next(item for item in caplog.records if "review decision" in item.getMessage())
    assert "trace_id=trace-1" in line.getMessage()
    assert "business=transfer" in line.getMessage()
    assert "transfer.plate_no:CONFLICT" in line.getMessage()
    # 冲突来自哪两类证据，是回放与调参唯一能看的线索。
    assert "transfer.plate_no:page+registration_certificate" in line.getMessage()


def test_decision_line_never_carries_business_values(caplog) -> None:
    """隐私边界：只记字段键、状态与材料类型，值一律不进日志。"""
    comparison = FieldComparison(
        field="transfer.vin",
        status=FieldStatus.MATCH,
        left_value="LFNAHXMW3SAB49001",
        right_value="LFNAHXMW3SAB49001",
        evidence=[EvidenceFact(source="img", document_type="vehicle_license", value="LFNAHXMW3SAB49001")],
    )
    with caplog.at_level(logging.INFO, logger="forj_review_service"):
        _log_decision(_response([comparison]))
    text = "\n".join(record.getMessage() for record in caplog.records if "review decision" in record.getMessage())
    assert "transfer.vin:MATCH" in text
    assert "LFNAHXMW3SAB49001" not in text
    assert "鲁B" not in text


def test_uncertain_evidence_is_named_so_a_false_conflict_can_be_traced(caplog) -> None:
    """假冲突率的原始数据：模型自己报了不确定的字段与来源必须分得出来。"""
    comparison = FieldComparison(
        field="transfer.invoice_date",
        status=FieldStatus.REVIEW_REQUIRED,
        evidence=[
            EvidenceFact(source="img", document_type="used_car_invoice", uncertain=True, value="2026-08-13"),
            EvidenceFact(source="img", document_type="vehicle_license", value="2026-08-13"),
        ],
    )
    with caplog.at_level(logging.INFO, logger="forj_review_service"):
        _log_decision(_response([comparison]))
    text = "\n".join(record.getMessage() for record in caplog.records if "review decision" in record.getMessage())
    assert "uncertain=transfer.invoice_date:used_car_invoice" in text
    assert "2026-08-13" not in text


def test_unconfigured_note_is_separate_from_a_real_review(caplog) -> None:
    """未配置与"跑完没冲突"必须能区分，否则回测集会被空响应污染。"""
    with caplog.at_level(logging.INFO, logger="forj_review_service"):
        _log_decision(_response([]), "unconfigured")
    text = "\n".join(record.getMessage() for record in caplog.records if "review decision" in record.getMessage())
    assert "note=unconfigured" in text
    assert "fields=-" in text
