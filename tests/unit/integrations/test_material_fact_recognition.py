import asyncio
import json

import httpx
import pytest

from forj_review_service.businesses.context import BusinessContext
from forj_review_service.businesses.material_policies import RetryPolicy
from forj_review_service.businesses.recognition_prompts import build_recognition_prompt
from forj_review_service.config.settings import QwenConfig
from forj_review_service.contracts.recognition import QwenExtraction
from forj_review_service.contracts.review import ImageInput
from forj_review_service.integrations.recognition.client import (
    QwenClient,
    QwenResponseSchemaError,
    parse_qwen_extraction,
)
from forj_review_service.integrations.recognition.extraction import RecognitionService
from tests.support.pages import (
    SCRAP_REPLACEMENT_CHANGCHUN,
    SCRAP_REPLACEMENT_QINGDAO,
    TRANSFER_DEFAULT,
    VEHICLE_SOURCE_DEFAULT,
)


def response(kind, facts, **extra):
    return {
        "document_type": kind,
        "type_confidence": 0.98,
        "confidence": 0.95,
        "identity_sides": [],
        "facts": facts,
        "uncertain_fields": [],
        **extra,
    }


def fact(field, value, **extra):
    return {"field": field, "label": "合成标签", "value": value, **extra}


@pytest.mark.parametrize(
    "profile,scope,kind,key,target",
    [
        (
            SCRAP_REPLACEMENT_QINGDAO,
            "old_vehicle",
            "vehicle_license",
            "vehicle.vin",
            "old_vehicle.vin",
        ),
        (
            SCRAP_REPLACEMENT_CHANGCHUN,
            "new_vehicle",
            "vehicle_license",
            "vehicle.vin",
            "new_vehicle.vin",
        ),
        (VEHICLE_SOURCE_DEFAULT, "vehicle", "vehicle_license", "vehicle.vin", "vehicle.vin"),
        (
            TRANSFER_DEFAULT,
            "transfer_vehicle",
            "used_car_invoice",
            "transfer_invoice.vin",
            "transfer_invoice.vin",
        ),
    ],
)
@pytest.mark.parametrize("count", [1, 4])
@pytest.mark.asyncio
async def test_content_recognition_is_independent_of_count_order_and_hints(
    profile, scope, kind, key, target, count
):
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        prompt = payload["messages"][0]["content"][0]["text"]
        assert "当前页面上传槽位" not in prompt
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(response(kind, [fact(key, "SYNTHETIC-VIN")]))
                        }
                    }
                ]
            },
        )

    service = RecognitionService(
        QwenClient(
            QwenConfig("synthetic-key", "https://model.test/v1", "synthetic-model"),
            transport=httpx.MockTransport(handler),
        )
    )
    images = [
        ImageInput(
            index=index,
            image_id=f"image-{index}",
            src=f"https://images.test/{index}.jpg",
            business_scope=scope,
            group_order=9 - index,
            document_type_hint="invoice",
        )
        for index in reversed(range(count))
    ]
    batch = await service.extract_async(images, business=BusinessContext.from_profile(profile))
    assert len(calls) == count
    assert batch.completed_count == count
    assert {item.document_type for item in batch.document_classifications} == {kind}
    assert {(item.field, item.value) for item in batch.observations} == {(target, "SYNTHETIC-VIN")}


@pytest.mark.parametrize("box", [[1, 2, 3, "未定位"], [5, 2, 1, 8], [0, 0, float("nan"), 10]])
def test_optional_coordinate_error_retains_facts_and_reports_location(box):
    parsed = parse_qwen_extraction(
        json.dumps(
            response(
                "vehicle_license",
                [
                    fact("vehicle.vin", "SYNTHETIC-VIN", box=box),
                    fact(None, "原文事实", section="另一区域"),
                ],
            )
        )
    )
    assert parsed.fields == {"vehicle.vin": "SYNTHETIC-VIN"}
    assert len(parsed.raw_fields) == 2
    assert parsed.raw_fields[0].box == []
    assert parsed.location_warnings == ["facts.0.box"]


