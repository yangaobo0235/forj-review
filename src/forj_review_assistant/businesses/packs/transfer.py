"""过户审核：旧车身份、二手车发票和新车发票分别取证。"""

from forj_review_assistant.businesses.material_policies import (
    MaterialPolicy,
    MaterialPresenceGroup,
    MaterialRequirement,
)
from forj_review_assistant.businesses.packs.model import (
    AuthorityRule,
    BusinessExtensionPack,
    FieldDeclaration,
    MaterialDeclaration,
    PageGroupDeclaration,
    RegionDeclaration,
    RouteDeclaration,
    SectionDeclaration,
)
from forj_review_assistant.capabilities.specs import CapabilitySpec
from forj_review_assistant.contracts.review import Region

USED_SCOPE = "transfer_vehicle"
NEW_SCOPE = "transfer_new"
USED_PREFIX = "transfer_invoice"
NEW_PREFIX = "transfer_new_invoice"

# 后缀、页面标签、页面别名、归一化器。旧车 VIN 由专门规则处理。
USED_FIELDS = (
    ("invoice_date", "开票日期", ("开票日期",), "date"),
    ("plate_no", "车牌号", ("车牌号", "车牌号码"), "plate"),
    ("vin", "识别车架号", ("识别车架号",), "vin"),
    ("buyer_name", "过户发票买家名称", ("过户发票买家名称",), "party_name"),
    ("buyer_id", "证件号", ("证件号",), "identifier"),
    ("invoice_no", "发票号码", ("发票号码",), "invoice_number"),
    ("amount", "开票金额(含税)", ("开票金额(含税)", "开票金额（含税）"), "amount"),
    ("seller_name", "卖方名称", ("卖方名称",), "party_name"),
    ("seller_id", "卖方证件号", ("卖方证件号",), "identifier"),
    ("brand_model", "厂牌型号", ("厂牌型号",), "text"),
    ("transfer_department", "转入地车管所", ("转入地车管所",), "text"),
    ("market_name", "二手车市场", ("二手车市场",), "party_name"),
    ("market_tax_no", "纳税人识别号", ("纳税人识别号",), "identifier"),
)
NEW_FIELDS = (
    ("invoice_date", "新车发票开票日期", ("新车发票开票日期", "开票日期"), "date"),
    ("invoice_no", "新车发票号码", ("新车发票号码", "发票号码"), "invoice_number"),
    ("amount", "新车发票价税合计", ("新车发票价税合计", "价税合计"), "amount"),
    ("vin", "新车发票OCR识别VIN", ("新车发票OCR识别VIN", "识别车架号"), "vin"),
)

USED_KEYS = tuple(f"{USED_PREFIX}.{name}" for name, *_ in USED_FIELDS)
NEW_KEYS = tuple(f"{NEW_PREFIX}.{name}" for name, *_ in NEW_FIELDS)
REGISTRATION_KEYS = ("transfer_registration.vin", "registration.covered_pages")
USED_GUIDANCE = (
    "只读取二手车销售统一发票。buyer_name和buyer_id属于买方；seller_name和seller_id"
    "属于卖方；market_name和market_tax_no只读二手车市场及其对应纳税人识别号，"
    "不得混用买卖方证件号、经营拍卖单位或其他主体的信息。amount读取车价合计小写金额，"
    "不读取税额、不推算金额；brand_model保留票面实际厂牌型号，只有品牌也原样输出，"
    "不补全整车型号或销售车型。vin读取完整车辆识别代号，plate_no读取车牌照号。"
    "invoice_no保留全部位数和前导零；invoice_date只读开票日期，不提取或核对发票代码。"
)
NEW_GUIDANCE = (
    "只读取机动车销售统一发票，不读取二手车发票。amount只读价税合计小写金额，"
    "不能用不含税价或税额代替。vin只读本张新车发票车辆识别代号，不能取旧车VIN。"
    "invoice_no保留全部位数和前导零；invoice_date只读开票日期，不提取或核对发票代码。"
)
REGISTRATION_GUIDANCE = (
    "只读取旧车登记证书第1、2页的完整车辆识别代号，写入transfer_registration.vin。"
    "不根据初始车主推定本次买卖方，不推断过户日期或当前车主。"
    "registration.covered_pages仅记录图片明确可见的页码，输出整数数组，不猜测缺页。"
)

_REGISTRATION = MaterialRequirement(
    "registration_certificate", USED_SCOPE, required_pages=(1, 2), display_name="登记证书1、2页",
)
_USED = MaterialRequirement("used_car_invoice", USED_SCOPE, display_name="二手车发票")
_NEW = MaterialRequirement("invoice", NEW_SCOPE, display_name="新车发票")


