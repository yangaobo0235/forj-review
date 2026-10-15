"""车源车型一致性规则：马力、整车型号、排放标准三项确定性结论。

模型只负责从图片里读出原文，归类与取值一律由固定正则完成，所以这组用例
全部用构造的观察值驱动，不调用模型。
"""

from app.businesses.profiles import VEHICLE_SOURCE_DEFAULT
from app.businesses.rules.vehicle_model import build_vehicle_model_checks
from app.capabilities.specs import ReviewExecutionContext
from app.models.review import FieldObservation, ReviewRequest
from app.workflow.models import AgentBatchResult

# 页面车型下拉值的真实格式。
PAGE_MODEL = (
    "一汽解放 J6L 中卡 220马力 4X2 6.75米仓栅式载货车"
    "(CA5180CCYP62K1L4E5)(国五)"
)

LICENSE = "vehicle_license"
REGISTRATION = "registration_certificate"
NAMEPLATE = "vehicle_nameplate"


def observation(field: str, value: object, document_type: str = LICENSE, index: int = 1):
    return FieldObservation(
        field=field,
        source_type="image",
        source_id=f"image-{index}",
        image_index=index,
        image_id=f"image-{index}",
        value=value,
        document_type=document_type,
        business_scope="vehicle",
    )


def context(page_fields: dict | None = None, observations: list | None = None):
    return ReviewExecutionContext(
        request=ReviewRequest(
            page_url="https://admin.forjtruck.com/vehicle-source/approval",
            business_type="vehicle_source",
            region="default",
            page_fields={"vehicle.model": PAGE_MODEL} if page_fields is None else page_fields,
        ),
        profile=VEHICLE_SOURCE_DEFAULT,
        batch=AgentBatchResult(),
        observations=tuple(observations or []),
    )


def run(page_fields: dict | None = None, observations: list | None = None):
    checks = build_vehicle_model_checks(context(page_fields, observations)).checks
    return {item.check_id: item for item in checks}


def material_values(check):
    """一条检查里的材料侧取值；页面侧用 `申请页面字段` 标记。"""
    return [value for value in check.values if value.source != "申请页面字段"]


def test_vehicle_model_produces_three_independent_conclusions() -> None:
    checks = run()

    assert [item.check_id for item in build_vehicle_model_checks(context()).checks] == [
        "VEHICLE-MODEL-POWER",
        "VEHICLE-MODEL-CODE",
        "VEHICLE-MODEL-EMISSION",
    ]
    assert all(item.status == "INSUFFICIENT" for item in checks.values())


def test_one_matching_power_candidate_cannot_hide_another_conflicting_material():
    checks = run(observations=[observation("vehicle.engine_model", "CA4DK1-22E5", REGISTRATION),
                              observation("vehicle.power_kw", "359 kw", NAMEPLATE, index=2)])
    assert checks["VEHICLE-MODEL-POWER"].status == "CONFLICT"


def test_uncertain_engine_model_cannot_automatically_pass_derived_checks():
    engine = observation("vehicle.engine_model", "CA4DK1-22E5", REGISTRATION)
    engine.uncertain = True
    checks = run(observations=[engine])
    assert checks["VEHICLE-MODEL-POWER"].status == "INSUFFICIENT"
    assert checks["VEHICLE-MODEL-EMISSION"].status == "INSUFFICIENT"


def test_vehicle_model_matches_when_materials_agree() -> None:
    checks = run(observations=[
        # 发动机型号尾部 22 → 220 马力；整车型号来自行驶证；铭牌给出排放标准。
        observation("vehicle.engine_model", "CA4DK1-22E5"),
        observation("vehicle.model_code", "CA5180CCYP62K1L4E5"),
        observation("vehicle.emission_standard", "国Ⅴ", REGISTRATION, index=2),
    ])

    assert checks["VEHICLE-MODEL-POWER"].status == "MATCH"
    assert "220" in checks["VEHICLE-MODEL-POWER"].reason
    assert checks["VEHICLE-MODEL-CODE"].status == "MATCH"
    assert checks["VEHICLE-MODEL-EMISSION"].status == "MATCH"


