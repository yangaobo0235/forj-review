"""新版车源审核贯穿主图；模型输出固定，不访问模型或数据库。"""

import pytest

from forj_review_service.contracts.evidence import FieldObservation
from forj_review_service.contracts.recognition import RecognitionBatchResult, RecognizedDocument
from forj_review_service.contracts.review import ReviewFieldSnapshot, ReviewRequest
from forj_review_service.runtime.review_service import ReviewService
from tests.support.business_values import LICENSE_FIELDS
from tests.support.pages import business_registry


@pytest.mark.asyncio
@pytest.mark.parametrize("page_brand", ["解放牌MODEL100A", "错误牌WRONG100"])
async def test_source_workflow_keeps_only_nine_review_items_and_never_chooses_sales_version(
    monkeypatch,
    page_brand,
):
    values = {
        "vehicle.plate_no": "鲁A12345",
        "vehicle.vin": "LFV00000000000001",
        "vehicle.engine_no": "12345678",
        "vehicle.license_type": "重型半挂牵引车",
        "vehicle.registration_date": "2021-09-24",
        "vehicle.issue_date": "2025-08-19",
        "vehicle.owner": "合成示例公司",
        "vehicle.brand_model": page_brand,
        "vehicle.model": "原页面车型",
        "spec.max_hp": "300",
        "spec.drive_form": "6X2",
        "spec.engine_model": "WRONG42",
        "vehicle.usage": "营运",
        "vehicle.address": "无需核对的地址",
    }
    observations = [
        FieldObservation(
            field=f"vehicle.{name}",
            value=values[f"vehicle.{name}"],
            source_type="image",
            source_id="license",
            image_id="license",
            document_type="vehicle_license",
            business_scope="vehicle",
        )
        for name, *_ in LICENSE_FIELDS
    ]
    observations.extend(
        [
            FieldObservation(
                field="vehicle.brand_model",
                value="解放牌MODEL100A",
                source_type="image",
                source_id="license",
                image_id="license",
                document_type="vehicle_license",
                business_scope="vehicle",
            ),
            *[
                FieldObservation(
                    field=field,
                    value=value,
                    source_type="image",
                    source_id="plate",
                    image_id="plate",
                    document_type="vehicle_nameplate",
                    business_scope="vehicle",
                )
                for field, value in (
                    ("vehicle.announcement_model", "MODEL100A80"),
                    ("vehicle.engine_model", "ENGINE42-E6"),
                )
            ],
        ]
    )
    batch = RecognitionBatchResult(
        total_count=2,
        completed_count=2,
        observations=observations,
        recognized_documents=[
            RecognizedDocument(
                target_id=id, image_index=i, document_type=kind, business_scope="vehicle"
            )
            for i, (id, kind) in enumerate(
                [("license", "vehicle_license"), ("plate", "vehicle_nameplate")]
            )
        ],
    )
    service = ReviewService(load_model=False, registry=business_registry())

    async def extract(*_args, **_kwargs):
        return batch

    monkeypatch.setattr(service, "_extract_documents", extract)
    request = ReviewRequest(
        page_url="https://admin.forjtruck.com/consistency-qingdao/review/1",
        business_type="vehicle_source",
        workflow_stage="vehicle_source",
        region="default",
        page_fields=values,
        review_fields=[
            ReviewFieldSnapshot(
                field=field, label=field, value=value, order=i, editable=True, control_type="text"
            )
            for i, (field, value) in enumerate(values.items(), start=1)
        ],
        images=[
            {
                "index": i,
                "src": f"https://example.test/{id}.jpg",
                "imageId": id,
                "businessScope": "vehicle",
                "documentTypeHint": kind,
            }
            for i, (id, kind) in enumerate(
                [("license", "vehicle_license"), ("plate", "vehicle_nameplate")]
            )
        ],
    )
    result = await service.assist_async(request)
    fields = {t.page_target_field: t for t in result.review_tasks if t.category == "FIELD"}
    assert set(fields) == {
        *(f"vehicle.{name}" for name, *_ in LICENSE_FIELDS),
        "vehicle.brand_model",
        "vehicle.model",
    }
    assert all(fields[f"vehicle.{name}"].result_status == "MATCH" for name, *_ in LICENSE_FIELDS)
    assert fields["vehicle.brand_model"].details["permitted_fill_values"] == ["解放牌MODEL100A80"]
    completion = fields["vehicle.brand_model"].details["brand_model_completion"]
    assert completion["completed_value"] == "解放牌MODEL100A80"
    assert completion["status"] == "READY"
    assert completion["license_model"] == "MODEL100A"
    assert fields["vehicle.model"].result_status == "INSUFFICIENT"
    assert fields["vehicle.model"].requires_reviewer_action
    model_check = next(
        item for item in result.cross_checks if item.check_id == "VEHICLE-SOURCE-MODEL"
    )
    assert model_check.details["model_search"] == {
        "announcement_model": "MODEL100A80",
        "engine_model": "ENGINE42-E6",
        "issues": [],
    }
    assert {item.image_id for item in fields["vehicle.model"].evidence} == {"license", "plate"}
    assert result.material_completeness.presence.status == "COMPLETE"
    assert not result.page_actions
    assert result.recommendation == "REVIEW_REQUIRED"
