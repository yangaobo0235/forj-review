import pytest

from forj_review_service.contracts.review import (
    FieldObservation,
    FieldStatus,
    ReviewFieldSnapshot,
    ReviewRequest,
)
from forj_review_service.evidence.comparison.review import build_field_comparisons
from forj_review_service.presentation.routing import build_review_tasks
from tests.support.pages import (
    SCRAP_REPLACEMENT_CHANGCHUN,
    SCRAP_REPLACEMENT_QINGDAO,
)


@pytest.mark.parametrize("profile", [SCRAP_REPLACEMENT_CHANGCHUN, SCRAP_REPLACEMENT_QINGDAO])
@pytest.mark.parametrize(
    "value,status", [("VIN-NEW", FieldStatus.MATCH), ("VIN-WRONG", FieldStatus.CONFLICT)]
)
def test_ocr_vin_compares_its_own_page_value_against_shared_materials(profile, value, status):
    fields = {"new_vehicle.vin": "VIN-NEW", "page_ocr.new_vehicle_vin": value}
    request = ReviewRequest(
        page_url="https://example.test/review",
        region=profile.region,
        page_fields=fields,
        review_fields=[
            ReviewFieldSnapshot(field=field, label=field, value=page_value, order=index)
            for index, (field, page_value) in enumerate(fields.items(), start=1)
        ],
    )
    observations = [
        FieldObservation(field=field, value=page_value, source_type="page", source_id="page")
        for field, page_value in fields.items()
    ]
    observations.extend(
        FieldObservation(
            field="new_vehicle.vin",
            value="VIN-NEW",
            source_type="image",
            source_id=f"image-{index}",
            image_id=f"image-{index}",
            image_index=index,
            document_type=document_type,
            business_scope="new_vehicle",
        )
        for index, document_type in enumerate(
            ["invoice", "vehicle_license", "registration_certificate"]
        )
    )
    # An unrelated material must never become a VIN candidate.
    observations.append(
        FieldObservation(
            field="new_vehicle.vin",
            value="WRONG",
            source_type="image",
            source_id="license",
            document_type="business_license",
        )
    )
    comparisons = build_field_comparisons(request, profile, observations)
    by_field = {item.field: item for item in comparisons}
    assert by_field["new_vehicle.vin"].status is FieldStatus.MATCH
    assert by_field["page_ocr.new_vehicle_vin"].status is status
    assert by_field["page_ocr.new_vehicle_vin"].right_value == value
    steps = build_review_tasks(
        request=request,
        profile=profile,
        comparisons=comparisons,
        external_checks=[],
        business_checks=[],
        completeness=None,
        limitations=[],
    )
    by_id = {item.step_id: item for item in steps}
    task = by_id["FIELD-NEW-VEHICLE-VIN"]
    assert task.page_values[0].value == "VIN-NEW"
    assert task.page_values[1].value == value
    assert task.result_status == ("CONFLICT" if value != "VIN-NEW" else "MATCH")
    assert task.requires_reviewer_action == (value != "VIN-NEW")
    assert len(task.evidence) == 3
    assert [item.value for item in task.values if item.image_id] == ["VIN-NEW"] * 3
