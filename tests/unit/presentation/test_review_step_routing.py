from dataclasses import replace

import pytest
from pydantic import ValidationError

from forj_review_service.contracts.evidence import DifferenceRange, EvidenceFact
from forj_review_service.contracts.recognition import (
    CheckResult,
    MaterialCompletenessIssue,
    MaterialCompletenessReport,
)
from forj_review_service.contracts.review import (
    FieldComparison,
    FieldStatus,
    ReviewDisplayTarget,
    ReviewFieldSnapshot,
    ReviewRequest,
    ReviewTask,
)
from forj_review_service.presentation.routing import build_review_tasks
from forj_review_service.presentation.tasks import prepare_display_tasks
from tests.support.pages import (
    BUSINESS_PROFILES,
    SCRAP_REPLACEMENT_CHANGCHUN,
    SCRAP_REPLACEMENT_QINGDAO,
)


def make_step(**changes) -> ReviewTask:
    values = {
        "step_id": "FIELD-new_vehicle.vin",
        "sequence": 1,
        "category": "FIELD",
        "display_target": ReviewDisplayTarget.PAGE_FIELD,
        "page_field": "new_vehicle.vin",
        "requires_reviewer_action": False,
        "label": "新车车架号",
        "result_status": "MATCH",
        "reason": "页面与材料一致",
    }
    values.update(changes)
    return ReviewTask(**values)


def test_page_field_target_requires_field_key() -> None:
    with pytest.raises(ValidationError):
        make_step(page_field=None)


def test_assistant_target_rejects_page_field_key() -> None:
    with pytest.raises(ValidationError):
        make_step(display_target="ASSISTANT", page_field="new_vehicle.vin")


def test_non_match_requires_reviewer_action() -> None:
    with pytest.raises(ValidationError):
        make_step(result_status="CONFLICT", requires_reviewer_action=False)


def test_matching_step_rejects_reviewer_action_requirement() -> None:
    with pytest.raises(ValidationError):
        make_step(result_status="MATCH", requires_reviewer_action=True)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("field_first", [False, True])
def test_all_bound_rules_survive_field_projection(reverse: bool, field_first: bool) -> None:
    field = make_step(
        **(
            {
                "display_target": "ASSISTANT",
                "page_field": None,
                "page_target_field": "new_vehicle.vin",
            }
            if field_first
            else {}
        )
    )
    rules = [
        make_step(
            step_id="BUSINESS-REQUIRED",
            sequence=2,
            category="BUSINESS_RULE",
            display_target="ASSISTANT",
            page_field=None,
            result_status="CONFLICT",
            requires_reviewer_action=True,
            reason="必要规则存在冲突",
            check_ids=["REQUIRED"],
        ),
        make_step(
            step_id="BUSINESS-SECOND",
            sequence=3,
            category="BUSINESS_RULE",
            display_target="ASSISTANT",
            page_field=None,
            reason="其他规则一致",
            check_ids=["SECOND"],
        ),
    ]
    if reverse:
        rules.reverse()
    tasks = prepare_display_tasks(
        [field, *rules],
        field_check_bindings=(("REQUIRED", "new_vehicle.vin"), ("SECOND", "new_vehicle.vin")),
    )
    assert len(tasks) == 1
    assert tasks[0].result_status == "CONFLICT"
    assert tasks[0].requires_reviewer_action
    assert set(tasks[0].check_ids) == {"REQUIRED", "SECOND"}
    assert "必要规则存在冲突" in tasks[0].reason
    assert "其他规则一致" in tasks[0].reason


def matching_comparison(field: str, value: str = "VIN-1") -> FieldComparison:
    return FieldComparison(
        field=field,
        left_value=value,
        right_value=value,
        status=FieldStatus.MATCH,
        message="页面与材料一致",
    )


