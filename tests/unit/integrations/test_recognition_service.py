import asyncio
import logging

import httpx
import pytest

from forj_review_service.config.settings import QwenConfig
from forj_review_service.contracts.recognition import (
    QwenExtraction,
)
from forj_review_service.contracts.review import ImageInput
from forj_review_service.integrations.recognition.client import QwenClient
from forj_review_service.integrations.recognition.extraction import (
    RecognitionService,
)
from tests.support.pages import SCRAP_BUSINESS as TEST_BUSINESS


class FakeQwenClient:
    def __init__(
        self,
        classification: QwenExtraction | None = None,
        extraction: QwenExtraction | None = None,
        fail_indices: set[int] | None = None,
    ) -> None:
        self.classification = classification
        self.extraction = extraction
        self.fail_indices = fail_indices or set()
        self.classification_calls: list[int] = []
        self.extraction_calls: list[tuple[int, str]] = []

    async def extract_document(self, image, *, business=None, retry_reason=None, focus_boxes=None):
        image_index = int(image["index"])
        if self.extraction is None:
            if self.classification is None:
                raise RuntimeError("缺少提取测试结果")
            result = QwenExtraction(
                document_type=self.classification.document_type,
                type_confidence=self.classification.confidence,
                identity_sides=self.classification.identity_sides,
            )
        else:
            result = self.extraction.model_copy()
            if self.classification:
                result.type_confidence = self.classification.confidence
                result.identity_sides = self.classification.identity_sides
        self.extraction_calls.append((image_index, result.document_type))
        if image_index in self.fail_indices:
            raise RuntimeError("Qwen 调用失败")
        return result


def extracted_values(service, images):
    """业务提取测试统一走生产使用的异步接口，值投影仅服务于断言。"""
    batch = asyncio.run(service.extract_async(images, business=TEST_BUSINESS))
    recognized = {
        item.field: (str(item.value), item.image_index) for item in batch.observations if item.value
    }
    return recognized, batch.limitations, batch.confidences


def image(
    index: int = 0,
    category_hint: str = "unknown",
    business_scope: str | None = None,
    group_order: int | None = None,
) -> ImageInput:
    values = {
        "index": index,
        "src": "https://images.example.test/synthetic.jpg",
        "category_hint": category_hint,
    }
    if business_scope is None:
        business_scope = {
            "invoice": "new_vehicle",
            "scrap_certificate": "old_vehicle",
            "registration_certificate": "old_vehicle",
        }.get(category_hint, "unknown")
    values["business_scope"] = business_scope
    if group_order is not None:
        values["group_order"] = group_order
    return ImageInput(**values)


def test_identity_card_is_extracted_without_sensitive_number() -> None:
    fake = FakeQwenClient(
        extraction=QwenExtraction(
            document_type="identity_card",
            fields={"identity_card.name": "张三", "identity_card.side": "FRONT"},
            confidence=0.96,
        )
    )

    recognized, limitations, confidences = extracted_values(
        RecognitionService(fake), [image(category_hint="id_card")]
    )

    assert recognized == {
        "identity_card.name": ("张三", 0),
        "identity_card.side": ("FRONT", 0),
    }
    assert confidences == [0.96]
    assert fake.classification_calls == []
    assert fake.extraction_calls == [(0, "identity_card")]
    assert limitations == []


def test_unavailable_identity_card_is_reported_as_unreadable() -> None:
    fake = FakeQwenClient()
    identity_card = ImageInput(
        index=0,
        src="blob:https://example.test/id-card",
        category_hint="id_card",
    )

    _, limitations, _ = extracted_values(RecognitionService(fake), [identity_card])

    assert limitations == ["图片 0 内容不可用"]


