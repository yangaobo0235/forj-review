"""Official QR verification and field consistency report independent results."""

import httpx
import pytest

from forj_review_service.contracts.recognition import RecognitionBatchResult, RecognizedDocument
from forj_review_service.contracts.review import FieldObservation, ImageInput, ReviewRequest
from forj_review_service.integrations.qr.web_verifier import QrWebVerifier
from forj_review_service.runtime.review_service import ReviewService


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["qingdao", "changchun"])
@pytest.mark.parametrize("different_field", [None, "scrap_certificate.certificate_no", "old_vehicle.vin"])
@pytest.mark.parametrize("entrypoint", ["workflow", "response_builder"])
async def test_qr_passes_independently_while_official_values_still_reach_field_comparison(
    region, different_field, entrypoint,
):
    official = {"scrap_certificate.certificate_no": "HS-2026-001", "old_vehicle.vin": "LSVAA123456789012"}
    values = dict(official)
    if different_field:
        values[different_field] = (
            "HS-2026-999" if different_field == "scrap_certificate.certificate_no" else "LSVBB123456789012"
        )
    request = ReviewRequest(
        page_url="https://example.test/review", region=region, page_fields=values,
        images=[ImageInput(
            index=0, src="https://images.example.test/certificate.jpg",
            category_hint="scrap_certificate", business_scope="old_vehicle",
        )],
    )
    batch = RecognitionBatchResult(
        recognized_documents=[RecognizedDocument(
            target_id="0", image_index=0, document_type="scrap_certificate",
            qr_codes=["https://qclt.mofcom.gov.cn/deal/scrap/validdata/synthetic"],
        )],
        observations=[
            FieldObservation(
                field=field, value=value, source_type="image", source_id="0", image_index=0,
                document_type="scrap_certificate",
            ) for field, value in values.items()
        ] + [FieldObservation(
            field="old_vehicle.vin", value=values["old_vehicle.vin"],
            source_type="image", source_id="registration", image_index=1,
            document_type="registration_certificate",
        )],
    )
    service = ReviewService(load_model=False)
    service.qr_web = QrWebVerifier(
        allowed_hosts=["qclt.mofcom.gov.cn"],
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, text="回收证明编号：HS-2026-001 车架号：LSVAA123456789012",
        )),
    )
    try:
        if entrypoint == "workflow":
            response, _ = await service.assist_with_batch_async(request, initial_batch=batch)
        else:
            checks = await service._collect_qr_checks(request, batch)
            response = service._build_response(request, batch, qr_checks=checks)
        assert len(response.qr_checks) == 1
        check = response.qr_checks[0]
        assert check.status == "MATCH"
        assert check.accessible is True and check.domain_valid is True
        assert check.page_fields == {"certificate_no": official["scrap_certificate.certificate_no"],
                                     "vin": official["old_vehicle.vin"]}
        for field, value in official.items():
            comparison = next(item for item in response.comparisons if item.field == field)
            assert comparison.status == ("CONFLICT" if field == different_field else "MATCH")
            assert any(item.source == "二维码官网字段" and item.value == value for item in comparison.evidence)
        if entrypoint == "workflow":
            qr_task = next(task for task in response.review_tasks if task.label == "二维码官网核验")
            assert qr_task.result_status == "MATCH"
            assert not qr_task.requires_reviewer_action
    finally:
        await service.aclose()
