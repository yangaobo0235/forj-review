"""过户审核：字段声明、材料要求、派生字段、组合字段与开票日期规则。

业务背景：过户审核页面与一致性审核**同址**（`/consistency-qingdao`、
`/consistency-changchun`），靠页面特征文案区分；不分地区，青岛和长春同一套规则。
核对 14 个字段，材料是登记证书第 1、2 页、第 3、4 页和二手车发票。
"""

import pytest

from app.businesses.completeness import evaluate_extracted
from app.businesses.materials import DOCUMENT_POLICIES
from app.businesses.packs import BUSINESS_PACKS, pack_for_business
from app.businesses.packs.transfer import TRANSFER_PACK
from app.businesses.page_catalog import identities_for_path
from app.businesses.profiles import TRANSFER_DEFAULT
from app.businesses.routing import route_fields
from app.businesses.rules.transfer_invoice_date import (
    CHECK_ID,
    PAGE_FIELD,
    SOURCE_PUBLISHED_FIELD,
    build_transfer_invoice_date_check,
)
from app.capabilities.specs import ReviewExecutionContext
from app.compare.composite_fields import build_page_composite_checks
from app.models.review import BusinessType, FieldObservation, Region, ReviewRequest
from app.services.review_response import _build_comparisons
from app.workflow.models import AgentBatchResult


def request_for(page_fields: dict | None = None) -> ReviewRequest:
    return ReviewRequest(
        page_url="https://admin.forjtruck.com/consistency-qingdao?showPageModel=1",
        business_type="transfer",
        region="default",
        page_fields=page_fields or {},
    )


def execution(page_fields: dict | None = None) -> ReviewExecutionContext:
    return ReviewExecutionContext(
        request=request_for(page_fields),
        profile=TRANSFER_DEFAULT,
        batch=AgentBatchResult(),
        observations=(),
    )


# --- 声明与 Profile ---------------------------------------------------------


def test_pack_is_registered_and_resolves_its_own_profile() -> None:
    assert pack_for_business(BusinessType.TRANSFER) is TRANSFER_PACK
    assert TRANSFER_DEFAULT.rules_configured is True
    assert TRANSFER_DEFAULT.region is Region.DEFAULT
    assert TRANSFER_DEFAULT.unconfigured_message is None


def test_the_fourteen_checked_fields_are_declared_in_page_order() -> None:
    assert TRANSFER_DEFAULT.required_fields == (
        "transfer.plate_no",
        "transfer.vin",
        "transfer.invoice_date",
        "transfer.buyer_name",
        "transfer.buyer_id",
        "transfer.invoice_code",
        "transfer.invoice_no",
        "transfer.amount",
        "transfer.seller_name",
        "transfer.seller_id",
        "transfer.model",
        "transfer.destination_authority",
        "transfer.market",
        "transfer.market_tax_no",
    )


def test_every_checked_field_has_a_chinese_label_and_page_alias() -> None:
    labels = TRANSFER_PACK.labels()

    assert labels["transfer.buyer_name"] == "过户发票买家名称"
    assert labels["transfer.market_tax_no"] == "纳税人识别号"
    for key in TRANSFER_DEFAULT.required_fields:
        declaration = TRANSFER_PACK.field(key)
        assert declaration is not None, key
        assert declaration.label, key
        assert declaration.aliases, key


def test_source_published_at_is_collected_but_never_checked() -> None:
    """车源发布时间只给开票日期规则读，不是审核目录里的一条。"""
    declaration = TRANSFER_PACK.field(SOURCE_PUBLISHED_FIELD)

    assert declaration is not None
    assert declaration.aliases == ("车源发布时间",)
    assert declaration.reviewable is False
    assert declaration.mode is None
    assert SOURCE_PUBLISHED_FIELD not in TRANSFER_DEFAULT.required_fields


# --- 页面识别 ---------------------------------------------------------------


