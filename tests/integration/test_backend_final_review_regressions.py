from dataclasses import replace

import pytest

from forj_review_assistant.businesses.packs.scrap_replacement import (
    CHANGCHUN_REPLACEMENT_POLICY,
    QINGDAO_REPLACEMENT_POLICY,
)
from forj_review_assistant.businesses.profiles import (
    SCRAP_REPLACEMENT_CHANGCHUN,
    SCRAP_REPLACEMENT_QINGDAO,
)
from forj_review_assistant.businesses.registry import BusinessRegistry
from forj_review_assistant.businesses.rules.affiliation import (
    build_affiliation_subject_check,
)
from forj_review_assistant.contracts.recognition import AgentBatchResult
from forj_review_assistant.contracts.review import (
    FieldObservation,
    Region,
    ReviewRequest,
)
from forj_review_assistant.runtime.review_service import ReviewService


@pytest.mark.parametrize(
    "path,business,region",
    [
        ("scrap-replace-changchun", "scrap_replacement", "qingdao"),
        ("scrap-replace-qingdao", "scrap_replacement", "changchun"),
        # 这两个业务取默认地区，声明青岛或长春都不对。
        ("consistency-changchun", "consistency", "qingdao"),
        ("consistency-qingdao", "consistency", "changchun"),
        ("scrap-replace-changchun", "consistency", "changchun"),
        ("consistency-qingdao", "scrap_replacement", "qingdao"),
    ],
)
def test_known_admin_route_rejects_conflicting_request_and_api(client, path, business, region):
    request = ReviewRequest(
        page_url=f"https://admin.forjtruck.com/{path}?showPageModel=1",
        business_type=business,
        region=region,
    )
    with pytest.raises((ValueError, LookupError)):
        ReviewService().resolve_profile(request)
    for endpoint in ("/api/review/assist", "/api/review/jobs"):
        response = client.post(endpoint, json=request.model_dump(mode="json"))
        assert response.status_code == 422
        assert "不一致" in response.json()["detail"]


@pytest.mark.parametrize(
    "path,business,region",
    [
        ("scrap-replace-changchun", "scrap_replacement", "changchun"),
        ("scrap-replace-qingdao", "scrap_replacement", "qingdao"),
        # 一致性和过户不分地区：两个地址都取默认地区。
        ("consistency-changchun", "consistency", "default"),
        ("consistency-qingdao", "consistency", "default"),
    ],
)
def test_matching_admin_route_and_unknown_test_url_remain_accepted(
    path, business, region
):
    service = ReviewService()
    assert (
        service.resolve_profile(
            ReviewRequest(
                page_url=f"https://admin.forjtruck.com/{path}/detail/1",
                business_type=business,
                region=region,
            )
        ).region.value
        == region
    )
    assert (
        service.resolve_profile(
            ReviewRequest(
                page_url="https://example.test/scrap-replace-changchun",
                region="qingdao",
            )
        ).region.value
        == "qingdao"
    )


@pytest.mark.parametrize(
    "profile,change",
    [
        (
            SCRAP_REPLACEMENT_CHANGCHUN,
            {"replacement_policy": QINGDAO_REPLACEMENT_POLICY},
        ),
        (
            SCRAP_REPLACEMENT_QINGDAO,
            {"replacement_policy": CHANGCHUN_REPLACEMENT_POLICY},
        ),
        (
            SCRAP_REPLACEMENT_QINGDAO,
            {"replacement_policy": replace(QINGDAO_REPLACEMENT_POLICY, version="2.0")},
        ),
        (
            SCRAP_REPLACEMENT_QINGDAO,
            {
                "replacement_policy": replace(
                    QINGDAO_REPLACEMENT_POLICY, policy_id="scrap_replacement_changchun"
                )
            },
        ),
        (SCRAP_REPLACEMENT_CHANGCHUN, {"rule_groups": ("qingdao_replacement_policy",)}),
        (
            SCRAP_REPLACEMENT_QINGDAO,
            {
                "rule_groups": (
                    "qingdao_replacement_policy",
                    "changchun_replacement_policy",
                )
            },
        ),
    ],
)
def test_policy_identity_must_match_profile_and_declared_rule(profile, change):
    with pytest.raises(ValueError):
        ReviewService(registry=BusinessRegistry((replace(profile, **change),)))