def test_known_invoice_uses_one_extraction_call() -> None:
    fake = FakeQwenClient(
        extraction=QwenExtraction(
            document_type="invoice",
            fields={"invoice.invoice_no": "123"},
            confidence=0.9,
        )
    )

    recognized, limitations, confidences = extracted_values(
        RecognitionService(fake), [image(category_hint="invoice")]
    )

    assert recognized == {
        "invoice.invoice_no": ("123", 0),
        "invoice.code": ("123", 0),
    }
    assert limitations == []
    assert confidences == [0.9]
    assert fake.classification_calls == []
    assert fake.extraction_calls == [(0, "invoice")]


def test_scrap_replacement_slot_hint_skips_classification() -> None:
    """分区名只是角色，组内槽位仍能把报废置换图片路由到具体材料。"""
    fake = FakeQwenClient(
        extraction=QwenExtraction(
            document_type="registration_certificate",
            fields={"vehicle.vin": "SYNTHETIC-VIN"},
            confidence=0.9,
        )
    )

    recognized, limitations, _ = extracted_values(
        RecognitionService(fake),
        [image(category_hint="old_vehicle", business_scope="old_vehicle", group_order=2)],
    )

    assert recognized == {"old_vehicle.vin": ("SYNTHETIC-VIN", 0)}
    assert limitations == []
    assert fake.classification_calls == []
    assert fake.extraction_calls == [(0, "registration_certificate")]


@pytest.mark.asyncio
async def test_invoice_number_derives_page_code_and_preserves_evidence_region() -> None:
    fake = FakeQwenClient(
        extraction=QwenExtraction(
            document_type="invoice",
            fields={"invoice.invoice_no": "2632000000731322946"},
            evidence_regions=[
                {
                    "field": "invoice.invoice_no",
                    "image_index": 3,
                    "box": [10, 20, 300, 80],
                }
            ],
        )
    )

    result = await RecognitionService(fake).extract_async(
        [image(index=3, category_hint="invoice", business_scope="new_vehicle")],
        business=TEST_BUSINESS,
    )

    observations = {item.field: item for item in result.observations}
    assert observations["invoice.invoice_no"].derived_from is None
    assert observations["invoice.code"].derived_from == "invoice.invoice_no"
    assert observations["invoice.invoice_no"].evidence_region == [10, 20, 300, 80]
    assert observations["invoice.code"].evidence_region == [10, 20, 300, 80]


def test_unknown_image_is_typed_and_extracted_in_one_call() -> None:
    fake = FakeQwenClient(
        classification=QwenExtraction(document_type="invoice", confidence=0.98),
        extraction=QwenExtraction(
            document_type="invoice",
            fields={"invoice.invoice_no": "123"},
            confidence=0.9,
        ),
    )

    recognized, limitations, _ = extracted_values(
        RecognitionService(fake), [image(business_scope="new_vehicle")]
    )

    assert recognized == {"invoice.invoice_no": ("123", 0), "invoice.code": ("123", 0)}
    assert limitations == []
    assert fake.classification_calls == []
    assert fake.extraction_calls == [(0, "invoice")]


def test_unreliable_classification_does_not_extract_fields() -> None:
    classifications = (
        QwenExtraction(document_type="invoice", confidence=0.69),
        QwenExtraction(document_type="unsupported", confidence=0.99),
    )

    for classification in classifications:
        fake = FakeQwenClient(classification=classification)
        recognized, limitations, _ = extracted_values(RecognitionService(fake), [image()])

        assert recognized == {}
        assert limitations
        assert 1 <= len(fake.extraction_calls) <= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [False, True])
async def test_joint_failure_does_not_invent_a_content_type(timeout):
    class FailedExtraction(FakeQwenClient):
        async def extract_document(
            self, image, *, business=None, retry_reason=None, focus_boxes=None
        ):
            self.extraction_calls.append((image["index"], "vehicle_license"))
            if timeout:
                await asyncio.sleep(1)
            raise RuntimeError("synthetic failure")

    client = FailedExtraction(
        classification=QwenExtraction(document_type="vehicle_license", confidence=0.98)
    )
    batch = await RecognitionService(client, image_timeout=0.02).extract_async(
        [
            # 没有槽位序号才是真正未知材料；组内第 3 张旧车图现在有业务声明，
            # 应直接按回收证明提取，不再进入分类模型。
            image(index=3, business_scope="old_vehicle"),
        ],
        business=TEST_BUSINESS,
    )
    assert batch.failed_count + batch.timed_out_count == 1
    assert not batch.recognized_documents
    assert batch.document_classifications == []
    assert client.classification_calls == []
    assert client.extraction_calls == [(3, "vehicle_license")]