def test_both_shared_addresses_resolve_to_transfer() -> None:
    """青岛和长春两个地址挂的是同一条声明，都不分地区。"""
    for path in ("/consistency-qingdao", "/consistency-changchun"):
        found = {(item.business_type, item.region) for item in identities_for_path(path)}
        assert (BusinessType.TRANSFER, Region.DEFAULT) in found, path
        assert (BusinessType.CONSISTENCY, Region.DEFAULT) in found, path


# --- 材料 -------------------------------------------------------------------


def test_three_materials_are_required_and_registration_needs_all_four_pages() -> None:
    policy = TRANSFER_PACK.material_policy
    requirements = {item.document_type: item for item in policy.materials}

    assert policy.mode == "enforce"
    assert set(requirements) == {"registration_certificate", "used_car_invoice"}
    registration = requirements["registration_certificate"]
    assert registration.required_pages == (1, 2, 3, 4)
    assert registration.business_scope == "transfer"
    assert requirements["used_car_invoice"].business_scope == "transfer"


def test_registration_certificate_carries_transfer_fields_in_both_page_halves() -> None:
    fields = TRANSFER_PACK.material("registration_certificate").fields_for_scope("transfer")

    # 第 1、2 页拿到车牌号和车架号，第 3、4 页的转让登记拿到买家名称和证件号。
    assert fields == (
        "transfer.plate_no",
        "transfer.vin",
        "transfer.buyer_name",
        "transfer.buyer_id",
        "registration.covered_pages",
    )


def _batch(*page_groups: tuple[int, ...]) -> AgentBatchResult:
    from app.workflow.models import RecognizedDocument

    return AgentBatchResult(
        recognized_documents=[
            RecognizedDocument(
                target_id=f"registration-{pages[0]}",
                image_index=pages[0],
                document_type="registration_certificate",
                business_scope="transfer",
                covered_pages=list(pages),
            )
            for pages in page_groups
        ]
    )


def _registration_issues(*page_groups: tuple[int, ...]) -> list:
    report = evaluate_extracted(request_for(), TRANSFER_DEFAULT, _batch(*page_groups))
    return [
        issue
        for issue in report.issues
        if issue.material_type == "registration_certificate"
    ]


def test_missing_third_and_fourth_pages_are_reported() -> None:
    """只传了第 1、2 页时，缺的是第 3、4 页——买家的两个字段就没有来源。"""
    issues = _registration_issues((1, 2))

    assert issues
    assert any(issue.missing_pages == [3, 4] for issue in issues), issues


def test_all_four_pages_together_satisfy_the_registration_requirement() -> None:
    assert _registration_issues((1, 2), (3, 4)) == []


def test_pages_out_of_the_declared_range_do_not_satisfy_the_requirement() -> None:
    """页码是数出来的，不是数图片张数：传了第 5、6 页不等于材料齐了。"""
    issues = _registration_issues((5, 6))

    assert any(issue.missing_pages == [1, 2, 3, 4] for issue in issues), issues


# --- 发票代码由数电号码派生 --------------------------------------------------


def test_the_invoice_code_is_derived_from_the_single_digital_number() -> None:
    """二手车销售统一发票票面只有一个数电号码，没有单独的发票代码。"""
    policy = DOCUMENT_POLICIES["used_car_invoice"]

    assert policy.derived_field == ("transfer.invoice_no", "transfer.invoice_code")

    routed, limitation = route_fields(
        "transfer",
        "used_car_invoice",
        {"transfer.invoice_no": "26132000003039067621"},
    )

    assert limitation is None
    assert routed["transfer.invoice_no"] == "26132000003039067621"
    assert routed["transfer.invoice_code"] == routed["transfer.invoice_no"]


