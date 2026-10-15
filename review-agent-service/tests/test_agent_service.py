import asyncio
import logging

import httpx
import pytest

from app.models.review import BusinessType, ImageInput
from app.workflow.config import QwenConfig
from app.workflow.models import QwenClassification, QwenExtraction
from app.workflow.qwen_client import QwenClient
from app.workflow.service import AgentService, declared_material_types


class FakeQwenClient:
    def __init__(
        self,
        classification: QwenClassification | None = None,
        extraction: QwenExtraction | None = None,
        fail_indices: set[int] | None = None,
    ) -> None:
        self.classification = classification
        self.extraction = extraction
        self.fail_indices = fail_indices or set()
        self.classification_calls: list[int] = []
        self.extraction_calls: list[tuple[int, str]] = []

    async def classify_document(self, image: dict[str, object]) -> QwenClassification:
        self.classification_calls.append(int(image["index"]))
        if self.classification is None:
            raise RuntimeError("缺少分类测试结果")
        return self.classification

    async def extract_fields(
        self, image: dict[str, object], policy: object
    ) -> QwenExtraction:
        image_index = int(image["index"])
        document_type = str(policy.document_type)
        self.extraction_calls.append((image_index, document_type))
        if image_index in self.fail_indices:
            raise RuntimeError("Qwen 调用失败")
        if self.extraction is None:
            raise RuntimeError("缺少提取测试结果")
        return self.extraction