@pytest.mark.asyncio
async def test_content_classification_is_reused_by_field_retry():
    client = FakeQwenClient(
        classification=QwenExtraction(document_type="invoice", confidence=0.98),
        extraction=QwenExtraction(
            document_type="invoice", fields={"invoice.invoice_no": "synthetic"}, confidence=0.3
        ),
    )
    batch = await RecognitionService(client).extract_async(
        [image(business_scope="new_vehicle")], business=TEST_BUSINESS
    )
    assert client.classification_calls == []
    assert len(client.extraction_calls) == 2
    assert len(batch.document_classifications) == 1


@pytest.mark.asyncio
async def test_content_side_is_retained_when_no_identity_name_is_read():
    client = FakeQwenClient(
        classification=QwenExtraction(
            document_type="identity_card", confidence=0.98, identity_sides=["BACK"]
        ),
    )
    batch = await RecognitionService(client).extract_async(
        [image(business_scope="identity")], business=TEST_BUSINESS
    )
    assert batch.completed_count == 1
    assert batch.document_classifications[0].identity_sides == ["BACK"]


@pytest.mark.asyncio
async def test_low_confidence_field_extraction_cannot_confirm_an_unlabelled_identity_side():
    client = FakeQwenClient(
        extraction=QwenExtraction(
            document_type="identity_card",
            fields={"identity_card.side": "FRONT"},
            confidence=0.4,
        )
    )
    batch = await RecognitionService(client).extract_async(
        [image(category_hint="id_card", business_scope="identity")], business=TEST_BUSINESS
    )
    assert batch.document_classifications == []


@pytest.mark.asyncio
async def test_low_classification_confidence_is_never_a_presence_fact():
    client = FakeQwenClient(classification=QwenExtraction(document_type="invoice", confidence=0.5))
    batch = await RecognitionService(client).extract_async(
        [image(business_scope="new_vehicle")], business=TEST_BUSINESS
    )
    assert batch.document_classifications == []


@pytest.mark.asyncio
async def test_classification_failure_and_timeout_have_no_presence_fact():
    class Timeout(FakeQwenClient):
        async def extract_document(self, image, **kwargs):
            await asyncio.sleep(1)

    for client in (FakeQwenClient(), Timeout()):
        batch = await RecognitionService(client, image_timeout=0.02).extract_async(
            [image(business_scope="old_vehicle")], business=TEST_BUSINESS
        )
        assert batch.failed_count + batch.timed_out_count == 1
        assert not batch.document_classifications


@pytest.mark.asyncio
async def test_page_label_cannot_confirm_identity_side_when_picture_cannot_be_read():
    unreadable = image(category_hint="id_card", business_scope="identity")
    unreadable.identity_sides_hint = ["FRONT"]
    unreadable.collection_error = "synthetic read error"
    batch = await RecognitionService(FakeQwenClient()).extract_async(
        [unreadable], business=TEST_BUSINESS
    )
    assert batch.failed_count == 1
    assert batch.document_classifications == []
    from forj_review_service.businesses.material_presence import evaluate_presence
    from forj_review_service.contracts.review import ReviewRequest

    request = ReviewRequest(
        page_url="https://example.test/scrap-replace-qingdao",
        business_type="scrap_replacement",
        region="qingdao",
        images=[unreadable],
    )
    presence = evaluate_presence(request, TEST_BUSINESS.pack.material_policy, batch)
    items = [item for group in presence.groups for option in group.alternatives for item in option]
    assert any(
        item.key.endswith("identity_card:FRONT") and item.status == "UNCERTAIN" for item in items
    )
    assert unreadable.identity_sides_hint == ["FRONT"]


