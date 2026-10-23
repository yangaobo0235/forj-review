"""An existing page's field edit must reach every stage of the review pipeline."""

import pytest

from forj_review_service.businesses.context import BusinessContext
from forj_review_service.businesses.packs import build_collect_manifest
from forj_review_service.businesses.page_config.maintenance import edit_fields
from forj_review_service.businesses.page_config.state import ConfigurationState
from forj_review_service.contracts.recognition import QwenExtraction
from forj_review_service.contracts.review import ImageInput, ReviewRequest
from forj_review_service.integrations.recognition.extraction import RecognitionService
from forj_review_service.runtime.configuration.publication import rollback_definition, stage_version
from forj_review_service.runtime.configuration.service import validated_snapshot
from forj_review_service.runtime.review_service import ReviewService
from tests.support.pages import VEHICLE_SOURCE_DEFAULT, configuration_state, page_definition


@pytest.mark.asyncio
async def test_field_replacement_reaches_collection_extraction_comparison_and_tasks():
    state = configuration_state()
    draft = page_definition(VEHICLE_SOURCE_DEFAULT)
    draft["version"] = "fields.2"
    draft = edit_fields(
        draft,
        [
            {"op": "remove_field", "value": {"key": "vehicle.engine_no"}},
            {
                "op": "upsert_field",
                "value": {
                    "key": "vehicle.inspection_no",
                    "label": "检验编号",
                    "aliases": ["检验编号"],
                    "mode": "SINGLE_SOURCE",
                    "evidence": [
                        {
                            "material": "vehicle_license",
                            "field": "printed.inspection_no",
                            "scope": "vehicle",
                        }
                    ],
                },
            },
        ],
    )
    _, staged = stage_version(state, draft)
    _, registry = validated_snapshot(staged)
    profile = registry.resolve("vehicle_source", "default", "fields.2", draft["page_id"])
    business = BusinessContext.from_profile(profile)
    manifest = build_collect_manifest(profile.pack_declaration)
    keys = {f["key"] for f in manifest["fields"]}
    assert "vehicle.engine_no" not in keys and "vehicle.inspection_no" in keys

    class Extractor:
        async def extract_document(
            self, image, *, business=None, retry_reason=None, focus_boxes=None
        ):
            policy = business.document_policies["vehicle_license"]
            assert "vehicle.engine_no" not in policy.fields
            assert "printed.inspection_no" in policy.fields
            return QwenExtraction(
                document_type="vehicle_license",
                confidence=0.99,
                fields={
                    "vehicle.engine_no": "discarded",
                    "printed.inspection_no": "TEST123",
                },
            )

    image = ImageInput(
        index=1,
        src="https://example.test/license.jpg",
        business_scope="vehicle",
        document_type_hint="vehicle_license",
    )
    batch = await RecognitionService(Extractor()).extract_async([image], business=business)
    assert [(o.field, o.value) for o in batch.observations] == [
        ("vehicle.inspection_no", "TEST123")
    ]
    service = ReviewService(registry=registry, load_model=False)
    try:
        response, _ = await service.assist_with_batch_async(
            ReviewRequest(
                page_id=draft["page_id"],
                business_type="vehicle_source",
                profile_version="fields.2",
                page_url="https://example.test" + draft["paths"][0],
                images=[image],
                page_fields={"vehicle.inspection_no": "TEST123"},
                review_fields=[
                    {"field": "vehicle.inspection_no", "label": "检验编号", "value": "TEST123"}
                ],
            ),
            initial_batch=batch,
        )
    finally:
        await service.aclose()
    comparisons = {c.field: c for c in response.comparisons}
    assert comparisons["vehicle.inspection_no"].status == "MATCH"
    assert "vehicle.engine_no" not in comparisons
    tasks = {t.page_target_field or t.page_field: t for t in response.review_tasks}
    assert tasks["vehicle.inspection_no"].result_status == "MATCH"
    assert not tasks["vehicle.inspection_no"].writable
    assert "vehicle.engine_no" not in tasks

    _, restored = stage_version(
        staged, rollback_definition(staged, draft["page_id"], "1.0"), activation_only=True
    )
    assert restored.active == state.active
    assert restored.source(draft["page_id"], "fields.2") is not None
    assert state.source(draft["page_id"], "fields.2") is None


def test_missing_database_selection_cannot_restore_packaged_page_defaults():
    state = configuration_state()
    with pytest.raises(ValueError, match="发布页面选择"):
        validated_snapshot(ConfigurationState(state.definitions, {}))
    missing = tuple(d for d in state.definitions if d["page_id"] != VEHICLE_SOURCE_DEFAULT.page_id)
    with pytest.raises(ValueError, match="发布页面选择"):
        validated_snapshot(ConfigurationState(missing, state.active))


def test_empty_business_context_has_no_page_fields_materials_or_routes():
    from forj_review_service.businesses.routing import route_fields

    context = BusinessContext()
    assert not context.field_policies and not context.document_policies and not context.labels
    assert not context.composites and context.pack is None
    fields, issue = route_fields("vehicle", "vehicle_license", {"vehicle.vin": "TEST"})
    assert fields == {} and issue
