from forj_review_assistant.contracts.review import (
    FieldComparison,
    FieldStatus,
    ReviewFieldSnapshot,
    ReviewRequest,
)
from forj_review_assistant.evidence.comparison.composite_fields import (
    build_page_composite_checks,
)


def request(**page_fields: str) -> ReviewRequest:
    return ReviewRequest(
        page_url="https://example.test/review/1",
        region="qingdao",
        page_fields=page_fields,
        review_fields=[
            ReviewFieldSnapshot(field=field, label=field, value=value, order=index)
            for index, (field, value) in enumerate(page_fields.items(), start=1)
        ],
    )


def comparison(field: str, value: str, status: FieldStatus = FieldStatus.MATCH) -> FieldComparison:
    return FieldComparison(
        field=field,
        left_value=value,
        right_value=value,
        status=status,
        message="材料值已核验",
    )


def test_invoice_composite_checks_page_pair_before_material() -> None:
    check = build_page_composite_checks(
        request(invoice_code="unused"),
        {"invoice.code": comparison("invoice.code", "INV-001")},
    )
    assert len(check) == 1
    assert check[0].status == "INSUFFICIENT"

    checks = build_page_composite_checks(
        request(**{"invoice.code": "INV-001", "invoice.invoice_no": "INV-001"}),
        {"invoice.code": comparison("invoice.code", "INV-001")},
    )
    assert len(checks) == 1
    assert checks[0].check_id == "FIELD-INVOICE-CODE-NO"
    assert checks[0].status == "MATCH"
    assert checks[0].details["page_values_match"] is True


def test_composite_task_uses_reviewer_facing_page_labels_and_keeps_differences() -> None:
    from forj_review_assistant.businesses.profiles import SCRAP_REPLACEMENT_QINGDAO
    from forj_review_assistant.contracts.evidence import DifferenceRange, EvidenceFact
    from forj_review_assistant.presentation.routing import build_review_tasks

    difference = DifferenceRange(
        kind="REPLACE", start=4, end=5, page_start=4, page_end=5, page_text="A"
    )
    material = FieldComparison(
        field="new_vehicle.vin",
        left_value="VIN-B",
        right_value="VIN-A",
        status=FieldStatus.CONFLICT,
        message="字段冲突",
        evidence=[
            EvidenceFact(
                source="图片识别",
                value="VIN-B",
                image_id="vin-image",
                differences=[difference],
            )
        ],
    )
    req = request(**{"new_vehicle.vin": "VIN-A", "page_ocr.new_vehicle_vin": "VIN-A"})
    task = next(
        item
        for item in build_review_tasks(
            request=req,
            profile=SCRAP_REPLACEMENT_QINGDAO,
            comparisons=[material],
            external_checks=[],
            business_checks=[],
            completeness=None,
            limitations=[],
        )
        if item.step_id == "FIELD-NEW-VEHICLE-VIN"
    )

    assert [item.source for item in task.page_values] == ["新车车架号", "OCR新车车架号"]
    assert task.values[0].differences == [difference]


def test_composite_page_mismatch_blocks_material_comparison() -> None:
    checks = build_page_composite_checks(
        request(**{"new_vehicle.vin": "VIN-A", "page_ocr.new_vehicle_vin": "VIN-B"}),
        {"new_vehicle.vin": comparison("new_vehicle.vin", "VIN-A")},
    )
    assert checks[0].check_id == "FIELD-NEW-VEHICLE-VIN"
    assert checks[0].status == "CONFLICT"
    assert "页面两个字段原始值不一致" in checks[0].reason


def test_composite_material_conflict_is_reported_after_matching_page_pair() -> None:
    checks = build_page_composite_checks(
        request(**{"invoice.code": "INV-PAGE", "invoice.invoice_no": "INV-PAGE"}),
        {"invoice.code": comparison("invoice.code", "INV-MATERIAL", FieldStatus.CONFLICT)},
    )
    assert checks[0].status == "CONFLICT"
    assert "材料提取值存在冲突" in checks[0].reason