def test_the_model_is_told_not_to_output_the_invoice_code() -> None:
    """模型只提一次号码；让它分别生成两个值，冲突是它自己造的。"""
    prompt = DOCUMENT_POLICIES["used_car_invoice"].build_extraction_prompt(
        image_index=1, business_scope="transfer"
    )

    assert "不要输出 transfer.invoice_code" in prompt
    # 二手车市场那一栏才有纳税人识别号，「经营、拍卖单位」那栏通常是空的。
    assert "不要读取「经营、拍卖单位」那一栏" in prompt
    # 只准读小写车价，不能把大写金额或不含税价当成开票金额。
    assert "小写" in prompt

    # 登记证书的提示词里要有"取最近一次"的约束，否则会把卖方当成买家。
    registration_prompt = DOCUMENT_POLICIES[
        "registration_certificate"
    ].build_extraction_prompt(image_index=2, business_scope="transfer")

    assert "只取转让登记日期最晚的那一条" in registration_prompt
    assert "转移登记摘要信息栏" in registration_prompt


# --- 发票代码/号码组合字段 ---------------------------------------------------


def page_fields() -> dict:
    return {
        "transfer.invoice_code": "26132000003039067621",
        "transfer.invoice_no": "26132000003039067621",
    }


def test_matching_invoice_code_and_number_are_compared_against_the_material() -> None:
    checks = build_page_composite_checks(request_for(page_fields()), {})

    assert [check.check_id for check in checks] == ["FIELD-TRANSFER-INVOICE-CODE-NO"]
    assert checks[0].status == "INSUFFICIENT"
    assert "未取得材料核验值" in checks[0].reason


def test_a_page_that_disagrees_with_itself_is_a_conflict() -> None:
    fields = {**page_fields(), "transfer.invoice_code": "2613200000303900"}

    check = build_page_composite_checks(request_for(fields), {})[0]

    assert check.status == "CONFLICT"
    assert "页面两个字段原始值不一致" in check.reason


def test_the_composite_no_longer_lives_only_for_scrap_replacement() -> None:
    """组合字段表从业务声明合成：两个业务各有自己的一对发票代码/号码。"""
    from app.compare.composite_fields import PAGE_FIELD_COMPOSITES

    members = {
        field
        for spec in PAGE_FIELD_COMPOSITES
        for field in (spec.primary_field, spec.secondary_field)
    }

    assert "invoice.invoice_code" not in members
    assert "transfer.invoice_code" in members
    assert "transfer.invoice_no" in members


# --- 开票日期晚于车源发布时间 ------------------------------------------------


@pytest.mark.parametrize(
    "invoice_date,published_at,expected",
    [
        ("2026-09-17", "2026-09-08 13:04:54", "MATCH"),
        # 只比到天：同一天先发布后开票是常事，比到秒会让它随机判成不通过。
        ("2026-09-08", "2026-09-08 13:04:54", "CONFLICT"),
        ("2026-09-07", "2026-09-08 13:04:54", "CONFLICT"),
        # 日期写法不同不影响结论。
        ("2026/09/17", "2026-09-08 13:04:54", "MATCH"),
        ("2026年09月17日", "2026-09-08 13:04:54", "MATCH"),
        # 读不到任何一个日期都交人工复核，不判不通过。
        ("", "2026-09-08 13:04:54", "INSUFFICIENT"),
        ("2026-09-17", "", "INSUFFICIENT"),
        ("2026-13-45", "2026-09-08 13:04:54", "INSUFFICIENT"),
        ("最近", "2026-09-08 13:04:54", "INSUFFICIENT"),
    ],
)
def test_invoice_date_must_be_later_than_the_source_publish_time(
    invoice_date, published_at, expected
) -> None:
    result = build_transfer_invoice_date_check(
        execution({PAGE_FIELD: invoice_date, SOURCE_PUBLISHED_FIELD: published_at})
    )

    assert [check.check_id for check in result.checks] == [CHECK_ID]
    check = result.checks[0]
    assert check.status == expected
    # 两个页面值都要留在卡片上，审核员才能看出是用哪两个值判的。
    assert [str(value.value or "") for value in check.values] == [
        invoice_date,
        published_at,
    ]
    assert [value.source for value in check.values] == [
        "开票日期（页面）",
        "车源发布时间（页面）",
    ]


def test_the_date_rule_reads_the_page_value_not_the_material_one() -> None:
    """页面值是审核员在审的记录本身；材料侧由字段比对单独负责。"""
    result = build_transfer_invoice_date_check(
        execution({PAGE_FIELD: "2026-09-17", SOURCE_PUBLISHED_FIELD: "2026-09-08"})
    )

    assert "晚于车源发布时间" in result.checks[0].reason
    assert "请核对" not in result.checks[0].reason


