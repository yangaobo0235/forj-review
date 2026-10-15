"""过户审核的业务声明。

页面地址与一致性审核相同（`/consistency-qingdao`、`/consistency-changchun`），
靠列表页的状态筛选进到各自的页面，识别由页面特征文案完成（见
`app/businesses/page_catalog.py`）。**不分地区**：青岛和长春两套页面用的是同一套
规则，所以和车源审核一样取 `Region.DEFAULT`。

核对 14 个字段，材料三份：登记证书第 1、2 页、登记证书第 3、4 页、二手车发票。

设计口径：

- **登记证书是一份材料的两段**（第 1、2 页与第 3、4 页），页码 1~4 全齐才算
  材料完整。两段承载的字段不同：第 1、2 页有车牌号和车架号，第 3、4 页的
  「转让登记」里有买家名称和证件号。
- **登记证书上有多条历史登记，一律取最近一次。** 第 1 页有「注册登记摘要信息栏」
  和「转移登记摘要信息栏」两个机动车登记编号；第 3、4 页有两个转让登记，
  前一个是**卖方**（本次交易的上一手），后一个才是买家。取错一条不会报错，
  只会把买家的名字判成别人的。
- **发票代码由票面数电号码派生**：二手车销售统一发票（电子）票面只有一个
  数电号码，没有单独的发票代码，见 `MaterialDeclaration.derived_field`。
- **开票日期必须晚于车源发布时间**，由 `transfer_invoice_date` 规则判定。

待确认点（必须先核对生产页面再上线）：

- **「车源发布时间」在页面上是只读文字，不是输入框**（和经销商、车源编号、
  车源名称、主机厂一样）。当前采集器只扫表单控件，需要按声明采集只读文本字段，
  否则该规则永远拿不到值。
- 14 个字段在页面 DOM 中的分区标识。当前统一声明为 `unknown`（不做分区限制），
  待页面结构确认后再补 `page_section`。
- 页面上的身份证正反面和营业执照槽位当前归到 `other`（不上传）：业务只要求
  登记证书和二手车发票，这两类资料是否需要参与审核待确认。
"""

from forj_review_assistant.businesses.material_policies import (
    MaterialPolicy,
    MaterialRequirement,
)
from forj_review_assistant.businesses.packs.model import (
    BusinessExtensionPack,
    CompositeFieldDeclaration,
    FieldDeclaration,
    MaterialDeclaration,
    PageGroupDeclaration,
    RegionDeclaration,
    RouteDeclaration,
    SectionDeclaration,
)
from forj_review_assistant.capabilities.specs import CapabilityBinding, CapabilitySpec
from forj_review_assistant.contracts.review import Region

TRANSFER_SCOPE = "transfer"
TRANSFER_SECTION_TITLE = "过户信息"

# 页面地址与一致性审核完全重合，识别只能靠页面特征文案。
TRANSFER_PATHS = ("/consistency-qingdao", "/consistency-changchun")
TRANSFER_ANCHORS = ("审核过户凭证", "过户发票买家名称", "转入地车管所")

# 登记证书第 1、2 页与第 3、4 页是同一份材料的两段，页码 1~4 全齐才算完整。
REGISTRATION_PAGES = (1, 2, 3, 4)

_REGISTRATION_FIELDS = (
    "transfer.plate_no",
    "transfer.vin",
    "transfer.buyer_name",
    "transfer.buyer_id",
    # 只用于材料完整性判页码，不出现在审核目录里。
    "registration.covered_pages",
)

# 二手车销售统一发票上能读到的字段。字段名与领域键同名，路由可以按同名生成。
_USED_CAR_INVOICE_FIELDS = (
    "transfer.invoice_date",
    "transfer.invoice_no",
    "transfer.amount",
    "transfer.buyer_name",
    "transfer.buyer_id",
    "transfer.seller_name",
    "transfer.seller_id",
    "transfer.model",
    "transfer.plate_no",
    "transfer.vin",
    "transfer.destination_authority",
    "transfer.market",
    "transfer.market_tax_no",
)