def build_steps(
    *,
    page_fields: dict[str, str] | None = None,
    comparisons: list[FieldComparison] | None = None,
    external_checks: list[CheckResult] | None = None,
    business_checks: list[CheckResult] | None = None,
    completeness: MaterialCompletenessReport | None = None,
    limitations: list[str] | None = None,
    review_fields: list[ReviewFieldSnapshot] | None = None,
) -> list[ReviewTask]:
    return build_review_tasks(
        request=ReviewRequest(
            page_url="https://admin.forjtruck.com/scrap-replace-qingdao/review/1",
            region="qingdao",
            page_fields=page_fields or {},
            review_fields=review_fields or [],
        ),
        profile=SCRAP_REPLACEMENT_QINGDAO,
        comparisons=comparisons or [],
        external_checks=external_checks or [],
        business_checks=business_checks or [],
        completeness=completeness,
        limitations=limitations or [],
    )


def test_present_comparison_routes_to_assistant_field() -> None:
    steps = build_steps(
        page_fields={"new_vehicle.vin": "VIN-1"},
        comparisons=[matching_comparison("new_vehicle.vin")],
    )

    step = next(item for item in steps if item.step_id == "FIELD-NEW-VEHICLE-VIN")

    assert step.display_target is ReviewDisplayTarget.ASSISTANT
    assert step.page_field is None
    assert step.page_target_fields == ["new_vehicle.vin", "page_ocr.new_vehicle_vin"]
    assert step.requires_reviewer_action is True


def test_field_step_counts_only_unique_material_images_as_evidence() -> None:
    comparison = FieldComparison(
        field="old_vehicle.type",
        left_value="重型半挂牵引车",
        right_value="牵引车",
        status=FieldStatus.MATCH,
        message="车辆类型属于同一业务大类",
        evidence=[
            {"source": "申请页面字段", "source_id": "page", "value": "牵引车"},
            {"source": "图片识别", "image_id": "old-license", "value": "重型半挂牵引车"},
            {"source": "图片识别", "image_id": "scrap-certificate", "value": "重型半挂牵引车"},
            {"source": "图片识别", "image_id": "old-registration", "value": "重型半挂牵引车"},
        ],
    )

    step = next(
        item
        for item in build_steps(
            page_fields={"old_vehicle.type": "牵引车"},
            comparisons=[comparison],
        )
        if item.step_id == "FIELD-old_vehicle.type"
    )

    assert step.evidence_count == 3
    assert len(step.evidence) == 3
    assert all(item.image_id for item in step.evidence)


def test_jiefang_flag_candidate_uses_page_vocabulary_for_fill() -> None:
    comparison = FieldComparison(
        field="old_vehicle.is_jiefang",
        left_value="福田牌",
        right_value=None,
        status=FieldStatus.REVIEW_REQUIRED,
        message="页面字段未填写",
        evidence=[
            EvidenceFact(
                source="图片识别",
                source_id="registration",
                image_id="registration-image",
                value="福田牌",
            ),
        ],
    )

    step = next(
        item
        for item in build_steps(comparisons=[comparison])
        if item.step_id == "FIELD-old_vehicle.is_jiefang"
    )

    assert step.values[0].value == "否"
    assert step.evidence[0].value == "福田牌"


def test_old_vehicle_vin_task_preserves_backend_difference_ranges() -> None:
    difference = DifferenceRange(
        kind="REPLACE",
        start=11,
        end=19,
        page_start=9,
        page_end=17,
        page_text="7G1E22467",
    )
    comparison = FieldComparison(
        field="old_vehicle.vin",
        left_value="LFWSRXSJ7G1E22467",
        right_value="LFWSRXSJ7G1E22467",
        status=FieldStatus.CONFLICT,
        message="行驶证或登记证车架号后 8 位与二维码官网车架号不一致",
        evidence=[
            EvidenceFact(
                source="图片识别",
                source_id="registration",
                image_id="registration-image",
                value="CA4250P66K24T1A1E4",
                conflicting=True,
                differences=[difference],
            ),
        ],
    )

    step = next(
        item
        for item in build_steps(
            page_fields={"old_vehicle.vin": "LFWSRXSJ7G1E22467"},
            comparisons=[comparison],
        )
        if item.step_id == "FIELD-old_vehicle.vin"
    )

    assert step.result_status == "CONFLICT"
    assert step.values[0].differences == [difference]


