"""尚未配置的一致性审核仍受保护；不能借新版过户配置恢复旧规则。"""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from forj_review_service.bootstrap.application import create_app
from forj_review_service.businesses.field_policies import field_policy
from forj_review_service.businesses.materials import DOCUMENT_POLICIES
from forj_review_service.businesses.packs import pack_for_business, pack_for_scope
from forj_review_service.businesses.page_catalog import active_identities
from forj_review_service.businesses.page_config.compiler import compile_page
from forj_review_service.businesses.page_config.schema import PageDefinition
from forj_review_service.businesses.profiles import CONSISTENCY_DEFAULT
from forj_review_service.businesses.rules.catalog import RULE_DEFINITIONS
from forj_review_service.contracts.recognition import RecognitionBatchResult
from forj_review_service.contracts.review import ReviewRequest
from forj_review_service.runtime.jobs.manager import ReviewJobManager
from forj_review_service.runtime.review_service import ReviewService
from forj_review_service.runtime.workflow.planner import plan_capabilities


@pytest.fixture(params=[CONSISTENCY_DEFAULT],
                ids=lambda p: p.business_type)
def profile(request):
    return request.param


def request_for(profile):
    return ReviewRequest(
        business_type=profile.business_type, region=profile.region, page_id=profile.page_id,
        page_url="https://example.test/reset-business",
        page_fields={"vehicle.vin": "OLD-SOURCE", "transfer.vin": "OLD-TRANSFER"},
        images=[{"index": 0, "src": "https://images.example.test/synthetic.jpg"}],
    )


def test_no_old_configuration_or_template_remains(profile):
    assert not profile.rules_configured
    assert profile.pack_declaration is None
    assert not profile.required_fields and not profile.sections
    assert profile.material_policy is None and profile.replacement_policy is None
    assert not profile.capabilities and not profile.bindings
    assert not profile.field_check_bindings and not profile.enabled_page_actions
    assert profile.invoice_verification_field is None and not profile.page_interaction
    assert not profile.allows_field_write("text")
    assert pack_for_business(profile.business_type) is None
    identity = next(p for p in active_identities() if p.business_type == profile.business_type)
    assert identity.review_tab == "一致性审核"
    assert set(identity.paths) == {"/consistency-qingdao", "/consistency-changchun"}
    assert [(p.capability_id, p.status) for p in plan_capabilities(profile)] == [("profile", "NOT_CONFIGURED")]
    definition = PageDefinition.model_validate({
        "page_id": profile.page_id or "consistency:default", "business_type": profile.business_type,
        "title": "新规划", "paths": ["/new-review"], "version": "2.0",
        "extends": {"page_id": profile.page_id or "consistency:default", "version": profile.version},
    })
    with pytest.raises(ValueError, match="已配置的业务模板"):
        compile_page(definition, profile)


def test_retired_rules_routes_and_fields_are_not_registered():
    assert not {"vehicle_model_consistency", "transfer_invoice_date"} & RULE_DEFINITIONS.keys()
    assert "used_car_invoice" in DOCUMENT_POLICIES
    for policy in DOCUMENT_POLICIES.values():
        assert not {"transfer"} & {scope for scope, _ in policy.scoped_fields}
        assert not any(field.startswith("transfer.") for field in policy.fields)
    assert pack_for_scope("transfer") is None
    assert field_policy("transfer.vin") is None


@pytest.mark.asyncio
async def test_direct_and_single_image_requests_cannot_run_models_or_workflow(profile):
    service = ReviewService(load_model=False)
    service.recognition = AsyncMock()
    service.workflow.run = AsyncMock()
    request = request_for(profile)
    response = await service.assist_async(request)
    await service.extract_image_async(request, request.images[0])
    assert response.presentation == "MANUAL_REVIEW"
    assert response.recommendation == "REVIEW_REQUIRED"
    assert "尚未配置" in response.summary
    assert not response.comparisons and not response.review_tasks and not response.page_actions
    # Even an internal partial-result projection must discard stale conclusions/actions.
    partial = service._build_response(request, RecognitionBatchResult(), business_checks=[])
    assert partial.presentation == "MANUAL_REVIEW" and not partial.page_actions
    service.recognition.extract_async.assert_not_called()
    service.workflow.run.assert_not_called()


def test_url_jobs_cannot_run_models_or_restore_old_tasks(profile):
    service = ReviewService(load_model=False)
    service.recognition = AsyncMock()
    manager = ReviewJobManager(service)
    try:
        created = manager.create_urls(request_for(profile))
        assert manager.wait(created.job_id, 3)
        result = manager.get(created.job_id).result
        assert result.presentation == "MANUAL_REVIEW"
        assert not result.comparisons and not result.review_tasks and not result.page_actions
        service.recognition.extract_async.assert_not_called()
    finally:
        manager.close()


def test_http_catalog_does_not_publish_old_collection_or_rules(profile):
    with TestClient(create_app(review_service=ReviewService(load_model=False))) as client:
        assert client.get("/api/review/collect-manifest", params={
            "business_type": profile.business_type,
        }).status_code == 404
        rules = client.get("/api/review/configuration/rules").json()["rules"]
    assert not {"vehicle_model_consistency", "transfer_invoice_date"} & {r["id"] for r in rules}
