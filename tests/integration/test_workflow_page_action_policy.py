"""Page action permissions must hold after the real workflow finishes."""

from dataclasses import replace

import pytest

from forj_review_service.businesses.page_config.compiler import compile_page
from forj_review_service.businesses.page_config.schema import PageDefinition
from forj_review_service.businesses.registry import BusinessRegistry
from forj_review_service.contracts.recognition import RecognitionBatchResult
from forj_review_service.contracts.review import FieldObservation, ReviewRequest
from forj_review_service.runtime.review_service import ReviewService
from tests.support.pages import SCRAP_REPLACEMENT_QINGDAO, page_definition


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actions",
    [
        (),
        ("fill_review_fields",),
        ("verify_invoice",),
        ("set_affiliation",),
        ("fill_review_fields", "verify_invoice", "set_affiliation"),
    ],
)
async def test_configured_actions_are_enforced_by_the_complete_review_workflow(actions):
    base = SCRAP_REPLACEMENT_QINGDAO
    affiliation_fields = {"old_vehicle.affiliation", "new_vehicle.affiliation"}
    definition = page_definition(base)
    definition.update(
        title="动作权限回归",
        version="action-policy-test",
        paths=["/synthetic"],
        page_actions=actions,
        actions=[{"action_id": a, "handler_id": a, "label": a} for a in actions],
    )
    for field in definition["fields"]:
        if field["writable"]:
            field["writable"] = "fill_review_fields" in actions or (
                "set_affiliation" in actions and field["key"] in affiliation_fields
            )
    profile = compile_page(PageDefinition.model_validate(definition))
    service = ReviewService(registry=BusinessRegistry((profile,)), load_model=False)
    try:
        response, _ = await service.assist_with_batch_async(
            ReviewRequest(
                page_url="https://example.test/synthetic",
                page_id=profile.page_id,
                profile_version=profile.version,
                region=profile.region,
                page_fields={"invoice.invoice_no": "12345678901234567890"},
                review_fields=[
                    {
                        "field": "invoice.invoice_no",
                        "label": "发票号码",
                        "order": 1,
                        "value": "12345678901234567890",
                        "control_type": "text",
                        "editable": True,
                    }
                ],
            ),
            initial_batch=RecognitionBatchResult(
                observations=[
                    FieldObservation(
                        field="invoice.invoice_no",
                        value="12345678901234567890",
                        source_type="image",
                        source_id="synthetic-invoice",
                        document_type="invoice",
                    ),
                    FieldObservation(
                        field="business_license.company_name",
                        value="测试有限公司",
                        source_type="image",
                        source_id="synthetic-license",
                        document_type="business_license",
                    ),
                ]
            ),
        )
        assert {item.action_id for item in response.page_actions} == (
            set(actions) & {"verify_invoice", "set_affiliation"}
        )
        assert all(item.requires_authorization is False for item in response.page_actions)
        if "fill_review_fields" not in actions:
            assert not any(task.writable for task in response.review_tasks)
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_disabling_page_interaction_suppresses_automatic_actions():
    profile = replace(SCRAP_REPLACEMENT_QINGDAO, page_interaction=False)
    service = ReviewService(registry=BusinessRegistry((profile,)), load_model=False)
    try:
        response, _ = await service.assist_with_batch_async(
            ReviewRequest(page_url="https://example.test/review", region="qingdao"),
            initial_batch=RecognitionBatchResult(
                observations=[
                    FieldObservation(
                        field="business_license.company_name",
                        value="测试有限公司",
                        source_type="image",
                        source_id="synthetic-license",
                        document_type="business_license",
                    )
                ]
            ),
        )
        assert response.page_actions == []
    finally:
        await service.aclose()