def test_external_step_accepts_legacy_dict_evidence() -> None:
    steps = build_steps(
        external_checks=[
            CheckResult(
                check_id="EXTERNAL-QR",
                label="二维码官网核验",
                status="MATCH",
                reason="官网字段已提取",
                evidence=[{"source": "二维码官网字段", "value": {"vin": "VIN-1"}}],
            )
        ]
    )

    step = next(item for item in steps if item.step_id == "EXTERNAL-QR")
    assert step.evidence_sources == ["二维码官网字段"]


def test_missing_page_field_routes_comparison_to_assistant() -> None:
    steps = build_steps(comparisons=[matching_comparison("new_vehicle.vin")])

    step = next(item for item in steps if item.step_id == "FIELD-NEW-VEHICLE-VIN")

    assert step.display_target is ReviewDisplayTarget.ASSISTANT
    assert step.page_field is None
    assert step.page_target_field == "new_vehicle.vin"
    assert step.result_status == "INSUFFICIENT"
    assert step.requires_reviewer_action is True


@pytest.mark.parametrize(
    "check_id",
    [
        "POLICY-NEW-ORIGIN",
        "AFFILIATION-SUBJECT-001",
    ],
)
def test_business_rules_route_to_assistant(check_id: str) -> None:
    steps = build_steps(
        business_checks=[
            CheckResult(
                check_id=check_id,
                label="页面外规则",
                status="MATCH",
                reason="规则通过",
            )
        ]
    )

    step = next(item for item in steps if check_id in item.step_id)

    assert step.display_target is ReviewDisplayTarget.ASSISTANT
    assert step.page_field is None


def date_rule(check_id: str) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        label="日期规则",
        status="MATCH",
        reason="符合政策范围",
    )


@pytest.mark.parametrize(
    ("check_id", "field"),
    [
        ("POLICY-INVOICE-DATE", "invoice.invoice_date"),
        ("POLICY-DISPOSAL-DEADLINE", "old_vehicle.recycle_date"),
    ],
)
def test_date_rules_land_under_their_field(check_id: str, field: str) -> None:
    """日期类规则并进对应字段行，**由业务声明决定并到哪个字段**。

    写死一张表时，每来一个带日期规则的业务都要回引擎里补一行；漏了不报错，
    只是审核员在字段下面找不到这条结论。
    """
    steps = build_steps(business_checks=[date_rule(check_id)])

    assert not any(item.step_id == f"BUSINESS-{check_id}" for item in steps), (
        f"{check_id} 不应再单独成卡"
    )
    backed = next(item for item in steps if item.page_target_field == field)
    assert check_id in backed.details["check_ids"]


@pytest.mark.parametrize(
    ("check_id", "page_field"),
    [("AFFILIATION-AUX-CUSTOMER-NAME", "application.customer_name")],
)
def test_present_affiliation_safeguard_routes_to_its_page_field(
    check_id: str,
    page_field: str,
) -> None:
    steps = build_steps(
        page_fields={page_field: "页面值"},
        business_checks=[
            CheckResult(
                check_id=check_id,
                label="辅助页面字段",
                status="MATCH",
                reason="页面与材料一致",
            )
        ],
    )

    # 结论投影到字段条目上；同一句话不再单出一张助手卡片，否则「待处理」里
    # 会看到同一条结论两遍。
    step = next(item for item in steps if item.step_id == f"FIELD-{page_field}")
    assert step.display_target is ReviewDisplayTarget.ASSISTANT
    assert step.page_field is None
    assert step.details["check_ids"] == [check_id]
    assert [item.step_id for item in steps if item.step_id.startswith("BUSINESS-")] == []


