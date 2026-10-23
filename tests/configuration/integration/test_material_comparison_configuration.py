"""Declared material comparisons can replace plain SYSTEM input without new rule code."""

from copy import deepcopy
from dataclasses import replace

import pytest

from forj_review_service.businesses.context import BusinessContext
from forj_review_service.businesses.page_config.maintenance import edit_fields, maintenance_contract
from forj_review_service.contracts.recognition import QwenExtraction
from forj_review_service.contracts.review import ImageInput, ReviewRequest
from forj_review_service.integrations.recognition.extraction import RecognitionService
from forj_review_service.runtime.configuration.publication import stage_version
from forj_review_service.runtime.configuration.service import validated_snapshot
from forj_review_service.runtime.review_service import ReviewService
from tests.support.pages import (
    SCRAP_REPLACEMENT_CHANGCHUN,
    SCRAP_REPLACEMENT_QINGDAO,
    configuration_state,
    page_definition,
)


def page_with_synthetic_phone(profile):
    page = page_definition(profile)
    page["fields"].append(
        {
            "key": "application.terminal_phone",
            "label": "合成联系电话",
            "aliases": ["合成联系电话"],
            "section": "new_vehicle",
            "page_section": "new_vehicle",
            "mode": "SYSTEM",
            "required": True,
            "writable": True,
            "reviewable": True,
            "uncertain_requires_review": True,
        }
    )
    return page


def synthetic_state(page):
    state = configuration_state()
    return replace(
        state,
        definitions=tuple(
            page if d["page_id"] == page["page_id"] and d["version"] == page["version"] else d
            for d in state.definitions
        ),
    )


def phone_operation():
    return {
        "op": "set_field_comparison",
        "value": {
            "key": "application.terminal_phone",
            "mode": "SINGLE_SOURCE",
            "normalizer": "phone",
            "evidence": [{"material": "invoice", "field": "invoice.phone", "scope": "new_vehicle"}],
        },
    }


@pytest.mark.parametrize("profile", [SCRAP_REPLACEMENT_QINGDAO, SCRAP_REPLACEMENT_CHANGCHUN])
@pytest.mark.parametrize(
    "printed,uncertain,status,task_status",
    [
        ("+86 (138) 0000-1234", False, "MATCH", "MATCH"),
        ("0451-50001234", False, "CONFLICT", "CONFLICT"),
        (None, False, "REVIEW_REQUIRED", "INSUFFICIENT"),
        ("13800001234", True, "REVIEW_REQUIRED", "INSUFFICIENT"),
    ],
)
@pytest.mark.asyncio
async def test_invoice_phone_reaches_extraction_comparison_and_tasks(
    profile, printed, uncertain, status, task_status
):
    page = page_with_synthetic_phone(profile)
    original = deepcopy(page)
    base = synthetic_state(page)
    page["version"] = "invoice-phone.2"
    candidate = edit_fields(page, [phone_operation()])
    assert (
        next(f for f in original["fields"] if f["key"] == "application.terminal_phone")["mode"]
        == "SYSTEM"
    )
    phone = next(f for f in candidate["fields"] if f["key"] == "application.terminal_phone")
    before = next(f for f in original["fields"] if f["key"] == phone["key"])
    assert all(
        phone[key] == before[key]
        for key in ("writable", "required", "reviewable", "uncertain_requires_review")
    )
    _, state = stage_version(base, candidate)
    _, registry = validated_snapshot(state)
    compiled = registry.resolve(
        "scrap_replacement", profile.region, candidate["version"], candidate["page_id"]
    )
    business = BusinessContext.from_profile(compiled)

    class Extractor:
        async def extract_document(
            self, image, *, business=None, retry_reason=None, focus_boxes=None
        ):
            policy = business.document_policies["invoice"]
            assert "invoice.phone" in policy.fields_for_scope("new_vehicle")
            return QwenExtraction(
                document_type="invoice",
                confidence=0.99,
                fields={"invoice.phone": printed} if printed else {},
                uncertain_fields=["invoice.phone"] if uncertain else [],
                raw_fields=[
                    {
                        "label": "电话",
                        "section": "销货单位",
                        "value": printed or "",
                        "uncertain": uncertain,
                    },
                    {"label": "购买方电话", "section": "购买方", "value": "13800001234"},
                ],
            )

    image = ImageInput(
        index=1,
        src="https://example.test/invoice.jpg",
        business_scope="new_vehicle",
        document_type_hint="invoice",
    )
    batch = await RecognitionService(Extractor()).extract_async([image], business=business)
    service = ReviewService(registry=registry, load_model=False)
    try:
        response, _ = await service.assist_with_batch_async(
            ReviewRequest(
                page_id=candidate["page_id"],
                business_type="scrap_replacement",
                region=profile.region,
                page_url="https://example.test" + candidate["paths"][0],
                profile_version=candidate["version"],
                images=[image],
                page_fields={"application.terminal_phone": "13800001234"},
                review_fields=[
                    {
                        "field": "application.terminal_phone",
                        "label": "终端客户手机号",
                        "value": "13800001234",
                    }
                ],
            ),
            initial_batch=batch,
        )
    finally:
        await service.aclose()
    comparison = next(c for c in response.comparisons if c.field == "application.terminal_phone")
    assert comparison.status == status
    task = next(
        t
        for t in response.review_tasks
        if (t.page_target_field or t.page_field) == comparison.field
    )
    assert task.result_status == task_status
    assert response.recognized_documents[0].raw_fields[0].section == "销货单位"
    # Other raw values must not become evidence merely because they match the page.
    if not printed:
        assert all(o.value != "13800001234" for o in batch.observations)


