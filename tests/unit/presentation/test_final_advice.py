from forj_review_assistant.contracts.recognition import CheckResult
from forj_review_assistant.contracts.review import (
    EvidenceFact,
    FieldComparison,
    FieldStatus,
    QrCheck,
)
from forj_review_assistant.presentation.advice import build_final_advice


def matched(field: str) -> FieldComparison:
    return FieldComparison(
        field=field, left_value="A", right_value="A", status=FieldStatus.MATCH
    )


def test_all_matching_checks_produce_pass_advice() -> None:
    cross_checks = [
        CheckResult(
            check_id="CROSS-OWNER-001", label="所有人", status="MATCH", reason="一致"
        ),
        CheckResult(
            check_id="CROSS-DATE-001", label="日期", status="MATCH", reason="同年"
        ),
    ]

    recommendation, advice = build_final_advice(
        [matched("old_vehicle.vin")],
        cross_checks,
        [QrCheck(status=FieldStatus.MATCH, message="二维码网页字段已提取")],
        [],
        [],
        [0.8, 1.0],
    )

    assert recommendation == "PASS"
    assert advice.title == "建议通过"
    assert advice.findings == []
    assert advice.recognition_confidence == 0.9


def test_field_conflict_produces_review_finding_with_original_sources() -> None:
    comparison = FieldComparison(
        field="old_vehicle.vin",
        status=FieldStatus.CONFLICT,
        message="多个来源存在不同值",
        evidence=[
            EvidenceFact(source="图片识别", detail="回收证明", value="VIN-1"),
            EvidenceFact(source="申请页面字段", value="VIN-2"),
        ],
    )

    recommendation, advice = build_final_advice([comparison], [], [], [], [], [])

    assert recommendation == "REVIEW_REQUIRED"
    assert advice.title == "建议人工复核"
    assert advice.findings[0].label == "报废车辆车架号"
    assert [(value.source, value.value) for value in advice.findings[0].values] == [
        ("图片识别", "VIN-1"),
        ("申请页面字段", "VIN-2"),
    ]


def test_missing_qr_result_is_not_invented_without_an_executed_external_check() -> None:
    recommendation, advice = build_final_advice(
        [matched("old_vehicle.vin")],
        [
            CheckResult(
                check_id="CROSS-OWNER-001",
                label="所有人",
                status="MATCH",
                reason="一致",
            ),
            CheckResult(
                check_id="CROSS-DATE-001", label="日期", status="MATCH", reason="同年"
            ),
        ],
        [],
        [],
        [],
        [],
    )

    assert recommendation == "PASS"
    assert advice.findings == []


def test_final_advice_does_not_require_qr_for_transfer() -> None:
    decision, advice = build_final_advice([], [], [], [], [], [])

    assert decision == "PASS"
    assert advice.findings == []


def test_insufficient_cross_check_and_qr_failure_are_all_findings() -> None:
    cross_check = CheckResult(
        check_id="CROSS-DATE-001",
        label="交车与开票日期同年",
        status="INSUFFICIENT",
        reason="基础日期尚未确定",
    )
    qr_check = QrCheck(
        status=FieldStatus.REVIEW_REQUIRED, message="二维码网页无法完成核验"
    )

    recommendation, advice = build_final_advice(
        [], [cross_check], [qr_check], ["未采集到审核图片"], ["识别超时"], []
    )

    assert recommendation == "REVIEW_REQUIRED"
    assert {finding.check_id for finding in advice.findings} >= {
        "CROSS-DATE-001",
        "QR-1",
        "ISSUE-1",
        "LIMITATION-1",
    }
    assert advice.summary == "发现 4 项需要审核人员确认"