def test_owner_type_control_is_a_non_writable_system_field() -> None:
    steps = build_steps(
        page_fields={"application.owner_type": "个人"},
        review_fields=[
            ReviewFieldSnapshot(
                field="application.owner_type",
                label="车辆所有人类型",
                value="个人",
                control_type="select",
                editable=True,
                order=1,
                section="申请信息",
            )
        ],
    )

    step = next(item for item in steps if item.step_id == "FIELD-application.owner_type")
    assert step.result_status == "MATCH"
    assert step.reason == "系统业务字段，无需材料比对"
    assert step.writable is False


def test_external_check_routes_to_assistant() -> None:
    steps = build_steps(
        external_checks=[
            CheckResult(
                check_id="QR-1",
                label="二维码官网核验",
                status="MATCH",
                reason="官网核验通过",
            )
        ]
    )

    assert steps[0].display_target is ReviewDisplayTarget.ASSISTANT
    assert steps[0].page_field is None


@pytest.mark.parametrize(
    "profile",
    [p for p in BUSINESS_PROFILES if p.rules_configured],
    ids=lambda p: f"{p.business_type}:{p.region}",
)
@pytest.mark.parametrize("page_interaction", [False, True])
@pytest.mark.parametrize("status", ["COMPLETE", "INCOMPLETE", "UNCERTAIN"])
def test_material_report_does_not_create_display_tasks(
    profile, page_interaction: bool, status: str
) -> None:
    steps = build_review_tasks(
        request=ReviewRequest(
            page_url="https://example.test/review",
            business_type=profile.business_type,
            region=profile.region,
        ),
        profile=replace(profile, page_interaction=page_interaction),
        comparisons=[],
        external_checks=[],
        limitations=[],
        business_checks=[
            CheckResult(
                check_id="MATERIAL-COMPLETENESS",
                label="材料完整性",
                status="MATCH" if status == "COMPLETE" else "INSUFFICIENT",
                reason="材料完整",
            )
        ],
        completeness=MaterialCompletenessReport(
            phase="EXTRACTED",
            status=status,
            enforced=False,
        ),
    )

    assert not any(item.category == "MATERIAL" for item in steps)
    assert not any(item.step_id == "BUSINESS-MATERIAL-COMPLETENESS" for item in steps)
    assert [item.sequence for item in steps] == list(range(1, len(steps) + 1))


def test_material_issue_and_limitation_do_not_create_display_tasks() -> None:
    issue = MaterialCompletenessIssue(
        code="MISSING_MATERIAL",
        reason_code="recognition_failed",
        reason_detail="新车发票识别失败或超时",
        material_type="invoice",
        business_scope="new_vehicle",
        message="新车发票未能识别",
        suggested_action="请查看新车发票原图",
    )
    steps = build_steps(
        business_checks=[
            CheckResult(
                check_id="MATERIAL-COMPLETENESS",
                label="材料完整性",
                status="INSUFFICIENT",
                reason="材料缺失或存在不确定证据",
            )
        ],
        completeness=MaterialCompletenessReport(
            phase="EXTRACTED",
            status="INCOMPLETE",
            enforced=False,
            issues=[issue],
        ),
        limitations=["新车发票识别失败或超时"],
    )

    material_steps = [item for item in steps if item.category == "MATERIAL"]

    assert material_steps == []
    assert not any(item.step_id == "BUSINESS-MATERIAL-COMPLETENESS" for item in steps)