def _fields(prefix, scope, definitions, document_type):
    for name, label, aliases, normalizer in definitions:
        custom = prefix == USED_PREFIX and name == "vin"
        yield FieldDeclaration(
            f"{prefix}.{name}", label, aliases=aliases, section=scope,
            page_section=scope, section_required=True, writable=True,
            mode=None if custom else "SINGLE_SOURCE", sources=(document_type,),
            normalizer=normalizer, required=not custom,
            authority=() if custom else (
                AuthorityRule("image", "二手车发票" if prefix == USED_PREFIX else "新车发票",
                              (document_type,)),
                AuthorityRule("page", "页面字段", required=True),
            ),
        )


TRANSFER_PACK = BusinessExtensionPack(
    business_type="transfer", title="过户审核", scopes=(USED_SCOPE, NEW_SCOPE),
    sections=(SectionDeclaration(USED_SCOPE, "审核过户凭证"),
              SectionDeclaration(NEW_SCOPE, "新车发票信息")),
    fields=(
        *_fields(USED_PREFIX, USED_SCOPE, USED_FIELDS, "used_car_invoice"),
        *_fields(NEW_PREFIX, NEW_SCOPE, NEW_FIELDS, "invoice"),
        FieldDeclaration("transfer_registration.vin", "登记证书VIN", reviewable=False),
        FieldDeclaration("registration.covered_pages", "登记证书可见页码", reviewable=False),
    ),
    materials=(
        MaterialDeclaration(
            "registration_certificate", "登记证书1、2页", fields=REGISTRATION_KEYS,
            guidance=REGISTRATION_GUIDANCE,
            scoped_fields=((USED_SCOPE, REGISTRATION_KEYS),),
            scoped_guidance=((USED_SCOPE, REGISTRATION_GUIDANCE),),
            hints=("登记证书1、2页", "登记证书1,2页", "登记证书1，2页", "登记证书"),
            allowed_scopes=(USED_SCOPE,),
        ),
        MaterialDeclaration(
            "used_car_invoice", "二手车发票", fields=USED_KEYS,
            guidance=USED_GUIDANCE, hints=("二手车发票", "二手车销售统一发票", "过户发票"),
            allowed_scopes=(USED_SCOPE,),
        ),
        MaterialDeclaration(
            "invoice", "新车发票", fields=NEW_KEYS,
            guidance=NEW_GUIDANCE,
            scoped_fields=((NEW_SCOPE, NEW_KEYS),),
            scoped_guidance=((NEW_SCOPE, NEW_GUIDANCE),),
            hints=("新车发票", "机动车销售统一发票"), allowed_scopes=(NEW_SCOPE,),
            slots=((NEW_SCOPE, 1),),
        ),
    ),
    routes=(
        *(RouteDeclaration("registration_certificate", key, (key,), scope=USED_SCOPE)
          for key in REGISTRATION_KEYS),
        *(RouteDeclaration("used_car_invoice", key, (key,), scope=USED_SCOPE)
          for key in USED_KEYS),
        *(RouteDeclaration("invoice", key, (key,), scope=NEW_SCOPE)
          for key in NEW_KEYS),
    ),
    page_groups=(
        PageGroupDeclaration("过户资料", USED_SCOPE, "过户资料"),
        PageGroupDeclaration("新车发票", NEW_SCOPE, "新车发票"),
    ),
    ignored_image_labels=("登记证书3、4页", "登记证书3,4页", "登记证书3，4页"),
    regions=(RegionDeclaration(
        Region.DEFAULT, admin_paths=("/consistency-qingdao", "/consistency-changchun"),
        review_tab="过户审核", page_anchors=("审核过户凭证", "新车发票信息"),
    ),),
    capability_specs=(CapabilitySpec("transfer_documents", stage="POST_COMPARE"),),
    field_check_bindings=(
        ("TRANSFER-OLD-VIN", f"{USED_PREFIX}.vin"),
    ),
    page_action_ids=("fill_review_fields",), page_interaction=True,
    review_declared_fields_only=True,
    writable_control_kinds=("text", "textarea", "number", "date", "select"),
    identity_anchors=(f"{USED_PREFIX}.vin", f"{NEW_PREFIX}.vin"),
    record_identity_fields=(f"{USED_PREFIX}.vin", f"{NEW_PREFIX}.vin"),
    material_policy=MaterialPolicy(
        mode="observe", materials=(_REGISTRATION, _USED, _NEW),
        presence_groups=tuple(
            MaterialPresenceGroup(key, requirement.display_name, ((requirement,),))
            for key, requirement in (("registration", _REGISTRATION), ("used_invoice", _USED),
                                     ("new_invoice", _NEW))
        ),
    ),
)
