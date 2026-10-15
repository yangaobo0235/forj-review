"""Page declarations must work through the real engine, without editing its tables."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from forj_review_assistant.bootstrap.application import create_app
from forj_review_assistant.businesses.configuration.compiler import compile_page
from forj_review_assistant.businesses.configuration.loader import load_page_profiles
from forj_review_assistant.businesses.configuration.schema import PageDefinition
from forj_review_assistant.businesses.context import business_context
from forj_review_assistant.businesses.context_validation import (
    BusinessContextMismatch,
    validate_request_route,
)
from forj_review_assistant.businesses.field_policies import field_policy
from forj_review_assistant.businesses.materials import DOCUMENT_POLICIES
from forj_review_assistant.businesses.packs import build_collect_manifest
from forj_review_assistant.businesses.profiles import BUSINESS_PROFILES, VEHICLE_SOURCE_DEFAULT
from forj_review_assistant.businesses.registry import BusinessProfileNotFound, BusinessRegistry
from forj_review_assistant.businesses.routing import route_fields
from forj_review_assistant.contracts.recognition import (
    AgentBatchResult,
    QwenExtraction,
    RecognizedDocument,
)
from forj_review_assistant.contracts.review import FieldObservation, ImageInput, ReviewRequest
from forj_review_assistant.integrations.recognition.extraction import AgentService
from forj_review_assistant.runtime.review_service import ReviewService


@pytest.fixture
def declaration():
    return {
        "page_id": "permit_page",
        "business_type": "permit_review",
        "region": "new_city",
        "title": "许可证审核",
        "paths": ["/permit-review"],
        "scopes": ["vehicle"],
        "page_groups": [{"label": "许可证资料", "scope": "vehicle", "title": "许可证资料"}],
        "materials": [
            {"document_type": "permit", "label": "许可证", "hints": ["许可证"], "required": True}
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


def _patch(**changes):
    data = {
        "page_id": "vehicle_source:default",
        "business_type": "vehicle_source",
        "title": "车源审核",
        "paths": ["/vehicle-source"],
        "version": "2.0",
        "extends": {"page_id": "vehicle_source:default", "version": "1.0"},
    }
    data.update(changes)
    return compile_page(PageDefinition.model_validate(data), VEHICLE_SOURCE_DEFAULT)


def test_one_field_generates_collection_extraction_and_mapping(declaration):
    profile = _compile(declaration)
    manifest = build_collect_manifest(profile.pack_declaration)
    assert manifest["fields"][0]["aliases"] == ["车辆识别代号"]
    with business_context(profile):
        assert DOCUMENT_POLICIES["permit"].fields_for_scope("vehicle") == ("license.vin",)
        assert route_fields("vehicle", "permit", {"license.vin": "VIN-1"}) == (
            {"permit.vin": "VIN-1"},
            None,
        )
        assert field_policy("permit.vin").normalizer == "vin"
        # An unrelated built-in scope must never fall back to a different pack.
        assert (
            route_fields("old_vehicle", "business_license", {"business_license.company_name": "X"})[
                0
            ]
            == {}
        )
    assert field_policy("permit.vin") is None


def test_template_changes_are_partial_and_do_not_mutate_original():
    original = VEHICLE_SOURCE_DEFAULT.pack_declaration
    profile = _patch(fields=[{"key": "vehicle.issue_date", "max_age_days": 180}])
    field = next(
        item for item in profile.pack_declaration.fields if item.key == "vehicle.issue_date"
    )
    assert field.max_age_days == 180
    assert field.aliases == ("发证日期",)
    assert field.sources == ("vehicle_license",)
    assert next(item for item in original.fields if item.key == field.key).max_age_days == 90
    assert profile.version == "2.0"


def test_deleting_field_prunes_only_its_routes():
    profile = _patch(remove_fields=["vehicle.usage_nature"])
    assert "vehicle.usage_nature" not in {field.key for field in profile.pack_declaration.fields}
    assert all(
        "vehicle.usage_nature" not in route.targets for route in profile.pack_declaration.routes
    )
    assert any("vehicle.vin" in route.targets for route in profile.pack_declaration.routes)


def test_deleting_rule_input_requires_explicit_rule_change():
    with pytest.raises(ValueError, match="vehicle_model_consistency.*vehicle.model_code"):
        _patch(remove_fields=["vehicle.model_code"])
    profile = _patch(
        remove_fields=["vehicle.model_code"], disable_rules=["vehicle_model_consistency"]
    )
    assert "vehicle_model_consistency" not in {item.capability_id for item in profile.capabilities}


def test_material_label_edit_preserves_requiredness():
    profile = _patch(materials=[{"document_type": "vehicle_license", "label": "行驶证材料"}])
    assert (
        next(
            item
            for item in profile.material_policy.materials
            if item.document_type == "vehicle_license"
        ).minimum_count
        == 1
    )
    assert (
        profile.pack_declaration.material("vehicle_license").hints
        == VEHICLE_SOURCE_DEFAULT.pack_declaration.material("vehicle_license").hints
    )


@pytest.mark.parametrize(
    "template", [p for p in BUSINESS_PROFILES if p.rules_configured], ids=lambda p: p.page_id
)
def test_every_existing_business_can_be_a_template_without_changing_behavior(template):
    region = template.pack_declaration.region(template.region)
    definition = PageDefinition.model_validate(
        {
            "page_id": template.page_id,
            "business_type": template.business_type,
            "region": template.region,
            "title": template.pack_declaration.title,
            "paths": list(region.admin_paths),
            "anchors": list(region.page_anchors),
            "version": "2.0",
            "extends": {"page_id": template.page_id, "version": template.version},
        }
    )
    compiled = compile_page(definition, template)
    ReviewService(registry=BusinessRegistry((compiled,)))
    assert compiled.required_fields == template.required_fields
    assert compiled.review_declared_fields_only == template.review_declared_fields_only
    assert build_collect_manifest(compiled.pack_declaration) == build_collect_manifest(
        template.pack_declaration
    )
    assert {s.capability_id for s in compiled.capabilities} == {
        s.capability_id for s in template.capabilities
    }


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
        _compile({**declaration, "enable_rules": ["invented_rule"]})
    with pytest.raises(ValueError, match="生产者"):
        _compile(
            {**declaration, "fields": [{**declaration["fields"][0], "checks": ["invented-check"]}]}
        )


def test_field_typos_and_dangling_anchors_are_rejected(declaration):
    with pytest.raises(ValueError, match="不存在的字段"):
        _patch(remove_fields=["vehicle.vinn"])
    with pytest.raises(ValueError, match="identity_anchors"):
        _compile({**declaration, "identity_anchors": ["permit.vinn"]})
    with pytest.raises(ValueError, match="identity_anchors"):
        _patch(remove_fields=["vehicle.plate_no"])


def test_required_pages_automatically_request_page_number_extraction(declaration):
    data = {**declaration, "materials": [{**declaration["materials"][0], "required_pages": [1, 2]}]}
    with business_context(_compile(data)):
        assert "registration.covered_pages" in DOCUMENT_POLICIES["permit"].fields


def test_collection_manifest_does_not_contain_backend_decision_policy(declaration):
    manifest = build_collect_manifest(_compile(declaration).pack_declaration)
    assert (
        not {"evidence", "sources", "mode", "required", "normalizer", "authority", "checks"}
        & manifest["fields"][0].keys()
    )
    assert "required" not in manifest["materials"][0]


def test_missing_or_cyclic_template_is_reported(tmp_path, declaration):
    data = {**declaration, "extends": {"page_id": "missing", "version": "1.0"}}
    (tmp_path / "cycle.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="不存在的模板或形成循环"):
        load_page_profiles(tmp_path)


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


def test_registry_rejects_changing_page_owner_between_versions(declaration):
    first = _compile(declaration)
    second = _compile({**declaration, "version": "2.0", "region": "another_city"})
    with pytest.raises(ValueError, match="更换业务或地区"):
        BusinessRegistry((first, second))


def test_registry_rejects_active_profiles_outside_its_snapshot(declaration):
    first = _compile(declaration)
    other = _compile({**declaration, "version": "2.0"})
    with pytest.raises(ValueError, match="当前注册表快照"):
        BusinessRegistry((first,), active_profiles=(other,))
    with pytest.raises(ValueError, match="一个活动版本"):
        BusinessRegistry((first, other), active_profiles=(first, other))


def test_loader_reports_file_and_does_not_change_templates(tmp_path):
    patch = {
        "page_id": "vehicle_source:default",
        "business_type": "vehicle_source",
        "title": "车源",
        "paths": ["/vehicle-source"],
        "version": "2.0",
        "extends": {"page_id": "vehicle_source:default", "version": "1.0"},
        "remove_fields": ["vehicle.typo"],
    }
    (tmp_path / "bad.json").write_text(json.dumps(patch), encoding="utf-8")
    with pytest.raises(ValueError, match="bad.json.*不存在"):
        load_page_profiles(tmp_path, (VEHICLE_SOURCE_DEFAULT,))
    assert VEHICLE_SOURCE_DEFAULT.version == "1.0"


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
            item["id"] == "vehicle_model_consistency" and item["required_fields"] for item in rules
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
        validate_request_route(request, (first, second))
    validate_request_route(request.model_copy(update={"page_id": "permit_page"}), (first, second))


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
    batch = AgentBatchResult(
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
    assert any(task.category == "FIELD" for task in response.review_tasks)


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
        with business_context(profile):
            await asyncio.sleep(0)
            return (field_policy("permit.vin").normalizer, DOCUMENT_POLICIES["permit"].fields)

    assert await asyncio.gather(inspect(first), inspect(second)) == [
        ("vin", ("license.vin",)),
        ("text", ("another.vin",)),
    ]


@pytest.mark.asyncio
async def test_custom_material_uses_generated_extraction_allowlist(declaration):
    profile = _compile(declaration)

    class Client:
        async def extract_fields(self, image, policy):
            assert policy.fields == ("license.vin",)
            return QwenExtraction(
                document_type="permit", fields={"license.vin": "VIN1"}, confidence=0.99
            )

    with business_context(profile):
        batch = await AgentService(Client()).extract_async(
            [
                ImageInput(
                    index=0,
                    src="https://example.test/synthetic.jpg",
                    business_scope="vehicle",
                    document_type_hint="permit",
                ),
            ],
            allowed_document_types=frozenset({"permit"}),
        )
    assert batch.observations[0].field == "permit.vin"
    assert batch.observations[0].value == "VIN1"