def test_the_date_rule_is_projected_onto_the_invoice_date_field() -> None:
    """结论投影到「开票日期」字段行，工作台不必再单出一张卡片。"""
    assert TRANSFER_PACK.field_check_bindings == ((CHECK_ID, PAGE_FIELD),)


# --- 一键验真与写回 ---------------------------------------------------------


def test_verify_invoice_triggers_on_the_transfer_invoice_number() -> None:
    assert TRANSFER_PACK.invoice_verification_field == "transfer.invoice_no"
    assert TRANSFER_DEFAULT.invoice_verification_field == "transfer.invoice_no"
    assert "verify_invoice" in {
        binding.capability_id for binding in TRANSFER_DEFAULT.binding_declarations
    }


def test_scrap_replacement_keeps_its_own_verification_field() -> None:
    """触发字段按业务声明：写死一个业务的名字会让另一个业务永远不验真。"""
    from app.businesses.profiles import SCRAP_REPLACEMENT_QINGDAO

    assert SCRAP_REPLACEMENT_QINGDAO.invoice_verification_field == "invoice.invoice_no"


@pytest.mark.parametrize("field", ["transfer.buyer_name", "transfer.buyer_id"])
@pytest.mark.parametrize(
    "documents,expected",
    [
        (("used_car_invoice",), "REVIEW_REQUIRED"),
        (("registration_certificate",), "REVIEW_REQUIRED"),
        (("registration_certificate", "used_car_invoice"), "MATCH"),
    ],
)
def test_buyer_fields_need_both_the_register_and_the_invoice(
    field, documents, expected
) -> None:
    """登记证书第 3、4 页的转让登记和二手车发票必须互相印证。

    只来了一边就判「一致」，等于登记证书没上传时也照样通过——而买家是谁恰恰
    只能从这两处确认。
    """
    observations = [
        FieldObservation(
            field=field,
            source_type="image",
            source_id=document_type,
            image_id=document_type,
            image_index=index,
            value="高广棋",
            document_type=document_type,
            business_scope="transfer",
        )
        for index, document_type in enumerate(documents, start=1)
    ]
    observations.append(FieldObservation(field=field, source_type="page", source_id="review_page", value="高广棋"))

    comparisons = _build_comparisons(
        request_for({"transfer.buyer_name": "高广棋", "transfer.buyer_id": "高广棋"}),
        TRANSFER_DEFAULT,
        observations,
    )
    comparison = next(item for item in comparisons if item.field == field)

    assert comparison.status.value == expected, comparison.message


def test_only_text_controls_may_be_written_back() -> None:
    assert TRANSFER_PACK.page_action_ids == ("fill_review_fields",)
    assert TRANSFER_PACK.writable_control_kinds == ("text", "textarea", "number")
    # 开票日期是日期控件：声明为可写，但控件类型不在白名单里，界面上不会出现
    # 回填按钮，一律交人工填写。
    assert TRANSFER_PACK.field("transfer.invoice_date").writable is True
    assert TRANSFER_DEFAULT.allows_field_write("date") is False
    assert TRANSFER_DEFAULT.allows_field_write("text") is True


def test_the_page_fingerprint_uses_a_strong_anchor() -> None:
    """没有强锚点时指纹是空串，页面写回和原图定位会被全部拒绝。"""
    anchors = TRANSFER_PACK.identity_anchors

    assert any(anchor == "application.id" or anchor.endswith(".vin") for anchor in anchors)
    # 证件号是个人隐私，不放进指纹。
    assert "transfer.buyer_id" not in anchors


def test_other_businesses_are_untouched_by_the_new_pack() -> None:
    assert set(BUSINESS_PACKS) == {
        BusinessType.SCRAP_REPLACEMENT,
        BusinessType.VEHICLE_SOURCE,
        BusinessType.TRANSFER,
    }
