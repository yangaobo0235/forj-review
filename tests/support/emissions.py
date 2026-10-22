"""Emission-rule inputs shared by rule unit tests and reference database tests."""

from forj_review_service.businesses.rules.emission_standard import (
    EMISSION_STANDARD_CHECK_ID,
    run_emission_standard,
)
from forj_review_service.capabilities.specs import ReviewExecutionContext
from forj_review_service.contracts.recognition import RecognitionBatchResult
from forj_review_service.contracts.review import FieldObservation, ReviewRequest
from tests.support.pages import SCRAP_REPLACEMENT_QINGDAO


def observation(field: str, value: object, *, index: int = 1, uncertain: bool = False):
    return FieldObservation(
        field=field,
        source_type="image",
        source_id=f"registration-{index}-{field}",
        image_id=f"registration-{index}",
        image_index=index,
        value=value,
        uncertain=uncertain,
        document_type="registration_certificate",
        business_scope="old_vehicle",
    )


def run(page: object, materials: list[FieldObservation], *, lookup):
    result = run_emission_standard(
        ReviewExecutionContext(
            request=ReviewRequest(
                page_url="https://example.test/scrap-replace-qingdao/review/1",
                region="qingdao",
                page_fields={"old_vehicle.emission_standard": page},
            ),
            profile=SCRAP_REPLACEMENT_QINGDAO,
            batch=RecognitionBatchResult(),
            observations=tuple(materials),
        ),
        lookup=lookup,
    )
    assert len(result.checks) == 1
    assert result.checks[0].check_id == EMISSION_STANDARD_CHECK_ID
    return result.checks[0]
