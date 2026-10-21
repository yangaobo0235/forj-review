"""Complete pages are independent; ordinary changes cannot widen permissions."""

import pytest

from forj_review_service.businesses.context import BusinessContext
from forj_review_service.businesses.page_config.compiler import compile_page
from forj_review_service.businesses.page_config.maintenance import (
    edit_fields,
    field_dependencies,
    validate_field_change,
)
from forj_review_service.businesses.page_config.schema import PageDefinition
from forj_review_service.businesses.routing import route_fields
from tests.support.pages import BUSINESS_PROFILES, VEHICLE_SOURCE_DEFAULT, page_definition

PROFILES = [p for p in BUSINESS_PROFILES if p.pack_declaration]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["settings"]["retry_policy"].update(unknown=True),
        lambda p: p["settings"]["capability_specs"][0].update(required="false"),
        lambda p: p["settings"]["retry_policy"].update(semantic_retry_count=True),
        lambda p: p["settings"]["retry_policy"].update(qr_web_retry_count=-1),
        lambda p: p["settings"]["material_policy"]["materials"][0].update(minimum_count=0),
        lambda p: p["materials"][0].update(required=True),
        lambda p: p.update(extends={"page_id": "source", "version": "1"}),
    ],
)
def test_complete_schema_rejects_unknown_or_ambiguous_developer_settings(mutate):
    value = page_definition(PROFILES[0])
    mutate(value)
    with pytest.raises(ValueError):
        PageDefinition.model_validate(value)


def test_deleting_native_implicit_field_removes_its_extraction_and_routing():
    page = page_definition(VEHICLE_SOURCE_DEFAULT)
    key = "vehicle.engine_no"
    assert not field_dependencies(page, key)
    changed = edit_fields(page, [{"op": "remove_field", "value": {"key": key}}])
    profile = compile_page(PageDefinition.model_validate(changed))
    material = profile.pack_declaration.material("vehicle_license")
    assert key not in material.fields_for_scope("vehicle")
    assert profile.pack_declaration.field(key) is None
    routed, issue = route_fields(
        "vehicle", "vehicle_license", {key: "SYNTHETIC"}, profile.pack_declaration
    )
    assert issue is None and key not in routed
    # Rule-only inputs are still extracted and are scoped to this page.
    business = BusinessContext.from_profile(profile)
    assert "vehicle.engine_model" in business.document_policies["vehicle_nameplate"].fields
    assert key not in business.field_policies


def test_shared_input_survives_until_last_ordinary_consumer_is_removed():
    page = page_definition(VEHICLE_SOURCE_DEFAULT)
    value = {
        "key": "vehicle.extra",
        "label": "补充信息",
        "aliases": ["补充信息"],
        "mode": "PARALLEL",
        "evidence": [
            {"material": "vehicle_license", "field": "vehicle.engine_no", "scope": "vehicle"}
        ],
    }
    changed = edit_fields(
        page,
        [
            {"op": "upsert_field", "value": value},
            {"op": "remove_field", "value": {"key": "vehicle.engine_no"}},
        ],
    )
    profile = compile_page(PageDefinition.model_validate(changed))
    assert "vehicle.engine_no" in profile.pack_declaration.material(
        "vehicle_license"
    ).fields_for_scope("vehicle")
    changed = edit_fields(changed, [{"op": "remove_field", "value": {"key": "vehicle.extra"}}])
    profile = compile_page(PageDefinition.model_validate(changed))
    assert "vehicle.engine_no" not in profile.pack_declaration.material(
        "vehicle_license"
    ).fields_for_scope("vehicle")


@pytest.mark.parametrize(
    "change",
    [
        {"writable": True},
        {"checks": ["invented"]},
        {"uncertain_requires_review": False},
        {"authority": [{"source": "page", "label": "绕过"}]},
        {"mode": "SYSTEM"},
    ],
)
def test_field_maintenance_cannot_inject_developer_attributes(change):
    page = page_definition(VEHICLE_SOURCE_DEFAULT)
    with pytest.raises(ValueError):
        edit_fields(page, [{"op": "upsert_field", "value": {"key": "vehicle.engine_no", **change}}])


def test_final_diff_blocks_identity_or_policy_edits_and_protected_deletion():
    page = page_definition(VEHICLE_SOURCE_DEFAULT)
    for name, value in [("page_id", "different"), ("page_actions", []), ("settings", {})]:
        with pytest.raises(ValueError):
            validate_field_change(page, {**page, name: value})
    assert field_dependencies(page, "vehicle.vin")
    with pytest.raises(ValueError):
        edit_fields(page, [{"op": "remove_field", "value": {"key": "vehicle.vin"}}])


def test_new_evidence_cannot_cross_a_materials_declared_scope():
    page = page_definition(PROFILES[0])
    with pytest.raises(ValueError, match="允许分区"):
        edit_fields(
            page,
            [
                {
                    "op": "upsert_field",
                    "value": {
                        "key": "old_vehicle.extra",
                        "label": "补充",
                        "aliases": ["补充"],
                        "mode": "SINGLE_SOURCE",
                        "evidence": [
                            {
                                "material": "invoice",
                                "field": "printed.extra",
                                "scope": "old_vehicle",
                            }
                        ],
                    },
                }
            ],
        )