def test_non_target_profile_keeps_steps_as_assistant_contracts() -> None:
    steps = build_review_tasks(
        request=ReviewRequest(
            page_url="https://example.test/transfer/1",
            business_type="permit",
            region="default",
            page_fields={"permit.vin": "VIN-1"},
        ),
        profile=replace(
            SCRAP_REPLACEMENT_QINGDAO,
            required_fields=("permit.vin",),
            field_check_bindings=(),
            pack_declaration=None,
        ),
        comparisons=[matching_comparison("permit.vin")],
        external_checks=[],
        business_checks=[],
        completeness=None,
        limitations=[],
    )

    assert [(item.step_id, item.display_target, item.page_field) for item in steps] == [
        ("FIELD-permit.vin", ReviewDisplayTarget.ASSISTANT, None)
    ]


def test_a_passing_date_rule_needs_no_reviewer_action():
    steps = build_steps(
        page_fields={"invoice.invoice_date": "2026-06-01"},
        business_checks=[
            CheckResult(
                check_id="POLICY-INVOICE-DATE",
                label="新车发票日期政策核验",
                status="MATCH",
                reason="符合政策",
            )
        ],
    )
    step = next(item for item in steps if item.page_target_field == "invoice.invoice_date")
    assert step.requires_reviewer_action is False
    assert "符合政策" in step.reason


def test_policy_date_checks_merge_into_field_tasks_for_page_first_profiles() -> None:
    checks = [
        CheckResult(
            check_id="POLICY-INVOICE-DATE",
            label="新车发票日期政策核验",
            status="MATCH",
            reason="符合政策范围：2026-09-01 至 2026-09-30",
        ),
        CheckResult(
            check_id="POLICY-DISPOSAL-DEADLINE",
            label="回收证明交车日期政策核验",
            status="MATCH",
            reason="符合政策范围：不晚于 2026-10-31",
        ),
    ]
    steps = build_steps(
        page_fields={
            "invoice.invoice_date": "2026-09-15",
            "old_vehicle.recycle_date": "2026-10-01",
        },
        comparisons=[
            matching_comparison("invoice.invoice_date", "2026-09-15"),
            matching_comparison("old_vehicle.recycle_date", "2026-10-01"),
        ],
        business_checks=checks,
    )

    task_ids = {item.step_id for item in steps}
    assert "BUSINESS-POLICY-INVOICE-DATE" not in task_ids
    assert "BUSINESS-POLICY-DISPOSAL-DEADLINE" not in task_ids
    invoice = next(item for item in steps if item.step_id == "FIELD-invoice.invoice_date")
    recycle = next(item for item in steps if item.step_id == "FIELD-old_vehicle.recycle_date")
    assert "2026-09-01 至 2026-09-30" in invoice.reason
    assert "不晚于 2026-10-31" in recycle.reason


def test_scrap_field_catalog_follows_the_current_dom_inventory() -> None:
    inventory = [
        ReviewFieldSnapshot(
            field="new_vehicle.vin",
            label="新车车架号",
            value="VIN-1",
            control_type="text",
            order=2,
        ),
        ReviewFieldSnapshot(
            field=None,
            label="页面新增字段",
            value="新增值",
            control_type="text",
            order=1,
        ),
        ReviewFieldSnapshot(
            field="new_vehicle.fuel_type",
            label="新车燃料类型",
            value="柴油",
            control_type="select",
            order=3,
        ),
    ]

    field_steps = [
        item
        for item in build_steps(
            page_fields={"new_vehicle.vin": "VIN-1"},
            comparisons=[matching_comparison("new_vehicle.vin")],
            review_fields=inventory,
        )
        if item.category == "FIELD"
    ]

    assert [item.label for item in field_steps] == [
        "页面新增字段",
        "新车车架号",
        "新车燃料类型",
    ]
    assert [item.step_id for item in field_steps] == [
        "FIELD-DOM-1",
        "FIELD-NEW-VEHICLE-VIN",
        "FIELD-new_vehicle.fuel_type",
    ]
    assert field_steps[0].result_status == "INSUFFICIENT"
    assert field_steps[1].result_status == "INSUFFICIENT"
    assert field_steps[2].result_status == "INSUFFICIENT"