def test_scoped_reading_instructions_are_configurable_but_material_implementation_is_not():
    page = page_with_synthetic_phone(SCRAP_REPLACEMENT_QINGDAO)
    material = next(m for m in page["materials"] if m["document_type"] == "invoice")
    details = {
        **material["details"],
        "scoped_guidance": [["new_vehicle", "按票面实际区域读取电话，区分购买方和销货单位。"]],
    }
    candidate = edit_fields(
        page,
        [
            {
                "op": "upsert_material",
                "value": {
                    "document_type": "invoice",
                    "guidance": "读取票面事实，再按标准字段映射。",
                    "details": details,
                },
            }
        ],
    )
    assert (
        next(m for m in candidate["materials"] if m["document_type"] == "invoice")["details"]
        == details
    )
    for key, value in (("slots", []), ("derived_field", None), ("scope_independent", True)):
        with pytest.raises(ValueError):
            edit_fields(
                page,
                [
                    {
                        "op": "upsert_material",
                        "value": {
                            "document_type": "invoice",
                            "details": {**details, key: value},
                        },
                    }
                ],
            )


def test_comparison_transition_keeps_explicit_command_and_real_dependencies():
    page = page_with_synthetic_phone(SCRAP_REPLACEMENT_QINGDAO)
    contract = maintenance_contract(page)["fields"]
    assert contract["application.terminal_phone"]["comparison_configurable"]
    assert not contract["new_vehicle.affiliation"]["comparison_configurable"]
    with pytest.raises(ValueError, match="显式"):
        edit_fields(page, [{**phone_operation(), "op": "upsert_field"}])
    with pytest.raises(ValueError):
        edit_fields(
            page,
            [
                {
                    "op": "set_field_comparison",
                    "value": {
                        **phone_operation()["value"],
                        "key": "new_vehicle.affiliation",
                    },
                }
            ],
        )
    with pytest.raises(ValueError):
        edit_fields(
            page,
            [
                {
                    "op": "set_field_comparison",
                    "value": {
                        **phone_operation()["value"],
                        "writable": False,
                    },
                }
            ],
        )


@pytest.mark.parametrize("profile", [SCRAP_REPLACEMENT_QINGDAO, SCRAP_REPLACEMENT_CHANGCHUN])
@pytest.mark.asyncio
async def test_removing_plain_system_input_removes_review_and_collection_but_not_other_fields(
    profile,
):
    from forj_review_service.businesses.packs import build_collect_manifest

    page = page_with_synthetic_phone(profile)
    key = "application.terminal_phone"
    assert maintenance_contract(page)["fields"][key]["removable"]
    candidate = edit_fields(page, [{"op": "remove_field", "value": {"key": key}}])
    assert candidate["fields"] == [f for f in page["fields"] if f["key"] != key]
    assert {k: v for k, v in candidate.items() if k != "fields"} == {
        k: v for k, v in page.items() if k != "fields"
    }
    candidate["version"] = "without-phone.2"
    _, state = stage_version(configuration_state(), candidate)
    _, registry = validated_snapshot(state)
    compiled = registry.resolve(
        "scrap_replacement", profile.region, candidate["version"], candidate["page_id"]
    )
    manifest = build_collect_manifest(compiled.pack_declaration)
    assert key not in {f["key"] for f in manifest["fields"]}
    assert key not in manifest["writable_fields"]
    service = ReviewService(registry=registry, load_model=False)
    try:
        from forj_review_service.contracts.recognition import RecognitionBatchResult

        response, _ = await service.assist_with_batch_async(
            ReviewRequest(
                page_id=candidate["page_id"],
                business_type="scrap_replacement",
                region=profile.region,
                page_url="https://example.test" + candidate["paths"][0],
                profile_version=candidate["version"],
                page_fields={key: "13800001234"},
            ),
            initial_batch=RecognitionBatchResult(),
        )
    finally:
        await service.aclose()
    assert key not in {item.field for item in response.comparisons}
    assert key not in {item.page_target_field or item.page_field for item in response.review_tasks}
    for protected in ("new_vehicle.affiliation", "application.id", "new_vehicle.affiliation"):
        assert not maintenance_contract(page)["fields"][protected]["removable"]
        with pytest.raises(ValueError):
            edit_fields(page, [{"op": "remove_field", "value": {"key": protected}}])
