"""新版车源审核规则，所有样本为合成数据，不依赖数据库或模型服务。"""

import pytest

from forj_review_assistant.businesses.completeness import evaluate_extracted
from forj_review_assistant.businesses.configuration.compiler import compile_page
from forj_review_assistant.businesses.configuration.schema import PageDefinition
from forj_review_assistant.businesses.field_policies import field_policy
from forj_review_assistant.businesses.packs.model import build_collect_manifest
from forj_review_assistant.businesses.profiles import VEHICLE_SOURCE_DEFAULT as PROFILE
from forj_review_assistant.businesses.rules.vehicle_source_model import run_vehicle_source_model
from forj_review_assistant.capabilities.specs import ReviewExecutionContext
from forj_review_assistant.contracts.evidence import FieldObservation
from forj_review_assistant.contracts.recognition import AgentBatchResult
from forj_review_assistant.contracts.review import FieldStatus, ReviewFieldSnapshot, ReviewRequest
from forj_review_assistant.evidence.comparison.aggregate import aggregate_by_authority
from forj_review_assistant.presentation.routing import build_review_tasks


def observation(field, value, kind="vehicle_license", **changes):
    return FieldObservation(field=field, value=value, document_type=kind,
                            source_type="image", source_id=kind, business_scope="vehicle", **changes)


def checks(*extra, license_model="解放牌CA4255P25K2T1E6A", model="CA4255P25K2T1E6A80",
           page="解放牌CA4255P25K2T1E6A", engine="CA6DM2-42E6"):
    observations = [observation("vehicle.brand_model", license_model)]
    if model is not None:
        observations.append(observation("vehicle.announcement_model", model, "vehicle_nameplate"))
    if engine is not None:
        observations.append(observation("vehicle.engine_model", engine, "vehicle_nameplate"))
    request = ReviewRequest(page_url="https://example.test", business_type="vehicle_source",
                            page_fields={"vehicle.brand_model": page})
    context = ReviewExecutionContext(request, PROFILE, AgentBatchResult(), tuple([*observations, *extra]))
    return run_vehicle_source_model(context).checks


@pytest.mark.parametrize("page,status", [
    ("解放牌CA4255P25K2T1E6A", "CONFLICT"), ("解放牌CA4255P25K2T1E6A80", "MATCH"),
])
def test_prefix_completion_preserves_brand_and_only_complete_value_can_fill(page, status):
    brand, model = checks(page=page)
    assert brand.status == status
    assert brand.details["permitted_fill_values"] == ["解放牌CA4255P25K2T1E6A80"]
    assert model.status == "INSUFFICIENT"  # 永远由操作员选择具体销售版本。
    assert model.details["model_search"]["announcement_model"] == "CA4255P25K2T1E6A80"
    assert model.details["model_search"]["engine_model"] == "CA6DM2-42E6"


def test_full_license_model_matches_full_auxiliary_model():
    assert checks(license_model="解放牌CA4255P25K2T1E6A80", page="解放牌CA4255P25K2T1E6A80")[0].status == "MATCH"


@pytest.mark.parametrize("extra", [
    observation("vehicle.announcement_model", "CA4255P25K2T1E6A90", "registration_certificate"),
    observation("vehicle.announcement_model", "CA4255P25K2T1E6A80", "registration_certificate", uncertain=True),
])
def test_auxiliary_conflicts_and_uncertainty_never_vote_or_complete(extra):
    brand, model = checks(extra)
    assert brand.status == "INSUFFICIENT" and brand.details["permitted_fill_values"] == []
    assert not model.details["model_search"]["announcement_model"]


def test_incompatible_model_is_not_treated_as_suffix_completion():
    brand, model = checks(model="CA4255P25K2T1E6B80")
    assert brand.status == "INSUFFICIENT"
    assert not model.details["model_search"]["announcement_model"]


def test_engine_uncertainty_only_blocks_engine_search_default():
    brand, model = checks(observation("vehicle.engine_model", "CA6DM2-42E6", "registration_certificate", uncertain=True))
    assert brand.status == "CONFLICT"
    assert brand.details["permitted_fill_values"] == ["解放牌CA4255P25K2T1E6A80"]
    assert model.details["model_search"]["engine_model"] == ""


@pytest.mark.parametrize("reverse", [False, True])
def test_material_resolution_is_order_independent_and_search_issues_do_not_leak_into_brand(reverse):
    observations = [
        observation("vehicle.brand_model", "解放牌CA4255P25K2T1E6A"),
        observation("vehicle.announcement_model", "CA4255P25K2T1E6A80", "registration_certificate"),
        observation("vehicle.announcement_model", "CA4255P25K2T1E6A80", "vehicle_nameplate"),
        observation("vehicle.engine_model", "CA6DM2-42E6", "registration_certificate"),
        observation("vehicle.engine_model", "CA6DM2-46E6", "vehicle_nameplate"),
    ]
    request = ReviewRequest(page_url="https://example.test", business_type="vehicle_source",
                            page_fields={"vehicle.brand_model": "解放牌CA4255P25K2T1E6A80"})
    context = ReviewExecutionContext(request, PROFILE, AgentBatchResult(),
                                     tuple(reversed(observations) if reverse else observations))
    brand, model = run_vehicle_source_model(context).checks
    assert brand.status == "MATCH"
    assert brand.reason == "品牌型号与材料一致"
    assert brand.details["permitted_fill_values"] == ["解放牌CA4255P25K2T1E6A80"]
    assert model.details["model_search"] == {
        "announcement_model": "CA4255P25K2T1E6A80", "engine_model": "",
        "issues": ["发动机型号在材料中存在不同读值，请核对原图或手动输入"],
    }