def test_affiliation_controls_are_compared_with_the_subject_type() -> None:
    subject = CheckResult(
        check_id="AFFILIATION-SUBJECT-001",
        label="新旧车主体关系",
        status="MATCH",
        reason="主体关系通过",
        values=[
            {"source": "旧车主体类型", "value": "COMPANY"},
            {"source": "新车主体类型", "value": "COMPANY"},
        ],
    )
    inventory = [
        ReviewFieldSnapshot(
            field="old_vehicle.affiliation",
            label="报废车挂靠",
            value="公司",
            control_type="select",
            order=1,
            operation_only=True,
        ),
        ReviewFieldSnapshot(
            field="new_vehicle.affiliation",
            label="新车挂靠",
            value="",
            control_type="select",
            order=2,
            operation_only=True,
        ),
    ]

    field_steps = [
        item
        for item in build_steps(business_checks=[subject], review_fields=inventory)
        if item.category == "FIELD"
    ]

    assert [(item.label, item.result_status) for item in field_steps] == [
        ("报废车挂靠", "MATCH"),
        ("新车挂靠", "MATCH"),
    ]
    assert "自动填写" in field_steps[1].reason


def test_affiliation_auxiliary_control_uses_its_material_check() -> None:
    auxiliary_checks = [
        CheckResult(
            check_id=check_id,
            label="主体辅助字段",
            status="MATCH",
            reason="页面与主体材料一致",
        )
        for check_id in ("AFFILIATION-AUX-CUSTOMER-NAME",)
    ]
    field_steps = [
        item
        for item in build_steps(
            page_fields={"application.customer_name": "甲公司"},
            business_checks=auxiliary_checks,
            review_fields=[
                ReviewFieldSnapshot(
                    field="application.customer_name",
                    label="客户名称",
                    value="甲公司",
                    order=1,
                )
            ],
        )
        if item.category == "FIELD"
    ]

    assert len(field_steps) == 1
    assert field_steps[0].step_id == "FIELD-application.customer_name"
    assert field_steps[0].result_status == "MATCH"


def test_page_only_field_is_visible_but_never_falsely_marked_as_matching() -> None:
    step = next(
        item
        for item in build_steps(
            review_fields=[
                ReviewFieldSnapshot(
                    field="new_vehicle.fuel_type",
                    label="新车燃料类型",
                    value="柴油",
                    control_type="select",
                    order=1,
                )
            ]
        )
        if item.step_id == "FIELD-new_vehicle.fuel_type"
    )

    assert step.label == "新车燃料类型"
    assert step.result_status == "INSUFFICIENT"
    assert step.requires_reviewer_action is True
    assert [(item.source, item.value) for item in step.values] == [("申请页面字段", "柴油")]


def test_derived_emission_task_keeps_manual_fill_enabled_when_page_value_is_empty() -> None:
    step = next(
        item
        for item in build_steps(
            page_fields={"old_vehicle.emission_standard": "未填写"},
            business_checks=[
                CheckResult(
                    check_id="EMISSION-STANDARD-001",
                    label="排放标准",
                    status="INSUFFICIENT",
                    reason="登记证书排放标准识别为 国五，页面尚未填写，请核对后回填",
                    reason_codes=["PAGE_VALUE_MISSING"],
                    values=[
                        {
                            "source": "登记证书排放标准推导",
                            "value": "国五",
                            "derived_from": "登记证书排放标准",
                        }
                    ],
                    details={
                        "standard": "国五",
                        "permitted_fill_values": ["国五"],
                    },
                )
            ],
            review_fields=[
                ReviewFieldSnapshot(
                    field="old_vehicle.emission_standard",
                    label="排放标准",
                    value="未填写",
                    control_type="select",
                    order=1,
                )
            ],
        )
        if item.step_id == "FIELD-old_vehicle.emission_standard"
    )

    assert step.writable is True
    assert step.details["permitted_fill_values"] == ["国五"]
    assert any(item.value == "国五" for item in step.values)