def test_mismatched_extraction_type_discards_fields() -> None:
    fake = FakeQwenClient(
        extraction=QwenExtraction(
            document_type="old_vehicle",
            fields={"invoice.code": "123"},
        )
    )

    recognized, limitations, _ = extracted_values(
        RecognitionService(fake), [image(category_hint="invoice")]
    )

    assert recognized == {}
    assert any("资料类型不一致" in item for item in limitations)


def test_non_allowlisted_fields_are_discarded() -> None:
    fake = FakeQwenClient(
        extraction=QwenExtraction(
            document_type="invoice",
            fields={"invoice.invoice_no": "123", "old_vehicle.engine_model": "OUT-OF-SCOPE"},
        )
    )

    recognized, _, _ = extracted_values(RecognitionService(fake), [image(category_hint="invoice")])

    assert recognized == {"invoice.invoice_no": ("123", 0), "invoice.code": ("123", 0)}


def test_classified_unknown_image_keeps_resolved_policy_allowlist() -> None:
    fake = FakeQwenClient(
        classification=QwenExtraction(document_type="invoice", confidence=0.98),
        extraction=QwenExtraction(
            document_type="invoice",
            fields={"invoice.invoice_no": "123", "old_vehicle.engine_model": "OUT-OF-SCOPE"},
        ),
    )

    recognized, _, _ = extracted_values(
        RecognitionService(fake), [image(category_hint="unknown", business_scope="new_vehicle")]
    )

    assert recognized == {"invoice.invoice_no": ("123", 0), "invoice.code": ("123", 0)}


def test_one_image_failure_does_not_stop_the_next_image() -> None:
    fake = FakeQwenClient(
        extraction=QwenExtraction(
            document_type="invoice",
            fields={"invoice.invoice_no": "123"},
        ),
        fail_indices={0},
    )

    recognized, limitations, _ = extracted_values(
        RecognitionService(fake), [image(0, "invoice"), image(1, "invoice")]
    )

    assert recognized == {"invoice.invoice_no": ("123", 1), "invoice.code": ("123", 1)}
    assert limitations
    assert fake.extraction_calls == [(0, "invoice"), (1, "invoice")]


def test_image_completion_log_names_the_uncertain_fields(caplog: object) -> None:
    """不确定字段是一票否决的直接原因，控制台必须能直接看到是哪几个。"""
    fake = FakeQwenClient(
        extraction=QwenExtraction(
            document_type="invoice",
            fields={"invoice.invoice_no": "123"},
            confidence=0.9,
            uncertain_fields=["invoice.invoice_date", "invoice.amount"],
        )
    )

    # 单图完成事件走 uvicorn 的 logger（服务日志直接打到 Uvicorn 控制台）。
    with caplog.at_level(logging.INFO, logger="forj_review_service"):
        extracted_values(RecognitionService(fake), [image(category_hint="invoice")])

    assert "image=0" in caplog.text
    assert "uncertain_field_count=2" in caplog.text
    assert "uncertain_fields=invoice.amount,invoice.invoice_date" in caplog.text
    # 重读的结果同样要能看出来，否则分不清"第一次就不确定"和"重读后仍不确定"。
    assert "retry_attempts=" in caplog.text
    assert "retry_result=" in caplog.text


def test_image_completion_log_marks_an_uncertainty_free_image(caplog: object) -> None:
    """没有不确定字段时用 `-` 占位，字段数仍固定，便于 grep。"""
    fake = FakeQwenClient(
        extraction=QwenExtraction(
            document_type="invoice",
            fields={"invoice.invoice_no": "123"},
            confidence=0.95,
            uncertain_fields=[],
        )
    )

    # 单图完成事件走 uvicorn 的 logger（服务日志直接打到 Uvicorn 控制台）。
    with caplog.at_level(logging.INFO, logger="forj_review_service"):
        extracted_values(RecognitionService(fake), [image(category_hint="invoice")])

    assert "uncertain_field_count=0 uncertain_fields=-" in caplog.text