def test_vehicle_model_power_accepts_kilowatt_derivation() -> None:
    """发动机型号读不出马力时，用千瓦功率换算仍然是有效证据。"""
    checks = run(observations=[observation("vehicle.power_kw", "162", NAMEPLATE)])

    assert checks["VEHICLE-MODEL-POWER"].status == "MATCH"
    assert checks["VEHICLE-MODEL-POWER"].details["derivation"] == ["发动机功率"]


def test_vehicle_model_power_tolerates_small_differences() -> None:
    """功率换算四舍五入后有几百瓦误差，差几个马力仍判一致。"""
    checks = run(observations=[observation("vehicle.power_kw", "160", NAMEPLATE)])

    assert checks["VEHICLE-MODEL-POWER"].status == "MATCH"


def test_vehicle_model_reports_conflicts_instead_of_passing() -> None:
    checks = run(observations=[
        observation("vehicle.engine_model", "CA4DK1-18E5"),
        observation("vehicle.model_code", "CA5180CCYP62K1L4E9"),
        observation("vehicle.emission_standard", "国四", REGISTRATION, index=2),
    ])

    assert checks["VEHICLE-MODEL-POWER"].status == "CONFLICT"
    assert checks["VEHICLE-MODEL-CODE"].status == "CONFLICT"
    assert checks["VEHICLE-MODEL-EMISSION"].status == "CONFLICT"


def test_vehicle_model_infers_emission_from_the_engine_model_suffix() -> None:
    checks = run(observations=[observation("vehicle.engine_model", "CA4DK1-22E5")])

    assert checks["VEHICLE-MODEL-EMISSION"].status == "MATCH"
    assert checks["VEHICLE-MODEL-EMISSION"].details["material_emissions"] == ["国五"]


def test_vehicle_model_flags_materials_that_disagree_with_each_other() -> None:
    """行驶证与辅助材料不一致时必须交人工，不能挑一个当结论。"""
    checks = run(observations=[
        observation("vehicle.model_code", "CA5180CCYP62K1L4E5"),
        observation("vehicle.model_code", "CA5180CCYP62K1L4E9", NAMEPLATE, index=2),
    ])

    assert checks["VEHICLE-MODEL-CODE"].status == "CONFLICT"
    assert "材料之间" in checks["VEHICLE-MODEL-CODE"].reason


def test_vehicle_model_ignores_unreadable_material_values() -> None:
    """低质量材料只能得到证据不足，绝不能因为没有可比对的值就判通过。"""
    checks = run(observations=[
        observation("vehicle.power_kw", "看不清", NAMEPLATE),
        observation("vehicle.emission_standard", "无法识别", NAMEPLATE),
    ])

    assert checks["VEHICLE-MODEL-POWER"].status == "INSUFFICIENT"
    assert checks["VEHICLE-MODEL-EMISSION"].status == "INSUFFICIENT"
    assert checks["VEHICLE-MODEL-CODE"].status == "INSUFFICIENT"


def test_vehicle_model_without_page_value_asks_for_review() -> None:
    checks = run(
        page_fields={},
        observations=[
            observation("vehicle.engine_model", "CA4DK1-22E5"),
            observation("vehicle.model_code", "CA5180CCYP62K1L4E5"),
        ],
    )

    assert all(item.status == "INSUFFICIENT" for item in checks.values())
    assert all("页面" in item.reason for item in checks.values())


def test_vehicle_model_accepts_a_model_code_printed_with_a_brand_prefix() -> None:
    """车型型号在部分材料上带品牌前缀，取包含关系仍算一致。"""
    checks = run(observations=[
        observation("vehicle.model_code", "解放牌CA5180CCYP62K1L4E5"),
    ])

    assert checks["VEHICLE-MODEL-CODE"].status == "MATCH"


