import asyncio
import json
import logging

import httpx
import pytest

from forj_review_service.businesses.material_policies import DEFAULT_RETRY_POLICY
from forj_review_service.config.settings import QwenConfig
from forj_review_service.contracts.recognition import QwenExtraction
from forj_review_service.contracts.review import ImageInput
from forj_review_service.integrations.recognition.client import QwenClient
from forj_review_service.integrations.recognition.extraction import RecognitionService
from forj_review_service.integrations.recognition.retry import (
    retry_reason_for_extraction,
)


@pytest.fixture
def invoice_policy():
    from forj_review_service.businesses.materials import DOCUMENT_POLICIES

    return DOCUMENT_POLICIES["invoice"]


def invoice_image() -> ImageInput:
    return ImageInput(index=0, src="https://images.example.test/synthetic.jpg", category_hint="invoice", business_scope="new_vehicle")


class SequencedClient:
    def __init__(self) -> None:
        self.calls = 0

    async def extract_fields(
        self,
        image: dict[str, object],
        policy: object,
        *,
        retry_reason: str | None = None,
    ) -> QwenExtraction:
        self.calls += 1
        return QwenExtraction(
            document_type="invoice",
            fields={"invoice.invoice_no": "B" if self.calls == 2 else "A"},
            confidence=0.92 if self.calls == 2 else 0.69,
        )


@pytest.mark.asyncio
async def test_low_confidence_retries_once_and_uses_second_result() -> None:
    client = SequencedClient()
    result = await RecognitionService(client).extract_async([invoice_image()], retry_policy=DEFAULT_RETRY_POLICY)
    assert client.calls == 2
    assert [(item.field, item.value) for item in result.observations] == [
        ("invoice.invoice_no", "B"),
        ("invoice.code", "B"),
    ]
    assert result.retry_summary.qwen_retries == 1
    assert result.completed_count == 1


@pytest.mark.asyncio
async def test_invalid_schema_retry_adds_targeted_correction_to_prompt() -> None:
    prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        prompts.append(payload["messages"][0]["content"][0]["text"])
        if len(prompts) == 1:
            result = {
                "document_type": "invoice",
                "fields": {"invoice.code": "123"},
                "confidence": 0.9,
                "evidence_regions": [],
                "uncertain_fields": {"field": "invoice.code"},
            }
        else:
            result = {
                "document_type": "invoice",
                "fields": {"invoice.code": "123"},
                "confidence": 0.9,
                "evidence_regions": [],
                "uncertain_fields": [],
            }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(result)}}]},
        )

    client = QwenClient(
        QwenConfig("key", "https://dashscope.test/v1", "qwen3.7-plus"),
        transport=httpx.MockTransport(handler),
    )
    result = await RecognitionService(client).extract_async(
        [invoice_image()],
        retry_policy=DEFAULT_RETRY_POLICY,
    )

    assert len(prompts) == 2
    assert "上一次响应字段类型错误" not in prompts[0]
    assert "上一次响应字段类型错误" in prompts[1]
    assert "uncertain_fields 必须是字符串数组" in prompts[1]
    assert result.completed_count == 1
    assert result.failed_count == 0
    assert result.retry_summary.attempts[0].reason_code == "invalid_schema"


def test_uncertain_field_triggers_a_targeted_retry_reason(invoice_policy) -> None:
    """模型自报看不清的字段要触发定向重读，并把字段名带进原因。"""
    extraction = QwenExtraction(
        document_type="invoice",
        fields={"invoice.invoice_no": "26320000000801433801"},
        confidence=0.95,
        uncertain_fields=["invoice.amount", "invoice.code"],
    )

    reason = retry_reason_for_extraction(extraction, invoice_policy)

    assert reason == "uncertain_field:invoice.amount,invoice.code"


def test_extraction_without_uncertain_fields_keeps_the_old_triggers(invoice_policy) -> None:
    confident = QwenExtraction(
        document_type="invoice",
        fields={"invoice.invoice_no": "26320000000801433801"},
        confidence=0.95,
    )
    assert retry_reason_for_extraction(confident, invoice_policy) is None

    low_confidence = QwenExtraction(
        document_type="invoice",
        fields={"invoice.invoice_no": "26320000000801433801"},
        confidence=0.5,
    )
    assert retry_reason_for_extraction(low_confidence, invoice_policy) == "low_confidence"