def extracted_values(service, images):
    """业务提取测试统一走生产使用的异步接口，值投影仅服务于断言。"""
    batch = asyncio.run(service.extract_async(images))
    recognized = {item.field: (str(item.value), item.image_index) for item in batch.observations if item.value}
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
    if business_scope is not None:
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

    recognized, limitations, confidences = extracted_values(AgentService(fake),
        [image(category_hint="id_card")]
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

    _, limitations, _ = extracted_values(AgentService(fake), [identity_card])

    assert limitations == ["图片 0 内容不可用"]


def test_known_invoice_uses_one_extraction_call() -> None:
    fake = FakeQwenClient(
        extraction=QwenExtraction(
            document_type="invoice",
            fields={"invoice.invoice_no": "123"},
            confidence=0.9,
        )
    )

    recognized, limitations, confidences = extracted_values(AgentService(fake),
        [image(category_hint="invoice")]
    )

    assert recognized == {
        "invoice.invoice_no": ("123", 0),
        "invoice.code": ("123", 0),
    }
    assert limitations == []
    assert confidences == [0.9]
    assert fake.classification_calls == []
    assert fake.extraction_calls == [(0, "invoice")]


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

    result = await AgentService(fake).extract_async(
        [image(index=3, category_hint="invoice", business_scope="new_vehicle")]
    )

    observations = {item.field: item for item in result.observations}
    assert observations["invoice.invoice_no"].derived_from is None
    assert observations["invoice.code"].derived_from == "invoice.invoice_no"
    assert observations["invoice.invoice_no"].evidence_region == [10, 20, 300, 80]
    assert observations["invoice.code"].evidence_region == [10, 20, 300, 80]


def test_unknown_image_is_classified_then_extracted() -> None:
    fake = FakeQwenClient(
        classification=QwenClassification(document_type="invoice", confidence=0.98),
        extraction=QwenExtraction(
            document_type="invoice",
            fields={"invoice.invoice_no": "123"},
            confidence=0.9,
        ),
    )

    recognized, limitations, _ = extracted_values(AgentService(fake),
        [image(business_scope="new_vehicle")]
    )

    assert recognized == {"invoice.invoice_no": ("123", 0), "invoice.code": ("123", 0)}
    assert limitations == []
    assert fake.classification_calls == [0]
    assert fake.extraction_calls == [(0, "invoice")]


def test_unreliable_classification_does_not_extract_fields() -> None:
    classifications = (
        QwenClassification(document_type="invoice", confidence=0.69),
        QwenClassification(document_type="unsupported", confidence=0.99),
    )

    for classification in classifications:
        fake = FakeQwenClient(classification=classification)
        recognized, limitations, _ = extracted_values(AgentService(fake), [image()])

        assert recognized == {}
        assert limitations
        assert fake.extraction_calls == []


def test_mismatched_extraction_type_discards_fields() -> None:
    fake = FakeQwenClient(
        extraction=QwenExtraction(
            document_type="old_vehicle",
            fields={"invoice.code": "123"},
        )
    )

    recognized, limitations, _ = extracted_values(AgentService(fake),
        [image(category_hint="invoice")]
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

    recognized, _, _ = extracted_values(AgentService(fake), [image(category_hint="invoice")])

    assert recognized == {"invoice.invoice_no": ("123", 0), "invoice.code": ("123", 0)}


def test_classified_unknown_image_keeps_resolved_policy_allowlist() -> None:
    fake = FakeQwenClient(
        classification=QwenClassification(document_type="invoice", confidence=0.98),
        extraction=QwenExtraction(
            document_type="invoice",
            fields={"invoice.invoice_no": "123", "old_vehicle.engine_model": "OUT-OF-SCOPE"},
        ),
    )

    recognized, _, _ = extracted_values(AgentService(fake),
        [image(category_hint="unknown", business_scope="new_vehicle")]
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

    recognized, limitations, _ = extracted_values(AgentService(fake),
        [image(0, "invoice"), image(1, "invoice")]
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
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        extracted_values(AgentService(fake), [image(category_hint="invoice")])

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
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        extracted_values(AgentService(fake), [image(category_hint="invoice")])

    assert "uncertain_field_count=0 uncertain_fields=-" in caplog.text


def test_qwen_failure_is_sanitized_and_logged(caplog: object) -> None:
    class SensitiveFailureClient(FakeQwenClient):
        async def extract_fields(
            self, image: dict[str, object], policy: object
        ) -> QwenExtraction:
            raise RuntimeError("HTTP 400 raw model response VIN=SHOULD-NOT-LEAK")

    with caplog.at_level(logging.WARNING, logger="app.workflow.service"):
        _, limitations, _ = extracted_values(AgentService(SensitiveFailureClient()),
            [image(category_hint="invoice")]
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

    result = await AgentService(client).extract_async(
        [image(business_scope="new_vehicle")]
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

        async def extract_fields(
            self, image: dict[str, object], policy: object
        ) -> QwenExtraction:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active == 6:
                self.all_started.set()
            await self.release.wait()
            self.active -= 1
            return QwenExtraction(
                document_type="invoice", fields={"invoice.code": "123"}
            )

    client = BlockingClient()
    service = AgentService(client, image_timeout=1, review_deadline=2)
    task = asyncio.create_task(
        service.extract_async([image(index, "invoice") for index in range(6)])
    )
    await asyncio.wait_for(client.all_started.wait(), timeout=0.5)

    assert client.max_active == 6
    client.release.set()
    result = await task
    assert result.completed_count == 6


@pytest.mark.asyncio
async def test_unknown_image_is_classified_then_uses_targeted_extraction() -> None:
    client = FakeQwenClient(
        classification=QwenClassification(document_type="invoice", confidence=0.98),
        extraction=QwenExtraction(
            document_type="invoice",
            fields={
                "invoice.invoice_no": "123",
                "old_vehicle.engine_model": "OUT-OF-SCOPE",
            },
            confidence=0.9,
        ),
    )
    result = await AgentService(client).extract_async(
        [image(category_hint="unknown", business_scope="new_vehicle")]
    )

    assert client.classification_calls == [0]
    assert client.extraction_calls == [(0, "invoice")]
    assert [(item.field, item.value) for item in result.observations] == [
        ("invoice.invoice_no", "123"),
        ("invoice.code", "123"),
    ]


@pytest.mark.asyncio
async def test_business_scope_rejects_incompatible_model_document_type() -> None:
    class CombinedClient(FakeQwenClient):
        async def extract_fields(
            self, image: dict[str, object], policy: object
        ) -> QwenExtraction:
            return QwenExtraction(
                document_type="invoice",
                fields={
                    "invoice.code": "123",
                    "old_vehicle.vin": "MUST-NOT-BE-COMPARED-AS-OLD-VEHICLE",
                },
                confidence=0.9,
            )

    result = await AgentService(CombinedClient()).extract_async(
        [image(category_hint="scrap_certificate")]
    )

    assert result.observations == []
    assert any("资料类型不一致" in item for item in result.limitations)


@pytest.mark.asyncio
async def test_identical_vehicle_documents_are_routed_by_page_business_scope() -> None:
    class VehicleLicenseClient(FakeQwenClient):
        async def extract_fields(
            self, image: dict[str, object], policy: object
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

    result = await AgentService(VehicleLicenseClient()).extract_async(
        [old_image, new_image]
    )

    assert [
        (item.field, item.value, item.business_scope) for item in result.observations
    ] == [
        ("old_vehicle.vin", "VIN-3", "old_vehicle"),
        ("new_vehicle.vin", "VIN-6", "new_vehicle"),
    ]


@pytest.mark.asyncio
async def test_registration_certificate_fields_follow_page_business_scope() -> None:
    class RegistrationCertificateClient(FakeQwenClient):
        async def extract_fields(
            self, image: dict[str, object], policy: object
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

    result = await AgentService(RegistrationCertificateClient()).extract_async(
        [
            image(index=3, business_scope="old_vehicle", group_order=2),
            image(index=6, business_scope="new_vehicle", group_order=2),
        ]
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
        async def extract_fields(
            self, image: dict[str, object], policy: object
        ) -> QwenExtraction:
            return QwenExtraction(
                document_type="registration_certificate",
                fields={"registration.covered_pages": page_value},
                confidence=0.9,
            )

    result = await AgentService(RegistrationPagesClient()).extract_async(
        [image(category_hint="registration_certificate", business_scope="old_vehicle")]
    )

    assert result.recognized_documents[0].covered_pages == [1, 2]


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_deadline_returns_completed_observations_and_marks_pending_timeout() -> (
    None
):
    class DelayClient(FakeQwenClient):
        async def extract_fields(
            self, image: dict[str, object], policy: object
        ) -> QwenExtraction:
            if int(image["index"]) == 1:
                await asyncio.sleep(1)
            return QwenExtraction(
                document_type="invoice",
                fields={"invoice.invoice_no": str(image["index"])},
            )

    progress: list[tuple[int, int]] = []
    result = await AgentService(
        DelayClient(),
        image_timeout=2,
        review_deadline=0.05,
    ).extract_async(
        [image(0, "invoice"), image(1, "invoice")],
        on_progress=lambda snapshot: progress.append(
            (snapshot.completed_count, snapshot.timed_out_count)
        ),
    )

    assert [(item.field, item.value) for item in result.observations] == [
        ("invoice.invoice_no", "0"),
        ("invoice.code", "0"),
    ]
    assert result.completed_count == 1
    assert result.timed_out_count == 1
    assert progress


def _transfer_invoice_image() -> ImageInput:
    return ImageInput(
        index=0,
        image_id="invoice-0",
        src="https://example.test/0.jpg",
        business_scope="transfer",
        category_hint="invoice",
    )


@pytest.mark.asyncio
async def test_transfer_invoice_hint_does_not_borrow_scrap_invoice_policy() -> None:
    """过户页面的「二手车发票」不得套用报废置换的机动车销售发票政策。

    前端把含"发票"的小标题判成 invoice，而跨业务合并的全局政策表里 invoice
    属于报废置换。限制候选到本业务声明的材料后，这张图必须改走"先分类、
    再按 used_car_invoice 提取"，而不是直接拿报废的白名单提取。
    """
    client = FakeQwenClient(
        classification=QwenClassification(
            document_type="used_car_invoice", confidence=0.9
        ),
        extraction=QwenExtraction(
            document_type="used_car_invoice",
            fields={"transfer.invoice_no": "2637200000467264218"},
            confidence=0.9,
        ),
    )

    result = await AgentService(client).extract_async(
        [_transfer_invoice_image()],
        allowed_document_types=declared_material_types(BusinessType.TRANSFER),
    )

    assert client.classification_calls == [0]
    assert client.extraction_calls == [(0, "used_car_invoice")]
    assert result.completed_count == 1
    assert result.failed_count == 0


@pytest.mark.asyncio
async def test_lookup_without_allowed_types_keeps_legacy_hint_path() -> None:
    """对照组：不传候选集合时保持原行为，直接按提示类型提取。

    尚未声明扩展包的业务没有材料名单，过滤不能把它们一起收紧成两阶段。
    """
    client = FakeQwenClient(
        extraction=QwenExtraction(
            document_type="invoice",
            fields={"invoice.invoice_no": "123"},
            confidence=0.9,
        ),
    )

    result = await AgentService(client).extract_async([_transfer_invoice_image()])

    assert client.classification_calls == []
    assert client.extraction_calls == [(0, "invoice")]
    assert result.completed_count == 1