def test_vehicle_model_reads_the_emission_suffix_wherever_it_sits() -> None:
    """排放后缀不总是收尾：CA6SM6-A48E6N 是国六，WP12.430E50 是国五。"""
    page = (
        "一汽解放新J6P重卡质惠版480马力6X4 LNG牵引车"
        "(CA4250P66M25T1A1E6)(国六)"
    )
    checks = run(
        page_fields={"vehicle.model": page},
        observations=[observation("vehicle.engine_model", "CA6SM6-A48E6N")],
    )
    assert checks["VEHICLE-MODEL-EMISSION"].status == "MATCH"
    assert checks["VEHICLE-MODEL-EMISSION"].details["material_emissions"] == ["国六"]

    five = run(observations=[observation("vehicle.engine_model", "WP12.430E50")])
    assert five["VEHICLE-MODEL-EMISSION"].details["material_emissions"] == ["国五"]


def test_vehicle_model_takes_the_last_number_when_displacement_shares_the_column() -> None:
    """登记证书的“排量/功率”同栏：12520 ml / 359 kw 里的功率是 359。"""
    checks = run(observations=[observation("vehicle.power_kw", "12520 ml / 359 kw")])

    assert checks["VEHICLE-MODEL-POWER"].details["material_powers"] == [488]


def test_one_material_submitted_several_times_yields_one_value_per_check() -> None:
    """登记证书重复上传、正反面分开上传都不能把结论撑成重复值。

    规则读的是原始观察，不走字段比较的合并；不先按材料合并的话，马力会推成
    「488、488」，型号会列出两条一模一样的字符串。
    """
    checks = run(observations=[
        observation("vehicle.power_kw", "12520 ml / 359 kw", REGISTRATION, index=1),
        observation("vehicle.power_kw", "12520 ml / 359 kw", REGISTRATION, index=2),
        observation("vehicle.model_code", "CA5180CCYP62K1L4E5", REGISTRATION, index=1),
        observation("vehicle.model_code", "CA5180CCYP62K1L4E5", REGISTRATION, index=3),
        observation("vehicle.engine_model", "CA4DK1-22E5", NAMEPLATE, index=4),
        observation("vehicle.engine_model", "CA4DK1-22E5", NAMEPLATE, index=5),
    ])

    assert checks["VEHICLE-MODEL-POWER"].details["material_powers"] == [220, 488]
    assert len(material_values(checks["VEHICLE-MODEL-CODE"])) == 1
    assert "、488、" not in checks["VEHICLE-MODEL-POWER"].reason


def test_every_conclusion_carries_its_page_and_material_side() -> None:
    """每条结论都要给出页面侧和材料侧，审核员才看得出比了什么。"""
    checks = run(observations=[observation("vehicle.power_kw", "12520 ml / 359 kw", REGISTRATION, index=7)])

    power = checks["VEHICLE-MODEL-POWER"]
    page_side = [value for value in power.values if value.source == "申请页面字段"]
    material = material_values(power)[0]
    assert [value.value for value in page_side] == ["220 马力"]
    assert power.label == "车型马力"
    assert material.source == "车型马力"
    assert material.value == "488 马力（由发动机功率推导）"
    assert material.detail == "12520 ml / 359 kw"
    assert material.conflicting is True
    assert material.document_type == REGISTRATION
    assert material.image_index == 7

    six = run(
        page_fields={"vehicle.model": "一汽解放480马力牵引车(CA4250P66M25T1A1E6)(国六)"},
        observations=[observation("vehicle.engine_model", "CA6SM6-A48E6N", REGISTRATION)],
    )
    emission = six["VEHICLE-MODEL-EMISSION"]
    assert [value.value for value in emission.values if value.source == "申请页面字段"] == ["国六"]
    material_emission = material_values(emission)[0]
    assert material_emission.value == "国六（由发动机型号的排放后缀推导）"
    assert material_emission.detail == "CA6SM6-A48E6N"
    assert material_emission.conflicting is False


