"""Page declarations must work through the real engine, without editing its tables."""

import asyncio
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from forj_review_service.bootstrap.application import create_app
from forj_review_service.businesses.context import BusinessContext
from forj_review_service.businesses.context_validation import (
    BusinessContextMismatch,
    validate_request_route,
)
from forj_review_service.businesses.field_policies import field_policy
from forj_review_service.businesses.packs import build_collect_manifest
from forj_review_service.businesses.page_config.compiler import compile_page
from forj_review_service.businesses.page_config.loader import load_page_profiles
from forj_review_service.businesses.page_config.schema import PageDefinition
from forj_review_service.businesses.registry import BusinessProfileNotFound, BusinessRegistry
from forj_review_service.businesses.routing import route_fields
from forj_review_service.contracts.recognition import (
    QwenExtraction,
    RecognitionBatchResult,
    RecognizedDocument,
)
from forj_review_service.contracts.review import (
    FieldObservation,
    ImageInput,
    ReviewFieldSnapshot,
    ReviewRequest,
)
from forj_review_service.integrations.recognition.extraction import RecognitionService
from forj_review_service.runtime.review_service import ReviewService
from tests.support.pages import SCRAP_BUSINESS as TEST_BUSINESS


@pytest.fixture
def declaration():
    return {
        "schema_version": "2.0",
        "settings": {
            "material_policy": {
                "mode": "enforce",
                "materials": [{"document_type": "permit", "business_scope": "vehicle"}],
            }
        },
        "page_id": "permit_page",
        "business_type": "permit_review",
        "region": "new_city",
        "title": "许可证审核",
        "paths": ["/permit-review"],
        "scopes": ["vehicle"],
        "page_groups": [{"label": "许可证资料", "scope": "vehicle", "title": "许可证资料"}],
        "materials": [
            {
                "document_type": "permit",
                "label": "许可证",
                "hints": ["许可证"],
                "scopes": ["vehicle"],
            }
        ],
        "fields": [
            {
                "key": "permit.vin",
                "label": "车辆识别代号",
                "aliases": ["车辆识别代号"],
                "normalizer": "vin",
                "evidence": [{"material": "permit", "field": "license.vin"}],
            }
        ],
    }


def _compile(data):
    return compile_page(PageDefinition.model_validate(data))


def test_one_field_generates_collection_extraction_and_mapping(declaration):
    profile = _compile(declaration)
    manifest = build_collect_manifest(profile.pack_declaration)
    assert manifest["fields"][0]["aliases"] == ["车辆识别代号"]
    business = BusinessContext.from_profile(profile)
    assert business.document_policies["permit"].fields_for_scope("vehicle") == ("license.vin",)
    assert route_fields("vehicle", "permit", {"license.vin": "VIN-1"}, pack=business.pack) == (
        {"permit.vin": "VIN-1"},
        None,
    )
    assert field_policy("permit.vin", business=business).normalizer == "vin"
    assert (
        route_fields(
            "old_vehicle",
            "business_license",
            {"business_license.company_name": "X"},
            pack=business.pack,
        )[0]
        == {}
    )
    assert field_policy("permit.vin", business=TEST_BUSINESS) is None


@pytest.mark.parametrize("locator", [{}, {"locator_id": "vin"}, {"locator_name": "vin"}])
def test_page_fields_require_collectable_bindings(declaration, locator):
    data = {**declaration, "fields": [{**declaration["fields"][0], "aliases": [], **locator}]}
    with pytest.raises(ValueError, match="permit.vin.*aliases"):
        _compile(data)


def test_material_only_field_does_not_require_a_page_alias(declaration):
    data = {
        **declaration,
        "fields": [{**declaration["fields"][0], "aliases": [], "page_evidence_required": False}],
    }
    assert _compile(data).required_fields == ("permit.vin",)


