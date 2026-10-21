from forj_review_service.businesses.completeness import evaluate_extracted
from forj_review_service.contracts.recognition import (
    RecognitionBatchResult,
    RecognizedDocument,
)
from forj_review_service.contracts.review import FieldObservation, ReviewRequest
from tests.support.pages import SCRAP_REPLACEMENT_QINGDAO


def test_uncertain_scrap_fields_keep_chinese_names_values_and_each_source_image():
    request = ReviewRequest(
        page_url="https://example.test",
        images=[
            {
                "index": index,
                "imageId": f"scrap-{index}",
                "src": f"https://example.test/{index}.jpg",
                "businessScope": "old_vehicle",
                "categoryHint": "scrap_certificate",
            }
            for index in [1, 2]
        ],
    )
    batch = RecognitionBatchResult(
        recognized_documents=[
            RecognizedDocument(
                target_id="scrap-1",
                image_index=1,
                document_type="scrap_certificate",
                business_scope="old_vehicle",
                uncertain_fields=["vehicle.vin", "scrap_certificate.certificate_no"],
                uncertain_values={
                    "vehicle.vin": "LG6?123",
                    "scrap_certificate.certificate_no": None,
                },
            ),
            RecognizedDocument(
                target_id="scrap-2",
                image_index=2,
                document_type="scrap_certificate",
                business_scope="old_vehicle",
                uncertain_fields=["vehicle.vin"],
                uncertain_values={"vehicle.vin": "LG68123"},
            ),
        ]
    )
    report = evaluate_extracted(request, SCRAP_REPLACEMENT_QINGDAO, batch)
    issues = [issue for issue in report.issues if issue.code == "UNCERTAIN_REQUIRED_FIELD"]
    assert len(issues) == 1
    assert issues[0].message == "报废证明存在无法确认的字段"
    details = issues[0].field_details
    assert [(item.field_label, item.value, item.image_id) for item in details] == [
        ("报废车辆车架号", "LG6?123", "scrap-1"),
        ("报废证明编号", None, "scrap-1"),
        ("报废车辆车架号", "LG68123", "scrap-2"),
    ]


def test_uncertain_field_value_falls_back_to_observation_from_the_same_image():
    document = RecognizedDocument(
        target_id="scrap-2",
        image_index=2,
        document_type="scrap_certificate",
        business_scope="old_vehicle",
        uncertain_fields=["vehicle.vin"],
    )
    observations = [
        FieldObservation(
            field="old_vehicle.vin",
            source_type="image",
            source_id=f"scrap-{index}",
            image_id=f"scrap-{index}",
            image_index=index,
            value=value,
        )
        for index, value in [(1, "UNRELATED"), (2, "VIN?2")]
    ]
    report = evaluate_extracted(
        ReviewRequest(page_url="https://example.test"),
        SCRAP_REPLACEMENT_QINGDAO,
        RecognitionBatchResult(recognized_documents=[document], observations=observations),
    )
    detail = next(issue for issue in report.issues if issue.field_details).field_details[0]
    assert detail.value == "VIN?2"
    assert detail.image_id == "scrap-2"


def test_scrap_extracted_policy_enforces_all_six_materials() -> None:
    report = evaluate_extracted(
        ReviewRequest(page_url="https://example.test"),
        SCRAP_REPLACEMENT_QINGDAO,
        RecognitionBatchResult(),
    )
    assert report.status == "INCOMPLETE"
    assert report.enforced is True
    assert [item.display_name for item in report.checklist] == [
        "旧车行驶证",
        "旧车登记证第 1、2 页",
        "报废证明",
        "新车行驶证",
        "新车登记证第 1、2 页",
        "新车发票",
    ]
    assert all(item.status == "MISSING" for item in report.checklist)
    assert len([item for item in report.issues if item.code == "MISSING_MATERIAL"]) == 6


def test_scrap_uploaded_material_with_unconfirmed_type_is_uncertain() -> None:
    request = ReviewRequest.model_validate(
        {
            "pageUrl": "https://example.test",
            "images": [
                {
                    "index": 1,
                    "imageId": "old-license",
                    "src": "https://example.test/old-license.jpg",
                    "businessScope": "old_vehicle",
                    "categoryHint": "vehicle_license",
                }
            ],
        }
    )

    report = evaluate_extracted(
        request,
        SCRAP_REPLACEMENT_QINGDAO,
        RecognitionBatchResult(),
    )
    old_license = next(
        item for item in report.checklist if item.key == "old_vehicle:vehicle_license"
    )

    assert old_license.status == "UNCERTAIN"
    assert report.status == "UNCERTAIN"
    assert any(
        item.code == "UNCERTAIN_MATERIAL"
        and item.business_scope == "old_vehicle"
        and item.material_type == "vehicle_license"
        for item in report.issues
    )


def test_scrap_extracted_policy_accepts_six_materials_and_both_registration_pages() -> None:
    documents = [
        RecognizedDocument(
            target_id="old-license", document_type="vehicle_license", business_scope="old_vehicle"
        ),
        RecognizedDocument(
            target_id="old-registration",
            document_type="registration_certificate",
            business_scope="old_vehicle",
            covered_pages=[1, 2],
        ),
        RecognizedDocument(
            target_id="scrap", document_type="scrap_certificate", business_scope="old_vehicle"
        ),
        RecognizedDocument(
            target_id="new-license", document_type="vehicle_license", business_scope="new_vehicle"
        ),
        RecognizedDocument(
            target_id="new-registration",
            document_type="registration_certificate",
            business_scope="new_vehicle",
            covered_pages=[1, 2],
        ),
        RecognizedDocument(
            target_id="invoice", document_type="invoice", business_scope="new_vehicle"
        ),
    ]

    report = evaluate_extracted(
        ReviewRequest(page_url="https://example.test"),
        SCRAP_REPLACEMENT_QINGDAO,
        RecognitionBatchResult(recognized_documents=documents),
    )

    assert report.status == "COMPLETE"
    assert report.issues == []
    assert len(report.checklist) == 6
    assert all(item.status == "PRESENT" for item in report.checklist)
