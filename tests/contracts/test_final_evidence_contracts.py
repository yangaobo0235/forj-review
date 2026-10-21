from dataclasses import replace

import pytest

from forj_review_service.businesses.registry import BusinessRegistry
from forj_review_service.contracts.recognition import RecognitionBatchResult
from forj_review_service.contracts.review import (
    FieldStatus,
    ImageInput,
    QrCheck,
    ReviewRequest,
)
from forj_review_service.evidence.comparison.check_results import qr_review_checks
from forj_review_service.runtime.review_service import ReviewService
from tests.support.pages import SCRAP_REPLACEMENT_QINGDAO


@pytest.mark.asyncio
async def test_qr_external_step_preserves_the_request_image_identity(monkeypatch):
    profile = replace(
        SCRAP_REPLACEMENT_QINGDAO, required_fields=(), material_policy=None, page_action_ids=()
    )
    service = ReviewService(registry=BusinessRegistry((profile,)))

    async def extract(*args):
        return RecognitionBatchResult()

    async def verify(*args):
        return [QrCheck(image_index=4, status=FieldStatus.MATCH, page_fields={"vin": "VIN1"})]

    monkeypatch.setattr(service, "_extract_documents", extract)
    monkeypatch.setattr(service, "_collect_qr_checks", verify)
    response = await service.assist_async(
        ReviewRequest(
            page_url="https://example.test/review",
            region="qingdao",
            images=[
                ImageInput(
                    index=4,
                    image_id="scrap-04",
                    src="https://example.test/image",
                    document_type_hint="scrap_certificate",
                    business_scope="old_vehicle",
                )
            ],
        )
    )
    step = next(step for step in response.review_tasks if step.category == "EXTERNAL")
    assert step.evidence[0].image_id == "scrap-04"
    assert step.evidence[0].source_id == "scrap-04"
    assert step.evidence[0].document_type == "scrap_certificate"
    assert step.evidence[0].image_index == 4


@pytest.mark.parametrize("indexes", [[], [3], [4, 4]])
def test_qr_evidence_does_not_guess_an_image_when_index_mapping_is_missing_or_ambiguous(indexes):
    images = [
        ImageInput(index=index, image_id=f"image-{order}", src="https://example.test/image")
        for order, index in enumerate(indexes)
    ]
    check = qr_review_checks([QrCheck(image_index=4)], images)[0]
    assert check.evidence[0].image_id is None