@pytest.mark.asyncio
@pytest.mark.parametrize("page_field", ["none", "present", "missing"])
async def test_material_only_field_can_pass_without_a_page_value(declaration, page_field):
    data = {
        **declaration,
        "fields": [{**declaration["fields"][0], "aliases": [], "page_evidence_required": False}],
    }
    if page_field != "none":
        data["fields"].append(
            {"key": "permit.note", "label": "Note", "aliases": ["Note"], "mode": "SYSTEM"}
        )
    profile = _compile(data)
    if page_field == "present":
        profile = replace(profile, page_interaction=True)
    service = ReviewService(registry=BusinessRegistry((profile,)))
    request = ReviewRequest(
        page_id=profile.page_id,
        business_type=profile.business_type,
        region=profile.region,
        page_url="https://example.test/permit-review",
        page_fields={"permit.note": "Synthetic note"} if page_field == "present" else {},
        review_fields=[
            ReviewFieldSnapshot(
                field="permit.note",
                label="Note",
                value="Synthetic note",
                order=1,
                control_type="text",
                editable=True,
            )
        ]
        if page_field == "present"
        else [],
        images=[
            ImageInput(
                index=0,
                src="https://example.test/synthetic.jpg",
                business_scope="vehicle",
                document_type_hint="permit",
            )
        ],
    )
    batch = RecognitionBatchResult(
        total_count=1,
        completed_count=1,
        recognized_documents=[
            RecognizedDocument(target_id="0", document_type="permit", business_scope="vehicle")
        ],
        observations=[
            FieldObservation(
                field="permit.vin",
                value="SYNTHETIC",
                source_type="image",
                source_id="0",
                image_index=0,
                document_type="permit",
                business_scope="vehicle",
            )
        ],
    )
    response, _ = await service.assist_with_batch_async(request, initial_batch=batch)
    assert response.recommendation == ("REVIEW_REQUIRED" if page_field == "missing" else "PASS")
    task = next((item for item in response.review_tasks if item.step_id == "FIELD-permit.vin"))
    assert task.result_status == "MATCH"
    assert not task.writable
    assert task.evidence_count == 1
    assert not task.not_found_sources
    if page_field == "missing":
        assert "未采集到右侧申请字段" in response.issues
    else:
        assert not response.issues


def test_authorable_normalizers_match_the_runtime_registry():
    from typing import get_args

    from forj_review_service.businesses.page_config.schema import Normalizer
    from forj_review_service.evidence.fields.normalize import DECLARED_NORMALIZERS

    assert set(get_args(Normalizer)) == {"default", *DECLARED_NORMALIZERS}


@pytest.mark.parametrize(
    "change, message",
    [
        ({"unknown_property": True}, "Extra inputs"),
        ({"paths": ["/permit-review?x=1"]}, "paths"),
        ({"fields": [{"key": "permit.vin", "normalizer": "guess"}]}, "normalizer"),
        ({"business_type": "../../code"}, "pattern"),
        ({"active": "false"}, "boolean"),
        (
            {"fields": [{"key": "permit.vin", "label": "VIN", "javascript": "run()"}]},
            "Extra inputs",
        ),
    ],
)
def test_invalid_authoring_data_is_rejected(declaration, change, message):
    with pytest.raises(ValidationError, match=message):
        PageDefinition.model_validate({**declaration, **change})


def test_missing_material_and_unknown_rules_are_rejected(declaration):
    with pytest.raises(ValueError, match="未声明的材料"):
        _compile({**declaration, "materials": []})
    with pytest.raises(ValueError, match="未注册"):
        _compile(
            {
                **declaration,
                "settings": {
                    "capability_specs": [{"capability_id": "invented_rule", "kind": "RULE"}]
                },
            }
        )
    with pytest.raises(ValueError, match="生产者"):
        _compile(
            {**declaration, "fields": [{**declaration["fields"][0], "checks": ["invented-check"]}]}
        )


def test_collection_manifest_does_not_contain_backend_decision_policy(declaration):
    manifest = build_collect_manifest(_compile(declaration).pack_declaration)
    assert (
        not {"evidence", "sources", "mode", "required", "normalizer", "authority", "checks"}
        & manifest["fields"][0].keys()
    )
    assert "required" not in manifest["materials"][0]


def test_loader_pins_versions_and_has_explicit_active_version(tmp_path, declaration):
    old = {**declaration, "active": False}
    new = {
        **declaration,
        "version": "2.0",
        "fields": [{**declaration["fields"][0], "label": "新标签"}],
    }
    (tmp_path / "z-old.json").write_text(json.dumps(old), encoding="utf-8")
    (tmp_path / "a-new.json").write_text(json.dumps(new), encoding="utf-8")
    pages = load_page_profiles(tmp_path)
    registry = BusinessRegistry(pages.profiles, active_profiles=pages.active)
    assert len(registry.profiles) == 2
    assert registry.active_profiles[0].version == "2.0"
    assert registry.resolve("permit_review", "new_city", "1.0", "permit_page").version == "1.0"


