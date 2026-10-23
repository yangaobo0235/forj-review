"""Review presentation controls must preserve evidence judgments and machine results."""

from dataclasses import replace

from forj_review_service.contracts.review import FieldObservation, ReviewRequest
from forj_review_service.evidence.comparison.review import build_field_comparisons
from tests.support.pages import SCRAP_REPLACEMENT_CHANGCHUN


def test_display_and_page_interaction_do_not_change_evidence_judgment():
    request = ReviewRequest(
        page_url="https://example.test/review", page_fields={"new_vehicle.vin": "VIN123"}
    )
    observations = [
        FieldObservation(
            field="new_vehicle.vin", source_type="page", source_id="page", value="VIN123"
        ),
        FieldObservation(
            field="new_vehicle.vin",
            source_type="image",
            source_id="license",
            value="VIN123",
            document_type="vehicle_license",
            uncertain=True,
        ),
    ]
    results = []
    for interaction in (True, False):
        profile = replace(SCRAP_REPLACEMENT_CHANGCHUN, page_interaction=interaction)
        results.append(build_field_comparisons(request, profile, observations))
    assert results[0] == results[1]
    assert (
        next((result for result in results[0] if result.field == "new_vehicle.vin")).status
        == "REVIEW_REQUIRED"
    )


def test_declared_visibility_keeps_machine_results_without_material_tasks():
    from forj_review_service.contracts.recognition import (
        CheckResult,
    )
    from forj_review_service.presentation.routing import build_review_tasks

    request = ReviewRequest(
        page_url="https://example.test/review",
        business_type="scrap_replacement",
        region="changchun",
    )
    tasks = build_review_tasks(
        request=request,
        profile=SCRAP_REPLACEMENT_CHANGCHUN,
        comparisons=[],
        external_checks=[],
        business_checks=[
            CheckResult(
                check_id="AFFILIATION-SUBJECT-001",
                label="挂靠关系",
                status="CONFLICT",
                reason="关系冲突",
            )
        ],
    )
    material_tasks = [task for task in tasks if task.category == "MATERIAL"]
    assert material_tasks == []
    affiliation = next((task for task in tasks if "AFFILIATION-SUBJECT-001" in task.step_id))
    assert not affiliation.visible
    assert affiliation.result_status == "CONFLICT" and affiliation.requires_reviewer_action