def test_legacy_optional_coordinate_error_does_not_reject_valid_text():
    parsed = parse_qwen_extraction(
        json.dumps(
            {
                "document_type": "vehicle_license",
                "fields": {"vehicle.vin": "SYNTHETIC"},
                "raw_fields": [{"label": "合成标签", "value": "原文", "box": [1, 2, 3, "未知"]}],
            }
        )
    )
    assert parsed.fields == {"vehicle.vin": "SYNTHETIC"}
    assert parsed.location_warnings == ["raw_fields.0.box"]


def test_two_different_values_are_not_silently_overwritten():
    parsed = parse_qwen_extraction(
        json.dumps(
            response(
                "vehicle_license",
                [
                    fact("vehicle.vin", "VALUE-A"),
                    fact("vehicle.vin", "VALUE-B"),
                ],
            )
        )
    )
    assert parsed.field_alternatives == {"vehicle.vin": ["VALUE-A", "VALUE-B"]}
    assert [row.value for row in parsed.raw_fields] == ["VALUE-A", "VALUE-B"]


def test_field_name_echo_cannot_reenter_through_alternative_readings():
    parsed = parse_qwen_extraction(
        json.dumps(
            response(
                "vehicle_license",
                [
                    fact("vehicle.vin", "车辆识别代号"),
                    fact("vehicle.vin", "VALUE-A"),
                    fact("vehicle.vin", "vehicle.vin"),
                ],
            )
        )
    )
    assert parsed.fields == {"vehicle.vin": "VALUE-A"}
    assert parsed.field_alternatives == {}


def test_invalid_fact_text_still_fails_validation():
    with pytest.raises(QwenResponseSchemaError):
        parse_qwen_extraction(json.dumps(response("vehicle_license", [{"value": "missing label"}])))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error", "deadline"])
async def test_failed_retry_retains_first_evidence_and_records_attempt(failure):
    class Client:
        calls = 0

        async def extract_document(self, image, **kwargs):
            self.calls += 1
            if self.calls == 2:
                if failure == "error":
                    raise RuntimeError("synthetic failure")
                await asyncio.Event().wait()
            return QwenExtraction(
                document_type="vehicle_license",
                fields={"vehicle.vin": "FIRST"},
                uncertain_fields=["vehicle.owner"],
                confidence=0.95,
                type_confidence=0.99,
            )

    client = Client()
    batch = await RecognitionService(client, image_timeout=0.2, review_deadline=0.08).extract_async(
        [ImageInput(index=0, src="https://images.test/0.jpg", business_scope="old_vehicle")],
        business=BusinessContext.from_profile(SCRAP_REPLACEMENT_QINGDAO),
        retry_policy=RetryPolicy(minimum_retry_window_seconds=0.001),
    )
    assert client.calls == 2
    assert [(item.field, item.value) for item in batch.observations] == [
        ("old_vehicle.vin", "FIRST")
    ]
    assert batch.completed_count == 1
    assert batch.failed_count == batch.timed_out_count == 0
    assert batch.retry_summary.attempts[0].result == "failed"
    assert batch.recognized_documents[0].uncertain_fields == ["vehicle.owner"]
    assert batch.limitations


