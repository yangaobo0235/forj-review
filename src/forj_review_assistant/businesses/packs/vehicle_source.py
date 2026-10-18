"""新版车源审核：行驶证核对、辅助型号读取与人工车型选择。"""

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

LICENSE_FIELDS = (
    ("plate_no", "车牌号码", ("车牌号码", "号牌号码", "车牌号"), "identifier"),
    ("vin", "VIN", ("VIN", "车架号", "车辆识别代号"), "vin"),
    ("engine_no", "发动机号", ("发动机号", "发动机号码"), "identifier"),
    ("license_type", "行驶证车辆类型", ("行驶证车辆类型",), "text"),
    ("registration_date", "注册日期", ("注册日期",), "date"),
    ("issue_date", "发证日期", ("发证日期",), "date"),
    ("owner", "所有人", ("所有人", "车辆所有人"), "party_name"),
)
SPEC_FIELDS = (
    ("brand", "品牌"), ("series", "车系"), ("drive_form", "驱动形式"),
    ("fuel_type", "燃料种类"), ("emission_standard", "排放标准"),
    ("max_hp", "最大马力"), ("engine_brand", "发动机品牌"),
    ("engine_model", "发动机型号"), ("cargo_length", "货箱长度"),
)
LICENSE_FIELD_KEYS = tuple(f"vehicle.{field[0]}" for field in LICENSE_FIELDS)
MODEL_EVIDENCE_FIELDS = ("vehicle.announcement_model", "vehicle.engine_model", "vehicle.vin")
_LICENSE = MaterialRequirement("vehicle_license", "vehicle", display_name="行驶证正面")
_REGISTRATION = MaterialRequirement(
    "registration_certificate", "vehicle", required_pages=(1, 2),
    display_name="登记证书1、2页", alternative_group="model_evidence",
)
_NAMEPLATE = MaterialRequirement(
    "vehicle_nameplate", "vehicle", display_name="铭牌", alternative_group="model_evidence",
)

VEHICLE_SOURCE_PACK = BusinessExtensionPack(
    business_type="vehicle_source", title="车源审核", scopes=("vehicle",),
    sections=(SectionDeclaration("license", "行驶证信息"),
              SectionDeclaration("spec", "规格参数")),
    fields=(
        FieldDeclaration("vehicle.type", "车辆类型", aliases=("车辆类型",), reviewable=False),
        *(FieldDeclaration(
            f"vehicle.{suffix}", label, section="license", aliases=aliases,
            page_section="license", section_required=True, required=True, writable=True,
            mode="SINGLE_SOURCE", sources=("vehicle_license",), normalizer=normalizer,
            authority=(AuthorityRule("image", "行驶证正面", ("vehicle_license",)),
                       AuthorityRule("page", "页面字段", required=True)),
        ) for suffix, label, aliases, normalizer in LICENSE_FIELDS),
        # 由独立规则比较、生成补全值，不让通用比较把截断型号判成材料冲突。
        FieldDeclaration("vehicle.brand_model", "品牌型号", section="license",
                         aliases=("品牌型号",), page_section="license",
                         section_required=True, writable=True),
        FieldDeclaration("vehicle.model", "车型", section="spec", aliases=("车型",),
                         page_section="spec", section_required=True, writable=True),
        *(FieldDeclaration(f"spec.{suffix}", label, section="spec", aliases=(label,),
                           page_section="spec", section_required=True,
                           reviewable=False, writable=True)
          for suffix, label in SPEC_FIELDS),
        FieldDeclaration("vehicle.announcement_model", "整车型号", reviewable=False),
        FieldDeclaration("vehicle.engine_model", "发动机型号（材料）", reviewable=False),
    ),
    materials=(
        MaterialDeclaration(
            "vehicle_license", "行驶证正面",
            fields=(*LICENSE_FIELD_KEYS, "vehicle.brand_model"),
            guidance="只读取行驶证正面及同图副页的实际文字。发动机号是号码，不是发动机型号。"
                     "vehicle.license_type读取完整车辆类型，不折叠为大类。品牌型号保留品牌文字和"
                     "实际可见型号，末尾被截断就保留截断值，禁止补写。不得从登记证或铭牌代读。",
            hints=("行驶证正面", "行驶证"), allowed_scopes=("vehicle",),
        ),
        MaterialDeclaration(
            "registration_certificate", "登记证书1、2页",
            fields=(*MODEL_EVIDENCE_FIELDS, "registration.covered_pages"),
            guidance="只从登记证书第1、2页读取车辆型号、发动机型号、车辆识别代号。"
                     "整车型号写vehicle.announcement_model；发动机型号写vehicle.engine_model，"
                     "禁止用发动机号码替代。保留型号的全部后缀。第3、4页不用于推断这些字段。"
                     "registration.covered_pages仅记录原图明确可见的页码，输出整数数组，不猜测未展示页码。",
            hints=("登记证书1、2页", "登记证书1,2页", "登记证书"),
            allowed_scopes=("vehicle",),
        ),
        MaterialDeclaration(
            "vehicle_nameplate", "铭牌",
            fields=MODEL_EVIDENCE_FIELDS,
            guidance="读取铭牌上的完整整车型号、完整发动机型号和VIN。"
                     "禁止推导马力、排放或销售版本；发动机号码不能替代发动机型号。",
            hints=("车辆铭牌", "铭牌"), allowed_scopes=("vehicle",),
        ),
    ),
    routes=tuple(
        RouteDeclaration(document, field, (field,))
        for document, fields in (
            ("vehicle_license", (*LICENSE_FIELD_KEYS, "vehicle.brand_model")),
            ("registration_certificate", MODEL_EVIDENCE_FIELDS),
            ("vehicle_nameplate", MODEL_EVIDENCE_FIELDS),
        ) for field in fields
    ),
    page_groups=(PageGroupDeclaration("证件资料", "vehicle", "证件资料"),
                 PageGroupDeclaration("车况资料", "other", "车况资料"),
                 PageGroupDeclaration("附件", "other", "附件")),
    ignored_image_labels=("行驶证反面", "登记证书3、4页", "登记证书3,4页", "登记证书3，4页"),
    regions=(RegionDeclaration(Region.DEFAULT,
                               admin_paths=("/consistency-qingdao", "/consistency-changchun"),
                               page_anchors=("行驶证信息", "规格参数"), review_tab="车源审核"),),
    capability_specs=(CapabilitySpec("vehicle_source_model", stage="POST_COMPARE"),),
    field_check_bindings=(("VEHICLE-SOURCE-BRAND-MODEL", "vehicle.brand_model"),
                          ("VEHICLE-SOURCE-MODEL", "vehicle.model")),
    page_action_ids=("fill_review_fields",),
    page_interaction=True, review_declared_fields_only=True,
    writable_control_kinds=("text", "textarea", "number", "date", "select"),
    identity_anchors=("vehicle.vin", "vehicle.plate_no", "vehicle.owner"),
    record_identity_fields=("vehicle.vin",),
    material_policy=MaterialPolicy(
        mode="observe", materials=(_LICENSE, _REGISTRATION, _NAMEPLATE),
        presence_groups=(
            MaterialPresenceGroup("license", "行驶证正面", ((_LICENSE,),)),
            MaterialPresenceGroup("model_evidence", "车型辅助材料", ((_REGISTRATION,), (_NAMEPLATE,))),
        ),
    ),
)