@pytest.mark.asyncio
async def test_uncertain_field_retry_asks_the_model_to_reread_that_region() -> None:
    """定向重读指令要指名字段、要求回看原图区域，且不泄露上一次的响应。"""
    prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        prompts.append(payload["messages"][0]["content"][0]["text"])
        result = {
            "document_type": "invoice",
            "fields": {"invoice.invoice_no": "26320000000801433801", "invoice.amount": "152000"},
            "confidence": 0.95,
            "evidence_regions": [],
            "uncertain_fields": ["invoice.amount"] if len(prompts) == 1 else [],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    client = QwenClient(
        QwenConfig("key", "https://dashscope.test/v1", "qwen3.7-plus"),
        transport=httpx.MockTransport(handler),
    )
    result = await RecognitionService(client).extract_async(
        [invoice_image()],
        retry_policy=DEFAULT_RETRY_POLICY,
    )

    assert len(prompts) == 2
    assert "上一次识别把以下字段标记为不确定" not in prompts[0]
    assert "上一次识别把以下字段标记为不确定：invoice.amount" in prompts[1]
    assert "evidence_regions" in prompts[1]
    # 只说明哪些字段不确定，不回传模型上一次的输出内容。
    assert "26320000000801433801" not in prompts[1].split("上一次识别")[1]
    assert result.retry_summary.attempts[0].reason_code == "uncertain_field:invoice.amount"


class SteadyClient:
    """每次都返回同一个结果，用于观察重读有没有发生。"""

    def __init__(self, uncertain: list[str] | None = None) -> None:
        self.calls = 0
        self.uncertain = list(uncertain or [])

    async def extract_fields(self, image, policy, **_) -> QwenExtraction:
        self.calls += 1
        return QwenExtraction(
            document_type="invoice",
            fields={"invoice.invoice_no": "A"},
            confidence=0.95,
            uncertain_fields=list(self.uncertain),
        )


@pytest.mark.asyncio
async def test_image_without_a_retry_reason_reports_no_retry(caplog) -> None:
    client = SteadyClient()

    with caplog.at_level(logging.INFO, logger="forj_review_service"):
        await RecognitionService(client).extract_async(
            [invoice_image()], retry_policy=DEFAULT_RETRY_POLICY
        )

    assert client.calls == 1
    assert "retry_attempts=0 retry_result=-" in caplog.text


@pytest.mark.asyncio
async def test_uncertain_fields_surviving_the_reread_are_reported_as_retried(caplog) -> None:
    """重读跑过但仍不确定 —— 与"第一次就不确定"必须能在日志里区分。"""
    client = SteadyClient(uncertain=["invoice.amount"])

    with caplog.at_level(logging.INFO, logger="forj_review_service"):
        result = await RecognitionService(client).extract_async(
            [invoice_image()], retry_policy=DEFAULT_RETRY_POLICY
        )

    assert client.calls == 2
    assert result.retry_summary.qwen_retries == 1
    assert "uncertain_field_count=1" in caplog.text
    assert "retry_attempts=1 retry_result=succeeded" in caplog.text


@pytest.mark.asyncio
async def test_retry_skipped_for_lack_of_time_is_not_counted_as_an_attempt(caplog) -> None:
    client = SteadyClient(uncertain=["invoice.amount"])
    service = RecognitionService(client)

    with caplog.at_level(logging.INFO, logger="forj_review_service"):
        result = await service.extract_async(
            [invoice_image()],
            retry_policy=DEFAULT_RETRY_POLICY,
            # 剩余时间小于 minimum_retry_window_seconds，重读会被跳过。
            absolute_deadline=asyncio.get_running_loop().time() + 0.5,
        )

    assert client.calls == 1
    assert result.retry_summary.attempts[0].result == "skipped"
    assert "retry_attempts=0 retry_result=skipped" in caplog.text