async def run_review(*, region, page_fields, business_type="scrap_replacement"):
    """端到端执行真实 LangGraph 主图；仅桩掉文档提取，避免调用真实模型。"""
    service = ReviewService()

    async def extract(*args):
        return AgentBatchResult()

    service._extract_documents = extract
    return await service.assist_async(
        ReviewRequest(
            page_url=f"https://example.test/{business_type}-{region}",
            business_type=business_type,
            region=region,
            page_fields=page_fields,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["qingdao", "changchun"])
async def test_scrap_profiles_return_valid_display_routes(region):
    result = await run_review(region=region, page_fields={"new_vehicle.vin": "VIN-1"})
    assert all(step.display_target in {"PAGE_FIELD", "ASSISTANT"} for step in result.review_tasks)
    assert all(step.page_field for step in result.review_tasks if step.display_target == "PAGE_FIELD")
    assert all(step.page_field is None for step in result.review_tasks if step.display_target == "ASSISTANT")


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["qingdao", "changchun"])
async def test_scrap_runs_route_collected_field_to_page_and_rest_to_assistant(region):
    result = await run_review(region=region, page_fields={"new_vehicle.vin": "VIN-1"})
    # 后端一次运行到底就返回完整展示路由：步骤非空、按顺序重排为连续序号。
    assert [step.sequence for step in result.review_tasks] == list(
        range(1, len(result.review_tasks) + 1)
    )
    vin_step = next(
        step for step in result.review_tasks if step.step_id == "FIELD-NEW-VEHICLE-VIN"
    )
    assert vin_step.display_target == "ASSISTANT"
    assert vin_step.page_field is None
    # 页面外政策、二维码、挂靠守护与材料异常只能停留在助手面板。
    assert all(
        step.display_target == "ASSISTANT" and step.page_field is None
        for step in result.review_tasks
        if step.step_id != "FIELD-NEW-VEHICLE-VIN"
    )
    # 缺少主体关系证据时不生成任何挂靠填写意图。
    assert result.page_fill_intent == []


def license_observations(source, company, representative, *, uncertain=False):
    return [
        FieldObservation(
            field=f"business_license.{field}",
            value=value,
            source_type="image",
            source_id=source,
            image_id=source,
            document_type="business_license",
            uncertain=uncertain and field == "legal_representative",
        )
        for field, value in [
            ("company_name", company),
            ("legal_representative", representative),
        ]
    ]


def mixed_licenses(kind):
    observations = license_observations(
        "a1", "甲有限公司", "张三"
    ) + license_observations("b", "乙有限公司", "张三")
    if kind == "uncertain":
        observations += license_observations("a2", "甲有限公司", "李四", uncertain=True)
    elif kind == "same_source_conflict":
        observations += license_observations(
            "a2", "甲有限公司", "李四"
        ) + license_observations("a2", "甲有限公司", "王五")
    elif kind == "company_name_conflict":
        observations += license_observations(
            "a2", "甲有限公司", "李四"
        ) + license_observations("a2", "丙有限公司", "李四")
    elif kind == "masked":
        observations += license_observations("a2", "甲有限公司", "张*")
    else:
        observations += license_observations("a2", "甲有限公司", "李四")
    return observations


@pytest.mark.parametrize(
    "kind",
    [
        "uncertain",
        "same_source_conflict",
        "company_name_conflict",
        "masked",
        "clear_conflict",
    ],
)
@pytest.mark.parametrize(
    "old,new",
    [
        ("甲有限公司", "乙有限公司"),
        ("甲有限公司", "张三"),
        ("张三", "甲有限公司"),
    ],
)
def test_all_relevant_license_sources_participate_in_sufficiency(kind, old, new):
    result = build_affiliation_subject_check(old, new, mixed_licenses(kind))
    assert result.check.status == "INSUFFICIENT"
    assert result.page_actions == ()
    assert {item.source_id for item in result.check.evidence} >= {"a1", "a2"}