def test_blank_auxiliary_announcement_never_falls_back_to_the_license():
    brand, model = checks(model="", license_model="解放牌CA4255P25K2T1E6A80")
    assert brand.status == "INSUFFICIENT"
    assert brand.details["permitted_fill_values"] == []
    assert model.details["model_search"]["announcement_model"] == ""


def test_configuration_cannot_enable_model_evidence_rule_without_vin_identity_check():
    data = {
        "page_id": "source:example", "business_type": "source_example", "title": "车源示例",
        "paths": ["/example"], "scopes": ["vehicle"], "enable_rules": ["vehicle_source_model"],
        "materials": [{"document_type": "vehicle_nameplate", "label": "铭牌"}],
        "fields": [{"key": key, "label": key, "aliases": [key],
                    "evidence": [{"material": "vehicle_nameplate", "field": key}]}
                   for key in ("vehicle.brand_model", "vehicle.announcement_model", "vehicle.engine_model")],
    }
    with pytest.raises(ValueError, match=r"vehicle_source_model.*vehicle.vin"):
        compile_page(PageDefinition.model_validate(data))
    data["fields"].append({"key": "vehicle.vin", "label": "VIN", "aliases": ["VIN"],
                           "evidence": [{"material": "vehicle_nameplate", "field": "vehicle.vin"}]})
    assert compile_page(PageDefinition.model_validate(data)).rules_configured


def test_wrong_vehicle_auxiliary_material_never_provides_search_defaults():
    brand, model = checks(observation("vehicle.vin", "LFV00000000000001"),
                          observation("vehicle.vin", "LFV00000000000002", "vehicle_nameplate"))
    assert brand.status == "INSUFFICIENT"
    assert model.details["model_search"]["announcement_model"] == ""
    assert model.details["model_search"]["engine_model"] == ""


def test_search_can_use_license_model_when_auxiliary_has_only_engine():
    _, model = checks(model=None, license_model="解放牌CA4255P25K2T1E6A80")
    assert model.details["model_search"]["announcement_model"] == "CA4255P25K2T1E6A80"


@pytest.mark.parametrize("field,value,other", [
    ("vehicle.vin", "LFV00000000000001", "ZZZ00000000000001"),
    ("vehicle.engine_no", "12345678", "CA6DM2-42E6"),
    ("vehicle.license_type", "重型半挂牵引车", "牵引车"),
])
def test_ordinary_fields_only_use_license_full_values(field, value, other):
    policy = field_policy(field)
    page = FieldObservation(field=field, value=value, source_type="page", source_id="page")
    result = aggregate_by_authority(field, [observation(field, value),
        observation(field, other, "registration_certificate"), page], policy.authority)
    assert result.status == FieldStatus.MATCH
    missing_license = aggregate_by_authority(field, [observation(field, value, "vehicle_nameplate"), page], policy.authority)
    assert missing_license.status != FieldStatus.MATCH
    mismatch = aggregate_by_authority(field, [observation(field, other), page], policy.authority)
    assert mismatch.status == FieldStatus.CONFLICT


@pytest.mark.parametrize("auxiliary", [["registration_certificate"], ["vehicle_nameplate"],
                                      ["registration_certificate", "vehicle_nameplate"], []])
def test_upload_presence_is_independent_of_failed_ocr_and_page_numbers(auxiliary):
    images = [{"index": i, "src": f"https://example.test/{i}.jpg", "businessScope": "vehicle",
               "documentTypeHint": kind} for i, kind in enumerate(["vehicle_license", *auxiliary])]
    request = ReviewRequest(page_url="https://example.test", business_type="vehicle_source", images=images)
    report = evaluate_extracted(request, PROFILE, AgentBatchResult(failed_count=len(images)))
    assert report.presence.status == ("COMPLETE" if auxiliary else "INCOMPLETE")
    assert not report.presence.groups[1].alternatives[0][0].required_pages


def test_manifest_declares_exact_review_scope_and_writable_specs():
    manifest = build_collect_manifest(PROFILE.pack_declaration)
    reviewed = {f["key"] for f in manifest["fields"] if f["reviewable"]}
    assert reviewed == {*PROFILE.required_fields, "vehicle.brand_model", "vehicle.model"}
    assert len(reviewed) == 9
    assert all(not any(word in f["aliases"] for word in ["使用性质", "地址", "住址"]) for f in manifest["fields"])
    assert "spec.engine_model" in manifest["writable_fields"]
    assert PROFILE.enabled_page_actions == ("fill_review_fields",)


def test_model_task_survives_missing_dom_and_brand_permission_survives_projection():
    request = ReviewRequest(page_url="https://example.test", business_type="vehicle_source",
        page_fields={"vehicle.brand_model": "旧值"},
        review_fields=[ReviewFieldSnapshot(field="vehicle.brand_model", label="品牌型号", value="旧值",
                                          control_type="text", editable=True, order=1)])
    tasks = build_review_tasks(request=request, profile=PROFILE, comparisons=[],
        business_checks=list(checks()), external_checks=[], completeness=None, limitations=[])
    by_field = {t.page_target_field: t for t in tasks}
    assert "vehicle.model" in by_field
    assert by_field["vehicle.brand_model"].writable
    assert by_field["vehicle.brand_model"].details["permitted_fill_values"] == ["解放牌CA4255P25K2T1E6A80"]