TRANSFER_MATERIALS = (
    MaterialDeclaration(
        document_type="registration_certificate",
        display_name="机动车登记证书",
        fields=_REGISTRATION_FIELDS,
        guidance=(
            "识别机动车登记证书第 1、2 页和第 3、4 页，读取车牌号、车辆识别代号、"
            "买方名称与证件号码，以及页脚实际印刷的页码。"
        ),
        scoped_fields=((TRANSFER_SCOPE, _REGISTRATION_FIELDS),),
        scoped_guidance=(
            (
                TRANSFER_SCOPE,
                (
                    "读取机动车登记证书。"
                    "**登记证书上有多次历史登记，一律取最近一次，不要取最早的那条。**"
                    "transfer.plate_no 只读取第 1 页「转移登记摘要信息栏」里的机动车登记编号"
                    "（最近一次转移登记后的号牌），不要读取同页上方「注册登记摘要信息栏」里"
                    "第一次上牌时的登记编号。"
                    "transfer.vin 读取第 2 页「注册登记机动车信息栏」第 9 项「车辆识别代号/车架号」，"
                    "不要读取车辆型号、发动机号或发动机型号。"
                    "transfer.buyer_name 和 transfer.buyer_id 只读取第 3、4 页「登记栏」的"
                    "「转让登记」部分：一本登记证书上可能有多条转让登记，"
                    "**只取转让登记日期最晚的那一条**（当前这一次过户的买方），"
                    "更早的一条是本次交易的卖方，不要读它；"
                    "transfer.buyer_id 读取该条「身份证明名称/号码」栏的号码本身，"
                    "不要连「居民身份证」「统一社会信用代码」这些名称一起输出。"
                    "第 1 页的「机动车所有人」是上一手车主，不是买家，不要写进 transfer.buyer_name。"
                    "registration.covered_pages 只输出图片页脚实际印刷的页码数组"
                    "（第 1、2 页那张图输出 [1,2]），不按上传顺序推测。"
                ),
            ),
        ),
        hints=("登记证书", "机动车登记证", "登记证", "过户资料"),
        # 槽位留空：两张图靠图片下方的小标题区分，不按上传顺序兜底。
        scoped_vehicle_fields=(),
    ),
    MaterialDeclaration(
        document_type="used_car_invoice",
        display_name="二手车销售统一发票",
        # 票面只有一个数电号码：模型只提取号码，发票代码由兼容层从它派生。
        # 让模型分别生成两个值，它会给出两个互相冲突的答案，而冲突是它自己造的。
        derived_field=("transfer.invoice_no", "transfer.invoice_code"),
        fields=_USED_CAR_INVOICE_FIELDS,
        guidance=(
            "识别二手车销售统一发票，读取发票号码、开票日期、买方与卖方名称及证件号码、"
            "车牌照号、车架号、厂牌型号、转入地车辆管理所名称、车价合计（小写）、"
            "二手车市场及其纳税人识别号。"
        ),
        scoped_fields=((TRANSFER_SCOPE, _USED_CAR_INVOICE_FIELDS),),
        scoped_guidance=(
            (
                TRANSFER_SCOPE,
                (
                    "读取二手车销售统一发票（电子）。"
                    "transfer.invoice_no 读取票面右上角「发票号码」后的完整号码；"
                    "**不要输出 transfer.invoice_code**，票面没有「发票代码」这一栏，"
                    "页面上那个值由系统从数电号码适配。"
                    "transfer.invoice_date 只读取「开票日期」，不要使用备注或其他日期。"
                    "transfer.buyer_name 读取「买方单位/个人」；"
                    "transfer.buyer_id 读取「买方单位/个人」同行右侧的「单位代码/身份证号码」，"
                    "只输出号码本身，不要连「居民身份证」「统一社会信用代码」一起输出。"
                    "transfer.seller_name 读取「卖方单位/个人」，"
                    "transfer.seller_id 读取同行右侧的「单位代码/身份证号码」。"
                    "transfer.plate_no 读取「车牌照号」；"
                    "transfer.vin 读取「车架号/车辆识别代号」，不要读取厂牌型号。"
                    "transfer.model 读取「厂牌型号」栏的完整原文，品牌名和型号代码一起输出。"
                    "transfer.destination_authority 读取「转入地车辆管理所名称」。"
                    "transfer.amount 读取「车价合计」的**小写**金额，不要输出大写金额、"
                    "不含税价或税额，也不要输出货币符号以外的文字。"
                    "transfer.market 读取「二手车市场」栏的名称；"
                    "transfer.market_tax_no 只读取「二手车市场」那一栏的纳税人识别号，"
                    "不要读取「经营、拍卖单位」那一栏（通常为空）。"
                ),
            ),
        ),
        hints=("二手车发票", "二手车销售统一发票", "过户发票", "过户资料"),
        scoped_names=((TRANSFER_SCOPE, "二手车销售统一发票"),),
        scoped_vehicle_fields=(),
    ),
)