@pytest.mark.asyncio
async def test_manual_cancel_during_retry_still_propagates():
    retry_started = asyncio.Event()

    class Client:
        async def extract_document(self, image, *, retry_reason=None, **kwargs):
            if retry_reason:
                retry_started.set()
                await asyncio.Event().wait()
            return QwenExtraction(
                document_type="vehicle_license",
                fields={"vehicle.vin": "FIRST"},
                uncertain_fields=["vehicle.owner"],
                confidence=0.95,
            )

    task = asyncio.create_task(
        RecognitionService(Client()).extract_async(
            [ImageInput(index=0, src="https://images.test/0.jpg", business_scope="old_vehicle")],
            business=BusinessContext.from_profile(SCRAP_REPLACEMENT_QINGDAO),
        )
    )
    await asyncio.wait_for(retry_started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_type_confidence_is_independent_of_field_reading_confidence():
    class Client:
        async def extract_document(self, image, **kwargs):
            return QwenExtraction(
                document_type="identity_card",
                type_confidence=0.99,
                confidence=0.3,
                identity_sides=["BACK"],
            )

    batch = await RecognitionService(Client()).extract_async(
        [ImageInput(index=0, src="https://images.test/0.jpg", business_scope="identity")],
        business=BusinessContext.from_profile(SCRAP_REPLACEMENT_QINGDAO),
        retry_policy=RetryPolicy(semantic_retry_count=0),
    )
    assert batch.document_classifications[0].identity_sides == ["BACK"]
    assert batch.observations == []


@pytest.mark.asyncio
async def test_low_confidence_values_remain_uncertain_when_type_is_confirmed():
    class Client:
        async def extract_document(self, image, **kwargs):
            return QwenExtraction(
                document_type="vehicle_license",
                type_confidence=0.99,
                confidence=0.3,
                fields={"vehicle.vin": "UNRELIABLE"},
            )

    batch = await RecognitionService(Client()).extract_async(
        [ImageInput(index=0, src="https://images.test/0.jpg", business_scope="vehicle")],
        business=BusinessContext.from_profile(VEHICLE_SOURCE_DEFAULT),
        retry_policy=RetryPolicy(semantic_retry_count=0),
    )
    assert batch.document_classifications[0].document_type == "vehicle_license"
    assert batch.recognized_documents[0].uncertain_fields == ["vehicle.vin"]
    assert len(batch.observations) == 1
    assert batch.observations[0].uncertain is True
    assert batch.observations[0].confidence == 0.3


def test_prompt_uses_active_material_definitions_without_fixed_order():
    from dataclasses import replace

    business = BusinessContext.from_profile(VEHICLE_SOURCE_DEFAULT)
    material = replace(
        business.pack.materials[0],
        document_type="custom_material",
        fields=("custom.visible_field",),
        scoped_fields=(),
        scoped_guidance=(),
        guidance="读取合成字段。",
    )
    business = replace(business, pack=replace(business.pack, materials=(material,)))
    prompt = build_recognition_prompt(9, "vehicle", business=business)
    assert "custom.visible_field" in prompt
    assert "custom_material" in prompt
    assert "vehicle.engine_no" not in prompt
    assert "组内第" not in prompt


@pytest.mark.asyncio
async def test_reliable_retry_disagreements_remain_separate_evidence():
    class Client:
        calls = 0

        async def extract_document(self, image, **kwargs):
            self.calls += 1
            return QwenExtraction(
                document_type="vehicle_license",
                type_confidence=0.99,
                confidence=0.95,
                fields={"vehicle.vin": "FIRST" if self.calls == 1 else "SECOND"},
                uncertain_fields=["vehicle.owner"] if self.calls == 1 else [],
            )

    batch = await RecognitionService(Client()).extract_async(
        [ImageInput(index=0, src="https://images.test/0.jpg", business_scope="old_vehicle")],
        business=BusinessContext.from_profile(SCRAP_REPLACEMENT_QINGDAO),
    )
    assert {(item.field, item.value) for item in batch.observations} == {
        ("old_vehicle.vin", "FIRST"),
        ("old_vehicle.vin", "SECOND"),
    }
    assert "vehicle.owner" in batch.recognized_documents[0].uncertain_fields


@pytest.mark.asyncio
async def test_expired_budget_does_not_start_a_model_request():
    class Client:
        async def extract_document(self, image, **kwargs):
            raise AssertionError("expired tasks must not call the model")

    batch = await RecognitionService(Client()).extract_async(
        [ImageInput(index=0, src="https://images.test/0.jpg", business_scope="old_vehicle")],
        absolute_deadline=asyncio.get_running_loop().time() - 1,
        business=BusinessContext.from_profile(SCRAP_REPLACEMENT_QINGDAO),
    )
    assert batch.timed_out_count == 1


@pytest.mark.asyncio
async def test_truncated_response_is_never_accepted_as_a_complete_reading():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "content": json.dumps(
                                response("vehicle_license", [fact("vehicle.vin", "PARTIAL")])
                            )
                        },
                    }
                ]
            },
        )

    batch = await RecognitionService(
        QwenClient(
            QwenConfig("synthetic-key", "https://model.test/v1", "synthetic-model"),
            transport=httpx.MockTransport(handler),
        )
    ).extract_async(
        [ImageInput(index=0, src="https://images.test/0.jpg", business_scope="old_vehicle")],
        business=BusinessContext.from_profile(SCRAP_REPLACEMENT_QINGDAO),
    )
    assert len(calls) == 1
    assert batch.failed_count == 1
    assert not batch.observations
    assert any("model_output_truncated" in item for item in batch.limitations)