def test_missing_configured_page_field_becomes_actionable_assistant_step():
    step = next(
        item
        for item in build_steps(
            comparisons=[matching_comparison("new_vehicle.vin")],
        )
        if item.step_id == "FIELD-NEW-VEHICLE-VIN"
    )
    assert step.display_target == "ASSISTANT"
    assert step.result_status == "INSUFFICIENT"
    assert step.requires_reviewer_action is True


def test_changchun_collected_field_routes_to_assistant() -> None:
    steps = build_review_tasks(
        request=ReviewRequest(
            page_url="https://admin.forjtruck.com/scrap-replace-changchun/review/1",
            region="changchun",
            page_fields={"new_vehicle.vin": "VIN-1"},
        ),
        profile=SCRAP_REPLACEMENT_CHANGCHUN,
        comparisons=[matching_comparison("new_vehicle.vin")],
        external_checks=[],
        business_checks=[],
        completeness=None,
        limitations=[],
    )

    step = next(item for item in steps if item.step_id == "FIELD-NEW-VEHICLE-VIN")

    assert step.display_target is ReviewDisplayTarget.ASSISTANT
    assert step.page_field is None
    assert step.requires_reviewer_action is True


def test_ambiguous_collected_page_field_routes_to_assistant() -> None:
    steps = build_review_tasks(
        request=ReviewRequest(
            page_url="https://admin.forjtruck.com/scrap-replace-qingdao/review/1",
            region="qingdao",
            page_fields={"new_vehicle.vin": "VIN-1"},
            collection_diagnostics={
                "ambiguous_fields": ["new_vehicle.vin"],
            },
        ),
        profile=SCRAP_REPLACEMENT_QINGDAO,
        comparisons=[matching_comparison("new_vehicle.vin")],
        external_checks=[],
        business_checks=[],
        completeness=None,
        limitations=[],
    )

    step = next(item for item in steps if item.step_id == "FIELD-NEW-VEHICLE-VIN")

    assert step.display_target is ReviewDisplayTarget.ASSISTANT
    assert step.result_status == "INSUFFICIENT"
    assert step.requires_reviewer_action is True


def test_unmapped_duplicate_vin_snapshot_is_merged_into_composite_task() -> None:
    steps = build_steps(
        page_fields={
            "new_vehicle.vin": "VIN-1",
            "page_ocr.new_vehicle_vin": "VIN-1",
        },
        review_fields=[
            ReviewFieldSnapshot(
                field="new_vehicle.vin", label="新车车架号", value="VIN-1", order=1
            ),
            # A duplicate DOM candidate may arrive without a canonical mapping.
            ReviewFieldSnapshot(field=None, label="新车车架号", value="VIN-1", order=2),
        ],
    )

    assert [item.step_id for item in steps].count("FIELD-NEW-VEHICLE-VIN") == 1
    assert not any(item.step_id.startswith("FIELD-DOM-") for item in steps)


def test_unmapped_duplicate_customer_snapshot_does_not_create_second_task() -> None:
    steps = build_steps(
        page_fields={"application.customer_name": "甲公司"},
        business_checks=[
            CheckResult(
                check_id="AFFILIATION-AUX-CUSTOMER-NAME",
                label="客户名称",
                status="INSUFFICIENT",
                reason="页面与材料未取得可比较的明确值",
            )
        ],
        review_fields=[
            ReviewFieldSnapshot(
                field="application.customer_name", label="客户名称", value="甲公司", order=1
            ),
            ReviewFieldSnapshot(field=None, label="客户名称", value="", order=2),
        ],
    )

    customer_steps = [item for item in steps if item.label == "客户名称"]
    assert len(customer_steps) == 1
    assert customer_steps[0].step_id == "FIELD-application.customer_name"
    assert customer_steps[0].page_value == "甲公司"