def test_each_conclusion_says_whether_it_agrees_with_the_page() -> None:
    """逐条结论标注是否与页面一致，审核员不用回头读理由。

    车型一个条目下挂着五六条候选值，只有逐条标注才看得出是哪一个对不上。
    """
    checks = run(
        page_fields={"vehicle.model": "青岛解放 悍V重卡 2.0 430马力 6X4 牵引车(CA4250P1K15T1E6A80)(国六)"},
        observations=[
            observation("vehicle.power_kw", "348 kw", REGISTRATION, index=1),
            observation("vehicle.model_code", "CA4250P2K8T1NE6A80", REGISTRATION, index=1),
            observation("vehicle.emission_standard", "国六", REGISTRATION, index=1),
        ],
    )

    assert [
        (item.value, item.conflicting)
        for item in material_values(checks["VEHICLE-MODEL-POWER"])
    ] == [("473 马力（由发动机功率推导）", True)]
    assert [
        (item.value, item.conflicting)
        for item in material_values(checks["VEHICLE-MODEL-CODE"])
    ] == [("CA4250P2K8T1NE6A80", True)]
    assert [
        (item.value, item.conflicting)
        for item in material_values(checks["VEHICLE-MODEL-EMISSION"])
    ] == [("国六", False)]


def test_conclusions_do_not_claim_agreement_when_the_page_value_is_missing() -> None:
    """页面侧解析不出值时不做判断：给一个"一致"比不给还危险。"""
    checks = run(
        page_fields={},
        observations=[
            observation("vehicle.power_kw", "348 kw", REGISTRATION),
            observation("vehicle.model_code", "CA4250P2K8T1NE6A80", REGISTRATION),
            observation("vehicle.emission_standard", "国六", REGISTRATION),
        ],
    )

    for check in checks.values():
        assert all(item.conflicting is None for item in material_values(check))
        assert all(item.conflicting is None for item in check.values)


def test_every_conclusion_starts_out_without_a_field_level_note() -> None:
    """逐条说明是**字段装配**填的（`check_reason`），规则本身不管展示。

    规则只产出值；把检查级的比对说明贴到候选值上是投影层的职责，两层不要混。
    """
    checks = run(observations=[observation("vehicle.model_code", "CA5180CCYP62K1L4E5")])

    material = material_values(checks["VEHICLE-MODEL-CODE"])[0]
    assert material.check_reason is None


def test_one_conclusion_reached_two_ways_is_a_single_row() -> None:
    """同一个马力的两种推导途径只出一行，途径并列写在括号里。

    否则审核员会看到两行一模一样的「220 马力」，以为材料之间在重复。
    """
    checks = run(observations=[
        observation("vehicle.engine_model", "CA4DK1-22E5"),
        observation("vehicle.power_kw", "162", NAMEPLATE, index=2),
    ])

    assert [(value.source, value.value) for value in material_values(checks["VEHICLE-MODEL-POWER"])] == [
        ("车型马力", "220 马力（由发动机型号、发动机功率推导）"),
    ]


def test_emission_declared_on_the_document_beats_the_inferred_one() -> None:
    """证件上直接印了排放标准时不出推导说明，也不重复出一行。"""
    checks = run(observations=[
        observation("vehicle.emission_standard", "国五", NAMEPLATE),
        observation("vehicle.engine_model", "CA4DK1-22E5", NAMEPLATE, index=2),
    ])

    check = checks["VEHICLE-MODEL-EMISSION"]
    assert [(value.source, value.value) for value in material_values(check)] == [
        ("车型排放标准", "国五"),
    ]
    # 值和理由必须一致：都说是材料上直接读到的，不说“由发动机型号推导”。
    assert "由发动机型号的排放后缀推导" not in check.reason


def test_emission_reason_says_when_it_was_inferred() -> None:
    """排放标准多是从发动机型号推的，理由里要说明来源。"""
    inferred = run(observations=[observation("vehicle.engine_model", "CA6SM6-A48E6N", REGISTRATION)])
    declared = run(observations=[observation("vehicle.emission_standard", "国五", NAMEPLATE)])

    assert "由发动机型号的排放后缀推导" in inferred["VEHICLE-MODEL-EMISSION"].reason
    assert "由发动机型号的排放后缀推导" not in declared["VEHICLE-MODEL-EMISSION"].reason
    # 证件上直接印的排放标准不加推导说明。
    assert material_values(declared["VEHICLE-MODEL-EMISSION"])[0].value == "国五"