def test_loader_rejects_duplicate_active_versions_and_json_keys(tmp_path, declaration):
    (tmp_path / "one.json").write_text(json.dumps(declaration), encoding="utf-8")
    (tmp_path / "two.json").write_text(
        json.dumps({**declaration, "version": "2.0"}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="active=true"):
        load_page_profiles(tmp_path)
    (tmp_path / "two.json").write_text('{"page_id":"a", "page_id":"b"}', encoding="utf-8")
    with pytest.raises(ValueError, match="属性重复"):
        load_page_profiles(tmp_path)


def test_region_is_versioned_while_business_identity_is_stable(declaration):
    from dataclasses import replace

    first = _compile(declaration)
    second = _compile({**declaration, "version": "2.0", "region": "another_city"})
    registry = BusinessRegistry((first, second), active_profiles=(second,))
    assert registry.resolve(second.business_type, "another_city", "2.0", second.page_id) is second
    assert registry.resolve(first.business_type, first.region, "1.0", first.page_id) is first
    with pytest.raises(ValueError, match="更换业务"):
        BusinessRegistry((first, replace(second, business_type="different_business")))


def test_registry_rejects_active_profiles_outside_its_snapshot(declaration):
    first = _compile(declaration)
    other = _compile({**declaration, "version": "2.0"})
    with pytest.raises(ValueError, match="当前注册表快照"):
        BusinessRegistry((first,), active_profiles=(other,))
    with pytest.raises(ValueError, match="一个活动版本"):
        BusinessRegistry((first, other), active_profiles=(first, other))


def test_http_catalog_and_collection_use_the_injected_registry(declaration):
    profile = _compile(declaration)
    service = ReviewService(registry=BusinessRegistry((profile,)))
    with TestClient(create_app(review_service=service)) as client:
        catalog = client.get("/api/review/page-catalog").json()["identities"]
        assert len(catalog) == 1 and catalog[0]["page_id"] == "permit_page"
        response = client.get(
            "/api/review/collect-manifest",
            params={
                "business_type": "permit_review",
                "region": "new_city",
                "profile_version": "1.0",
                "page_id": "permit_page",
            },
        )
        assert response.status_code == 200
        assert response.json()["fields"][0]["key"] == "permit.vin"
        assert response.json()["page_id"] == "permit_page"
        assert (
            client.get("/api/review/configuration/schema").json()["additionalProperties"] is False
        )
        rules = client.get("/api/review/configuration/rules").json()["rules"]
        assert any(
            (item["id"] == "owner_consistency" and item["required_fields"] for item in rules)
        )


def test_collection_refuses_ambiguous_pages(declaration):
    first = _compile(declaration)
    second = _compile({**declaration, "page_id": "permit_other", "paths": ["/other"]})
    service = ReviewService(registry=BusinessRegistry((first, second)))
    with TestClient(create_app(review_service=service)) as client:
        assert (
            client.get(
                "/api/review/collect-manifest", params={"business_type": "permit_review"}
            ).status_code
            == 409
        )
    with pytest.raises(BusinessProfileNotFound, match="page_id"):
        service.registry.resolve("permit_review", "new_city", "1.0")


def test_request_route_rejects_another_page_in_the_same_business(declaration):
    first = _compile(declaration)
    second = _compile({**declaration, "page_id": "permit_other", "paths": ["/other"]})
    request = ReviewRequest(
        page_id="permit_other",
        business_type="permit_review",
        region="new_city",
        page_url="https://admin.forjtruck.com/permit-review",
    )
    with pytest.raises(BusinessContextMismatch, match="page_id"):
        validate_request_route(request, (first, second), business=TEST_BUSINESS)
    validate_request_route(
        request.model_copy(update={"page_id": "permit_page"}),
        (first, second),
        business=TEST_BUSINESS,
    )


@pytest.mark.asyncio
async def test_new_business_runs_existing_review_graph(declaration):
    profile = _compile(declaration)
    service = ReviewService(registry=BusinessRegistry((profile,)))
    request = ReviewRequest(
        page_id=profile.page_id,
        business_type="permit_review",
        region="new_city",
        page_url="https://admin.forjtruck.com/permit-review",
        page_fields={"permit.vin": "abc123"},
        images=[
            ImageInput(
                index=0,
                src="https://example.test/synthetic.jpg",
                business_scope="vehicle",
                document_type_hint="permit",
            )
        ],
    )
    batch = RecognitionBatchResult(
        total_count=1,
        completed_count=1,
        confidences=[0.99],
        recognized_documents=[
            RecognizedDocument(target_id="0", document_type="permit", business_scope="vehicle")
        ],
        observations=[
            FieldObservation(
                field="permit.vin",
                value="ABC123",
                source_type="image",
                source_id="0",
                image_index=0,
                document_type="permit",
                business_scope="vehicle",
            )
        ],
    )
    response, _ = await service.assist_with_batch_async(request, initial_batch=batch)
    assert response.business_type == "permit_review"
    assert response.comparisons[0].status == "MATCH"
    assert response.recommendation == "PASS"
    assert any((task.category == "FIELD" for task in response.review_tasks))


@pytest.mark.asyncio
async def test_same_field_and_material_are_isolated_across_concurrent_pages(declaration):
    first = _compile(declaration)
    changed = {
        **declaration,
        "page_id": "permit_other",
        "fields": [
            {
                **declaration["fields"][0],
                "normalizer": "text",
                "evidence": [{"material": "permit", "field": "another.vin"}],
            }
        ],
    }
    second = _compile(changed)

    async def inspect(profile):
        business = BusinessContext.from_profile(profile)
        await asyncio.sleep(0)
        return (
            field_policy("permit.vin", business=business).normalizer,
            business.document_policies["permit"].fields,
        )

    assert await asyncio.gather(inspect(first), inspect(second)) == [
        ("vin", ("license.vin",)),
        ("text", ("another.vin",)),
    ]


@pytest.mark.asyncio
async def test_custom_material_uses_generated_extraction_allowlist(declaration):
    profile = _compile(declaration)

    class Client:
        async def extract_fields(self, image, policy, *, retry_reason=None):
            assert policy.fields == ("license.vin",)
            return QwenExtraction(
                document_type="permit", fields={"license.vin": "VIN1"}, confidence=0.99
            )

    business = BusinessContext.from_profile(profile)
    batch = await RecognitionService(Client()).extract_async(
        [
            ImageInput(
                index=0,
                src="https://example.test/synthetic.jpg",
                business_scope="vehicle",
                document_type_hint="permit",
            )
        ],
        allowed_document_types=frozenset({"permit"}),
        business=business,
    )
    assert batch.observations[0].field == "permit.vin"
    assert batch.observations[0].value == "VIN1"


def test_new_configuration_cannot_disable_uncertain_evidence_protection(declaration):
    declaration["fields"][0]["uncertain_requires_review"] = False
    with pytest.raises(ValidationError, match="uncertain_requires_review"):
        _compile(declaration)


@pytest.mark.parametrize(
    "material_value, uncertain, expected",
    [
        ("VIN-A", False, "MATCH"),
        ("VIN-B", False, "CONFLICT"),
        ("VIN-B", True, "REVIEW_REQUIRED"),
        ("VIN-A", True, "REVIEW_REQUIRED"),
    ],
)
def test_configured_page_uses_the_shared_decision_boundary(
    declaration, material_value, uncertain, expected
):
    from forj_review_service.evidence.comparison.review import build_field_comparisons

    profile = _compile(declaration)
    request = ReviewRequest(
        page_url="https://example.test/permit-review",
        business_type="permit_review",
        region="new_city",
        page_fields={"permit.vin": "VIN-A"},
    )
    observations = [
        FieldObservation(field="permit.vin", source_type="page", source_id="page", value="VIN-A"),
        FieldObservation(
            field="permit.vin",
            source_type="image",
            source_id="permit1",
            document_type="permit",
            value=material_value,
            uncertain=uncertain,
        ),
    ]
    result = build_field_comparisons(request, profile, observations)
    assert result[0].status == expected
    if uncertain:
        assert result[0].reason_codes == ["RECOGNITION_UNCERTAIN"]


def test_ambiguous_page_control_cannot_prove_conflict(declaration):
    from forj_review_service.evidence.comparison.review import build_field_comparisons

    profile = _compile(declaration)
    request = ReviewRequest(
        page_url="https://example.test/permit-review",
        business_type="permit_review",
        region="new_city",
        page_fields={"permit.vin": "WRONG"},
        collection_diagnostics={"ambiguous_fields": ["permit.vin"]},
    )
    observations = [
        FieldObservation(field="permit.vin", source_type="page", source_id="page", value="WRONG"),
        FieldObservation(
            field="permit.vin",
            source_type="image",
            source_id="permit1",
            document_type="permit",
            value="VIN-A",
        ),
    ]
    result = build_field_comparisons(request, profile, observations)
    assert result[0].status == "REVIEW_REQUIRED"
    assert result[0].reason_codes == ["PAGE_VALUE_MISSING"]
