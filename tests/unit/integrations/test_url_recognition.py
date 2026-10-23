"""模型字段提取与原图扫码独立执行，二维码网址必须来自原图解码。"""

import asyncio
import json

import httpx
import pytest

from forj_review_service.config.settings import QwenConfig
from forj_review_service.contracts.review import (
    FieldStatus,
    ImageInput,
    ReviewRequest,
)
from forj_review_service.integrations.qr.web_verifier import QrWebVerifier
from forj_review_service.integrations.recognition.client import QwenClient
from forj_review_service.integrations.recognition.extraction import RecognitionService
from forj_review_service.runtime.review_service import ReviewService
from tests.support.pages import SCRAP_BUSINESS as TEST_BUSINESS
from tests.support.pages import business_registry
from tests.support.qr import qr_image_decoder

IMAGE_URL = "https://images.example.test/original.jpg?signature=synthetic%2Btoken&expires=123"
QR_URL = "https://qclt.mofcom.gov.cn/deal/scrap/validdata/synthetic"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "qr_url",
    [
        QR_URL,
        "https://evil.example/test",
        "https://qclt.mofcom.gov.cn/deal/scrap/printPreview/synthetic",
        None,
    ],
)
async def test_url_extraction_and_qr_verification_use_independent_image_decoding(qr_url):
    model_requests = []
    official_requests = []

    def model_handler(request):
        model_requests.append(request)
        payload = json.loads(request.content)
        assert payload["messages"][0]["content"][1]["image_url"]["url"] == IMAGE_URL
        assert "专用解码器读取" in payload["messages"][0]["content"][0]["text"]
        extraction = {
            "document_type": "scrap_certificate",
            "fields": {"scrap_certificate.certificate_no": "SYNTHETIC-1"},
            "confidence": 0.96,
            "qr_codes": [qr_url] if qr_url else [],
        }
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(extraction)}}]}
        )

    def official_handler(request):
        official_requests.append(request)
        return httpx.Response(200, text="回收证明编号：SYNTHETIC-1 车架号：LSVAA123456789012")

    service = ReviewService(
        registry=business_registry(), qr_decoder=qr_image_decoder({IMAGE_URL: QR_URL})
    )
    service.recognition = RecognitionService(
        QwenClient(
            QwenConfig("synthetic-key", "https://model.example.test/v1", "qwen-test"),
            transport=httpx.MockTransport(model_handler),
        )
    )
    service.qr_web = QrWebVerifier(
        allowed_hosts=["qclt.mofcom.gov.cn"], transport=httpx.MockTransport(official_handler)
    )
    image = ImageInput(
        index=0,
        image_id="url-0",
        src=IMAGE_URL,
        category_hint="scrap_certificate",
        business_scope="old_vehicle",
    )
    batch = await service.recognition.extract_async([image], business=TEST_BUSINESS)
    assert batch.completed_count == 1
    checks = await service._collect_qr_checks(
        ReviewRequest(page_url="https://example.test/review", region="qingdao", images=[image]),
        batch,
    )
    assert len(model_requests) == 1
    assert not hasattr(service, "qr_images")
    assert len(checks) == 1
    assert checks[0].status is FieldStatus.MATCH
    assert checks[0].page_fields["vin"] == "LSVAA123456789012"
    assert checks[0].raw_value == QR_URL
    assert checks[0].decoder == "zxing"
    assert len(official_requests) == 1
    assert str(official_requests[0].url) == QR_URL


@pytest.mark.asyncio
async def test_missing_extraction_is_not_reported_as_a_broken_image_link():
    service = ReviewService(registry=business_registry(), qr_decoder=qr_image_decoder({}))
    request = ReviewRequest(
        page_url="https://example.test/review",
        region="qingdao",
        images=[
            ImageInput(
                index=0,
                src=IMAGE_URL,
                category_hint="scrap_certificate",
                business_scope="old_vehicle",
            ),
        ],
    )
    from forj_review_service.contracts.recognition import RecognitionBatchResult

    checks = await service._collect_qr_checks(request, RecognitionBatchResult())
    assert len(checks) == 1
    assert checks[0].status is FieldStatus.REVIEW_REQUIRED
    assert "材料识别未完成" in checks[0].message
    assert checks[0].raw_value is None


@pytest.mark.asyncio
async def test_url_focused_retry_keeps_original_url(monkeypatch):
    seen = []

    def handler(request):
        payload = json.loads(request.content)
        seen.append(payload["messages"][0]["content"][1]["image_url"]["url"])
        assert "[0.1, 0.2, 0.3, 0.4]" in payload["messages"][0]["content"][0]["text"]
        result = {
            "document_type": "invoice",
            "fields": {"invoice.invoice_no": "123"},
            "confidence": 0.96,
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    recognition = RecognitionService(
        QwenClient(
            QwenConfig("key", "https://model.example.test/v1", "qwen-test"),
            transport=httpx.MockTransport(handler),
        )
    )
    await recognition._extract_image(
        ImageInput(index=0, src=IMAGE_URL, category_hint="invoice", business_scope="new_vehicle"),
        retry_reason="uncertain_field:invoice.invoice_no",
        focus_boxes=[[0.1, 0.2, 0.3, 0.4]],
        business=TEST_BUSINESS,
    )
    assert seen == [IMAGE_URL]


def test_extraction_prompt_delegates_qr_to_dedicated_decoder():
    from forj_review_service.businesses.recognition_prompts import build_recognition_prompt

    prompt = build_recognition_prompt(0, "old_vehicle", business=TEST_BUSINESS)
    assert "scrap_certificate.certificate_no" in prompt
    assert "二维码" in prompt
    assert "专用解码器读取" in prompt
    assert "不要把页眉、页脚的文字网址当作扫码结果" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404])
async def test_inaccessible_url_records_failure_without_upload_fallback(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "synthetic image access failure"})

    recognition = RecognitionService(
        QwenClient(
            QwenConfig("key", "https://model.example.test/v1", "qwen-test"),
            transport=httpx.MockTransport(handler),
        )
    )
    batch = await recognition.extract_async(
        [
            ImageInput(
                index=0, src=IMAGE_URL, category_hint="invoice", business_scope="new_vehicle"
            ),
        ],
        business=TEST_BUSINESS,
    )
    assert batch.failed_count == 1
    assert batch.completed_count == 0
    assert batch.failed_image_ids == ["0"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_url_model_timeout_keeps_manual_review_outcome():
    async def handler(request):
        await asyncio.sleep(0.1)
        raise AssertionError("request must be cancelled at the image deadline")

    recognition = RecognitionService(
        QwenClient(
            QwenConfig("key", "https://model.example.test/v1", "qwen-test"),
            transport=httpx.MockTransport(handler),
        ),
        image_timeout=0.01,
    )
    batch = await recognition.extract_async(
        [
            ImageInput(
                index=0, src=IMAGE_URL, category_hint="invoice", business_scope="new_vehicle"
            ),
        ],
        business=TEST_BUSINESS,
    )
    assert batch.timed_out_count == 1
    assert batch.completed_count == 0
    assert batch.timed_out_image_ids == ["0"]