@pytest.mark.parametrize(
    "kind", ["uncertain", "same_source_conflict", "masked", "clear_conflict"]
)
@pytest.mark.asyncio
async def test_graph_blocks_intent_for_additional_bad_license_with_all_auxiliary_checks_matching(
    kind, monkeypatch
):
    observations = mixed_licenses(kind) + [
        FieldObservation(
            field=field,
            value=value,
            source_type="image",
            source_id=source,
            document_type="vehicle_license",
            business_scope=scope,
        )
        for field, value, source, scope in [
            ("old_vehicle.owner", "甲有限公司", "old", "old_vehicle"),
            ("new_vehicle.owner", "乙有限公司", "new", "new_vehicle"),
            ("new_vehicle.vin", "VIN1", "new", "new_vehicle"),
        ]
    ]
    service = ReviewService()

    async def extract(*args):
        return AgentBatchResult(observations=observations)

    monkeypatch.setattr(service, "_extract_documents", extract)
    result = await service.assist_async(
        ReviewRequest(
            page_url="https://example.test/review",
            region="qingdao",
            page_fields={
                "old_vehicle.owner": "甲有限公司",
                "new_vehicle.owner": "乙有限公司",
                "new_vehicle.vin": "VIN1",
                "application.owner_type": "公司",
                "application.customer_name": "乙有限公司",
                "page_ocr.new_vehicle_vin": "VIN1",
            },
        )
    )
    checks = {item.check_id: item.status for item in result.cross_checks}
    assert checks["AFFILIATION-SUBJECT-001"] == "INSUFFICIENT"
    assert all(
        status == "MATCH"
        for key, status in checks.items()
        if key.startswith("AFFILIATION-AUX")
    )
    assert result.page_fill_intent == []


@pytest.mark.parametrize(
    "path,region,stage",
    [
        ("scrap-replace-qingdao", "default", "transfer"),
        ("scrap-replace-changchun", "default", "transfer"),
        # 共用地址上的业务取默认地区，声明成青岛或长春都不对——地址上的两个
        # 业务属于同一个地区，这个维度在它们身上不成立。
        ("consistency-qingdao", "changchun", "transfer"),
        ("consistency-changchun", "qingdao", "consistency"),
    ],
)
def test_transfer_compatibility_does_not_allow_other_route_contexts(
    path, region, stage
):
    request = ReviewRequest(
        page_url=f"https://admin.forjtruck.com/{path}/review/1",
        business_type="transfer",
        region=region,
        workflow_stage=stage,
    )
    with pytest.raises((ValueError, LookupError)):
        ReviewService().resolve_profile(request)


@pytest.mark.parametrize(
    "path",
    ["consistency-qingdao", "consistency-changchun"],
)
def test_three_review_steps_share_consistency_routes(path):
    """车源、过户、一致性共用入口；一致性的规则仍未配置。"""
    for business_type, configured in (("consistency", False), ("transfer", True), ("vehicle_source", True)):
        request = ReviewRequest(
            page_url=f"https://admin.forjtruck.com/{path}/review/1",
            business_type=business_type,
            region="default",
            workflow_stage=business_type,
        )
        profile = ReviewService().resolve_profile(request)
        assert profile.business_type.value == business_type
        assert profile.region is Region.DEFAULT
        assert profile.rules_configured is configured
        # 未配置的业务必须说清楚为什么不能自动判；配置好的业务不写这句话。
        assert bool(profile.unconfigured_message) is not configured


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("missing", "MATCH"),
        ("uncertain", "INSUFFICIENT"),
        ("same_source_conflict", "INSUFFICIENT"),
        ("company_name_conflict", "INSUFFICIENT"),
        ("masked", "INSUFFICIENT"),
        ("clear_conflict", "INSUFFICIENT"),
    ],
)
def test_identical_company_requires_one_unambiguous_matching_license(kind, expected):
    observations = (
        [
            FieldObservation(
                field="business_license.company_name",
                value="甲有限公司",
                source_type="image",
                source_id="a",
                document_type="business_license",
            )
        ]
        if kind == "missing"
        else mixed_licenses(kind)
    )
    result = build_affiliation_subject_check("甲有限公司", "甲有限公司", observations)
    assert result.check.status == expected
    assert bool(result.page_actions) == (expected == "MATCH")