TRANSFER_MATERIAL_POLICY = MaterialPolicy(
    mode="enforce",
    materials=(
        MaterialRequirement(
            "registration_certificate",
            TRANSFER_SCOPE,
            required_pages=REGISTRATION_PAGES,
            display_name="机动车登记证书第 1、2、3、4 页",
        ),
        MaterialRequirement(
            "used_car_invoice",
            TRANSFER_SCOPE,
            display_name="二手车销售统一发票",
        ),
    ),
)


def _material_routes() -> tuple[RouteDeclaration, ...]:
    """材料字段 → 领域字段：过户的材料字段名与领域键同名，直接按同名生成。"""
    return tuple(
        RouteDeclaration(material.document_type, field, (field,), scope=TRANSFER_SCOPE)
        for material in TRANSFER_MATERIALS
        for field in material.fields_for_scope(TRANSFER_SCOPE)
        if field.startswith(f"{TRANSFER_SCOPE}.")
    )


# 只看二手车发票的字段：登记证书上没有这些栏目。
_INVOICE_ONLY = (
    "transfer.invoice_date",
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

TRANSFER_PACK = BusinessExtensionPack(
    business_type="transfer",
    title="过户审核",
    scopes=(TRANSFER_SCOPE,),
    sections=(SectionDeclaration(TRANSFER_SCOPE, TRANSFER_SECTION_TITLE),),
    fields=(
        FieldDeclaration(
            key="transfer.plate_no",
            writable=True,
            label="车牌号",
            section=TRANSFER_SCOPE,
            aliases=("车牌号",),
            page_section="unknown",
            mode="PARALLEL",
            sources=("registration_certificate", "used_car_invoice"),
            normalizer="plate",
            required=True,
        ),
        FieldDeclaration(
            key="transfer.vin",
            writable=True,
            label="识别车架号",
            section=TRANSFER_SCOPE,
            aliases=("识别车架号",),
            page_section="unknown",
            mode="PARALLEL",
            sources=("registration_certificate", "used_car_invoice"),
            normalizer="vin",
            required=True,
        ),
        FieldDeclaration(
            key="transfer.invoice_date",
            writable=True,
            label="开票日期",
            section=TRANSFER_SCOPE,
            aliases=("开票日期",),
            page_section="unknown",
            mode="SINGLE_SOURCE",
            sources=("used_car_invoice",),
            normalizer="date",
            required=True,
        ),
        FieldDeclaration(
            key="transfer.buyer_name",
            writable=True,
            label="过户发票买家名称",
            section=TRANSFER_SCOPE,
            aliases=("过户发票买家名称",),
            page_section="unknown",
            mode="PARALLEL",
            sources=("registration_certificate", "used_car_invoice"),
            # **两边都要有**：登记证书第 3、4 页的转让登记和二手车发票的买方
            # 必须互相印证。只来了一边就判「一致」，等于登记证书没上传时也照样
            # 通过——而买家是谁恰恰只能从这两处确认。
            allow_single_evidence=False,
            normalizer="party_name",
            required=True,
        ),
        FieldDeclaration(
            key="transfer.buyer_id",
            writable=True,
            label="证件号",
            section=TRANSFER_SCOPE,
            aliases=("证件号",),
            page_section="unknown",
            mode="PARALLEL",
            sources=("registration_certificate", "used_car_invoice"),
            # 同买家名称：缺任一侧来源即「仅有一个有效来源，证据不足」。
            allow_single_evidence=False,
            normalizer="identifier",
            required=True,
        ),
        FieldDeclaration(
            key="transfer.invoice_code",
            writable=True,
            label="发票代码",
            section=TRANSFER_SCOPE,
            aliases=("发票代码",),
            page_section="unknown",
            mode="SINGLE_SOURCE",
            sources=("used_car_invoice",),
            normalizer="invoice_number",
            required=True,
        ),
        FieldDeclaration(
            key="transfer.invoice_no",
            writable=True,
            label="发票号码",
            section=TRANSFER_SCOPE,
            aliases=("发票号码",),
            page_section="unknown",
            mode="SINGLE_SOURCE",
            sources=("used_car_invoice",),
            normalizer="invoice_number",
            required=True,
        ),
        FieldDeclaration(
            key="transfer.amount",
            writable=True,
            label="开票金额(含税)",
            section=TRANSFER_SCOPE,
            aliases=("开票金额(含税)", "开票金额（含税）"),
            page_section="unknown",
            mode="SINGLE_SOURCE",
            sources=("used_car_invoice",),
            normalizer="amount",
            required=True,
        ),
        FieldDeclaration(
            key="transfer.seller_name",
            writable=True,
            label="卖方名称",
            section=TRANSFER_SCOPE,
            aliases=("卖方名称",),
            page_section="unknown",
            mode="SINGLE_SOURCE",
            sources=("used_car_invoice",),
            normalizer="party_name",
            required=True,
        ),
        FieldDeclaration(
            key="transfer.seller_id",
            writable=True,
            label="卖方证件号",
            section=TRANSFER_SCOPE,
            aliases=("卖方证件号",),
            page_section="unknown",
            mode="SINGLE_SOURCE",
            sources=("used_car_invoice",),
            normalizer="identifier",
            required=True,
        ),
        FieldDeclaration(
            key="transfer.model",
            writable=True,
            label="厂牌型号",
            section=TRANSFER_SCOPE,
            aliases=("厂牌型号",),
            page_section="unknown",
            mode="SINGLE_SOURCE",
            sources=("used_car_invoice",),
            normalizer="identifier",
            required=True,
        ),
        FieldDeclaration(
            key="transfer.destination_authority",
            writable=True,
            label="转入地车管所",
            section=TRANSFER_SCOPE,
            aliases=("转入地车管所",),
            page_section="unknown",
            mode="SINGLE_SOURCE",
            sources=("used_car_invoice",),
            normalizer="text",
            required=True,
        ),
        FieldDeclaration(
            key="transfer.market",
            writable=True,
            label="二手车市场",
            section=TRANSFER_SCOPE,
            aliases=("二手车市场",),
            page_section="unknown",
            mode="SINGLE_SOURCE",
            sources=("used_car_invoice",),
            normalizer="party_name",
            required=True,
        ),
        FieldDeclaration(
            key="transfer.market_tax_no",
            writable=True,
            label="纳税人识别号",
            section=TRANSFER_SCOPE,
            aliases=("纳税人识别号",),
            page_section="unknown",
            mode="SINGLE_SOURCE",
            sources=("used_car_invoice",),
            normalizer="identifier",
            required=True,
        ),
        # 页面上的只读文字，不对应任何材料，只给 transfer_invoice_date 规则读。
        # reviewable=False：它不是可核验的控件，不进审核目录。
        FieldDeclaration(
            key="application.source_published_at",
            label="车源发布时间",
            section=TRANSFER_SCOPE,
            aliases=("车源发布时间",),
            page_section="unknown",
            reviewable=False,
        ),
    ),
    materials=TRANSFER_MATERIALS,
    # 页面上的图片分组标题 → 业务分区。「过户资料」是唯一的上传区域，两张登记
    # 证书和二手车发票都在这一组里，具体是哪一份由图片下方的小标题区分。
    # 身份证和营业执照归到 other，前端选图时直接排除——业务只要求登记证书和
    # 二手车发票，这两类资料是否参与审核待确认（见模块开头的待确认点）。
    page_groups=(
        PageGroupDeclaration("过户资料", TRANSFER_SCOPE, "过户资料"),
        PageGroupDeclaration("过户凭证", TRANSFER_SCOPE, "过户凭证"),
        PageGroupDeclaration("身份证正面", "other", "身份证正面"),
        PageGroupDeclaration("身份证反面", "other", "身份证反面"),
        PageGroupDeclaration("营业执照", "other", "营业执照"),
        PageGroupDeclaration("其他图片", "other", "其他图片"),
    ),
    routes=_material_routes(),
    regions=(
        # 不分地区：青岛和长春两个地址用的是同一套规则。
        RegionDeclaration(
            Region.DEFAULT,
            admin_paths=TRANSFER_PATHS,
            page_anchors=TRANSFER_ANCHORS,
        ),
    ),
    capability_specs=(
        CapabilitySpec(
            "material_completeness",
            kind="MATERIAL",
            stage="EVIDENCE_VALIDATION",
            failure_policy="MANUAL_REVIEW",
        ),
        CapabilitySpec(
            "transfer_invoice_date",
            kind="RULE",
            stage="POST_COMPARE",
            failure_policy="MANUAL_REVIEW",
        ),
        CapabilitySpec(
            "verify_invoice",
            kind="RULE",
            stage="POST_COMPARE",
            failure_policy="SAFE_DEGRADE",
        ),
    ),
    binding_declarations=(
        CapabilityBinding("material_completeness"),
        CapabilityBinding("transfer_invoice_date"),
        # 发票号码与页面一致时提出「一键验真」，失败不产生人工复核任务。
        CapabilityBinding("verify_invoice", required=False),
    ),
    # 开票日期的规则结论投影到「开票日期」字段行，审核员在字段视图里就能看到，
    # 不必切到页面外核验。
    field_check_bindings=(("TRANSFER-INVOICE-DATE", "transfer.invoice_date"),),
    # 发票代码/号码是一对组合字段：先查页面两个控件是否一致，再与发票比对；
    # 通过后由助手去点页面上的「一键验真」。
    page_field_composites=(
        CompositeFieldDeclaration(
            check_id="FIELD-TRANSFER-INVOICE-CODE-NO",
            label="发票代码/号码",
            primary_field="transfer.invoice_code",
            secondary_field="transfer.invoice_no",
            material_field="transfer.invoice_no",
            primary_label="发票代码",
            secondary_label="发票号码",
        ),
    ),
    # 「一键验真」的触发字段：它和材料一致时才值得去点验真。
    invoice_verification_field="transfer.invoice_no",
    # 过户审核只核对声明的 14 个字段：页面上的经销商、车源编号、车源名称、
    # 主机厂等只读信息不该出现在审核目录里。
    review_declared_fields_only=True,
    page_interaction=True,
    page_action_ids=("fill_review_fields",),
    # 开票日期通过浏览器单日期日历适配器写回，仍需字段与记录身份校验。
    writable_control_kinds=("text", "textarea", "number", "select", "date"),
    # 页面指纹锚点。`transfer.vin` 是强锚点，没有它指纹恒为空串，页面写回和
    # 原图定位会被全部拒绝。证件号是个人隐私，不放进指纹。
    identity_anchors=("transfer.vin", "transfer.plate_no"),
    record_identity_fields=("transfer.vin",),
    material_policy=TRANSFER_MATERIAL_POLICY,
)


# 只看二手车发票的字段在登记证书上没有栏目；单独列出便于测试核对覆盖面。
INVOICE_ONLY_FIELDS = _INVOICE_ONLY