def test_qwen_failure_is_sanitized_and_logged(caplog: object) -> None:
    class SensitiveFailureClient(FakeQwenClient):
        async def extract_document(
            self, image: dict[str, object], *, business=None, retry_reason=None, focus_boxes=None
        ) -> QwenExtraction:
            raise RuntimeError("HTTP 400 raw model response VIN=SHOULD-NOT-LEAK")

    with caplog.at_level(
        logging.WARNING, logger="forj_review_service.integrations.recognition.extraction"
    ):
        _, limitations, _ = extracted_values(
            RecognitionService(SensitiveFailureClient()), [image(category_hint="invoice")]
        )

    assert limitations == ["图片 0 Qwen 处理失败（qwen_runtime_error）"]
    assert "SHOULD-NOT-LEAK" not in caplog.text
    assert "image=0" in caplog.text
    assert "error_code=qwen_runtime_error" in caplog.text


@pytest.mark.asyncio
async def test_malformed_qwen_api_envelope_is_counted_as_safe_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{not-json", request=request)

    client = QwenClient(
        QwenConfig("key", "https://dashscope.test/v1", "qwen3.7-plus"),
        transport=httpx.MockTransport(handler),
    )

    result = await RecognitionService(client).extract_async(
        [image(business_scope="new_vehicle")], business=TEST_BUSINESS
    )

    assert result.failed_count == 1
    assert result.failed_image_ids == ["0"]
    assert result.limitations == ["图片 0 Qwen 处理失败（invalid_response_structure）"]


@pytest.mark.asyncio
async def test_six_images_start_concurrently() -> None:
    class BlockingClient(FakeQwenClient):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.max_active = 0
            self.all_started = asyncio.Event()
            self.release = asyncio.Event()

        async def extract_document(
            self, image: dict[str, object], *, business=None, retry_reason=None, focus_boxes=None
        ) -> QwenExtraction:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active == 6:
                self.all_started.set()
            await self.release.wait()
            self.active -= 1
            return QwenExtraction(document_type="invoice", fields={"invoice.code": "123"})

    client = BlockingClient()
    service = RecognitionService(client, image_timeout=1, review_deadline=2)
    task = asyncio.create_task(
        service.extract_async(
            [image(index, "invoice") for index in range(6)], business=TEST_BUSINESS
        )
    )
    await asyncio.wait_for(client.all_started.wait(), timeout=0.5)

    assert client.max_active == 6
    client.release.set()
    result = await task
    assert result.completed_count == 6


@pytest.mark.asyncio
async def test_unknown_image_uses_the_joint_recognition_contract() -> None:
    client = FakeQwenClient(
        classification=QwenExtraction(document_type="invoice", confidence=0.98),
        extraction=QwenExtraction(
            document_type="invoice",
            fields={
                "invoice.invoice_no": "123",
                "old_vehicle.engine_model": "OUT-OF-SCOPE",
            },
            confidence=0.9,
        ),
    )
    result = await RecognitionService(client).extract_async(
        [image(category_hint="unknown", business_scope="new_vehicle")], business=TEST_BUSINESS
    )

    assert client.classification_calls == []
    assert client.extraction_calls == [(0, "invoice")]
    assert {(item.field, item.value) for item in result.observations} == {
        ("invoice.invoice_no", "123"),
        ("invoice.code", "123"),
    }


@pytest.mark.asyncio
async def test_business_scope_rejects_incompatible_model_document_type() -> None:
    class CombinedClient(FakeQwenClient):
        async def extract_document(
            self, image: dict[str, object], *, business=None, retry_reason=None, focus_boxes=None
        ) -> QwenExtraction:
            return QwenExtraction(
                document_type="invoice",
                fields={
                    "invoice.code": "123",
                    "old_vehicle.vin": "MUST-NOT-BE-COMPARED-AS-OLD-VEHICLE",
                },
                confidence=0.9,
            )

    result = await RecognitionService(CombinedClient()).extract_async(
        [image(category_hint="scrap_certificate", business_scope="old_vehicle")],
        business=TEST_BUSINESS,
    )

    assert result.observations == []
    assert any("不属于它的资料区域" in item for item in result.limitations)


@pytest.mark.asyncio
async def test_identical_vehicle_documents_are_routed_by_page_business_scope() -> None:
    class VehicleLicenseClient(FakeQwenClient):
        async def extract_document(
            self, image: dict[str, object], *, business=None, retry_reason=None, focus_boxes=None
        ) -> QwenExtraction:
            return QwenExtraction(
                document_type="vehicle_license",
                fields={"vehicle.vin": f"VIN-{image['index']}"},
                confidence=0.9,
            )

    old_image = ImageInput(
        index=3,
        src="https://images.example.test/synthetic.jpg",
        image_id="old_vehicle-01",
        business_scope="old_vehicle",
        group_title="报废车辆资料",
        group_order=1,
    )
    new_image = ImageInput(
        index=6,
        src="https://images.example.test/synthetic.jpg",
        image_id="new_vehicle-01",
        business_scope="new_vehicle",
        group_title="新车资料",
        group_order=1,
    )

    result = await RecognitionService(
        VehicleLicenseClient(
            classification=QwenExtraction(document_type="vehicle_license", confidence=0.98),
        )
    ).extract_async([old_image, new_image], business=TEST_BUSINESS)

    assert [(item.field, item.value, item.business_scope) for item in result.observations] == [
        ("old_vehicle.vin", "VIN-3", "old_vehicle"),
        ("new_vehicle.vin", "VIN-6", "new_vehicle"),
    ]


@pytest.mark.asyncio
async def test_registration_certificate_fields_follow_page_business_scope() -> None:
    class RegistrationCertificateClient(FakeQwenClient):
        async def extract_document(
            self, image: dict[str, object], *, business=None, retry_reason=None, focus_boxes=None
        ) -> QwenExtraction:
            return QwenExtraction(
                document_type="registration_certificate",
                fields={
                    "vehicle.owner": f"OWNER-{image['index']}",
                    "vehicle.vin": f"VIN-{image['index']}",
                    "vehicle.engine_model": f"ENGINE-{image['index']}",
                },
                confidence=0.9,
            )

    result = await RecognitionService(
        RegistrationCertificateClient(
            classification=QwenExtraction(
                document_type="registration_certificate", confidence=0.98
            ),
        )
    ).extract_async(
        [
            image(index=3, business_scope="old_vehicle", group_order=2),
            image(index=6, business_scope="new_vehicle", group_order=2),
        ],
        business=TEST_BUSINESS,
    )

    observations = {
        (item.field, item.value, item.business_scope, item.document_type)
        for item in result.observations
    }
    assert observations == {
        ("old_vehicle.vin", "VIN-3", "old_vehicle", "registration_certificate"),
        (
            "old_vehicle.engine_model",
            "ENGINE-3",
            "old_vehicle",
            "registration_certificate",
        ),
        ("new_vehicle.vin", "VIN-6", "new_vehicle", "registration_certificate"),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("page_value", [[1, 2], "第1页、第2页", "第 1 页 / 第 2 页"])
async def test_registration_page_numbers_are_normalized_for_scrap_documents(page_value) -> None:
    class RegistrationPagesClient(FakeQwenClient):
        async def extract_document(
            self, image: dict[str, object], *, business=None, retry_reason=None, focus_boxes=None
        ) -> QwenExtraction:
            return QwenExtraction(
                document_type="registration_certificate",
                fields={"registration.covered_pages": page_value},
                confidence=0.9,
            )

    result = await RecognitionService(RegistrationPagesClient()).extract_async(
        [image(category_hint="registration_certificate", business_scope="old_vehicle")],
        business=TEST_BUSINESS,
    )

    assert result.recognized_documents[0].covered_pages == [1, 2]


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_deadline_returns_completed_observations_and_marks_pending_timeout() -> None:
    class DelayClient(FakeQwenClient):
        async def extract_document(
            self, image: dict[str, object], *, business=None, retry_reason=None, focus_boxes=None
        ) -> QwenExtraction:
            if int(image["index"]) == 1:
                await asyncio.sleep(1)
            return QwenExtraction(
                document_type="invoice",
                fields={"invoice.invoice_no": str(image["index"])},
            )

    progress: list[tuple[int, int]] = []
    result = await RecognitionService(
        DelayClient(),
        image_timeout=2,
        review_deadline=0.05,
    ).extract_async(
        [image(0, "invoice"), image(1, "invoice")],
        on_progress=lambda snapshot: progress.append(
            (snapshot.completed_count, snapshot.timed_out_count)
        ),
        business=TEST_BUSINESS,
    )

    assert {(item.field, item.value) for item in result.observations} == {
        ("invoice.invoice_no", "0"),
        ("invoice.code", "0"),
    }
    assert result.completed_count == 1
    assert result.timed_out_count == 1
    assert progress


def _hinted_image() -> ImageInput:
    return ImageInput(
        index=0,
        image_id="invoice-0",
        src="https://example.test/0.jpg",
        business_scope="new_vehicle",
        category_hint="invoice",
    )


@pytest.mark.asyncio
async def test_content_type_overrides_hint_within_allowed_materials() -> None:
    client = FakeQwenClient(
        classification=QwenExtraction(document_type="vehicle_license", confidence=0.9),
        extraction=QwenExtraction(
            document_type="vehicle_license", fields={"vehicle.vin": "SYNTHETIC"}, confidence=0.9
        ),
    )
    result = await RecognitionService(client).extract_async(
        [_hinted_image()],
        allowed_document_types=frozenset({"vehicle_license"}),
        business=TEST_BUSINESS,
    )
    assert client.classification_calls == []
    assert client.extraction_calls == [(0, "vehicle_license")]
    assert result.completed_count == 1
    assert result.failed_count == 0


@pytest.mark.asyncio
async def test_configured_hint_can_extract_without_an_extra_candidate_filter() -> None:
    client = FakeQwenClient(
        extraction=QwenExtraction(
            document_type="invoice",
            fields={"invoice.invoice_no": "123"},
            confidence=0.9,
        ),
    )

    result = await RecognitionService(client).extract_async(
        [_hinted_image()], business=TEST_BUSINESS
    )

    assert client.classification_calls == []
    assert client.extraction_calls == [(0, "invoice")]
    assert result.completed_count == 1


@pytest.mark.asyncio
async def test_cancelled_batch_waits_for_all_image_cleanup():
    started, cleaned = asyncio.Event(), asyncio.Event()

    class PendingClient:
        async def extract_document(
            self, image, *, business=None, retry_reason=None, focus_boxes=None
        ):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

    service = RecognitionService(PendingClient())
    task = asyncio.create_task(
        service.extract_async([image(category_hint="identity_card")], business=TEST_BUSINESS)
    )
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_unexpected_image_error_is_propagated_and_cancels_siblings():
    started, cleaned = asyncio.Event(), asyncio.Event()

    class BrokenClient:
        async def extract_document(
            self, image, *, business=None, retry_reason=None, focus_boxes=None
        ):
            if image["index"] == 0:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()
            else:
                await started.wait()
                raise AttributeError("broken client contract")

    service = RecognitionService(BrokenClient())
    with pytest.raises(AttributeError, match="broken client contract"):
        await service.extract_async(
            [image(0, "identity_card"), image(1, "identity_card")], business=TEST_BUSINESS
        )
    assert cleaned.is_set()
