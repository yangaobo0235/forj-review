"""车源审核的业务声明。

页面路径是 `/vehicle-source/approval`，只核对车型基础信息（基础信息、行驶证
信息、车身与车籍信息、规格参数、补充信息）里的 13 个字段，材料只需要行驶证，
登记证书第 1、2 页与车辆铭牌二选一。

设计口径：

- **材料只有一份分区**（`vehicle`）。车源页面不像报废置换那样区分旧车/新车，
  所以不存在跨分区的字段歧义，材料字段名与领域字段名同名。
- **行驶证是唯一基准材料，必须存在**；登记证书与车辆铭牌是辅助材料，两者
  上传其一即可（`MaterialRequirement.alternative_group`），辅助材料与行驶证
  不一致时按冲突处理，交人工复核，不做自动否决。
- **车型（`vehicle.model`）不参与材料比对**：它是页面下拉值，由
  `vehicle_model_consistency` 规则按固定正则解析后与材料侧比对；因此该字段
  `mode=None` 且 `reviewable=False`，只采集、不生成字段核验任务。
- **规则输入字段**（`vehicle.model_code`、`vehicle.power_kw`、
  `vehicle.emission_standard`）只供规则读取，`aliases` 为空表示不从页面采集。

待确认点（必须先核对生产页面再上线）：

- 图片上传区域的实际分组标题文案。这里按页面区块名称列出候选，若生产页面
  用的是别的写法，图片会落到 `unknown` 分区，字段一律不路由并提示人工复核。
- 13 个字段在页面 DOM 中的分区标识。当前统一声明为 `unknown`（不做分区限制），
  待页面结构确认后再补 `page_section`。
"""

from forj_review_assistant.businesses.material_policies import (
    MaterialPolicy,
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
from forj_review_assistant.capabilities.specs import CapabilityBinding, CapabilitySpec
from forj_review_assistant.contracts.review import Region

VEHICLE_SCOPE = "vehicle"
VEHICLE_SECTION_TITLE = "车源车辆信息"

# 登记证书第 1、2 页与车辆铭牌互为替代材料，只需上传其中一份。
SUPPLEMENT_GROUP = "vehicle_supplement"

# 材料字段 → 领域字段。车源的材料字段名与领域字段名同名，绝大多数可以直接
# 按同名生成；只有行驶证的“车辆类型”需要同时支撑两个页面控件，单独列出。
_ROUTE_EXCEPTIONS = {"vehicle_license": {"vehicle.type"}}

_VEHICLE_LICENSE_FIELDS = (
    "vehicle.plate_no",
    "vehicle.vin",
    "vehicle.engine_no",
    "vehicle.type",
    "vehicle.brand_model",
    "vehicle.usage_nature",
    "vehicle.registration_date",
    "vehicle.issue_date",
    "vehicle.owner",
    "vehicle.fuel_type",
)
# 行驶证没有独立的分区兜底差异：它本来就只承载上面这些栏目。
_VEHICLE_LICENSE_FALLBACK = _VEHICLE_LICENSE_FIELDS

_REGISTRATION_FIELDS = (
    "vehicle.vin",
    "vehicle.engine_model",
    "vehicle.model_code",
    "vehicle.brand_model",
    "vehicle.fuel_type",
    "vehicle.emission_standard",
    "vehicle.power_kw",
    "registration.covered_pages",
)
_REGISTRATION_FALLBACK = tuple(
    field for field in _REGISTRATION_FIELDS if field != "vehicle.engine_model"
)

_NAMEPLATE_FIELDS = (
    "vehicle.vin",
    "vehicle.engine_model",
    "vehicle.model_code",
    "vehicle.brand_model",
    "vehicle.power_kw",
    "vehicle.emission_standard",
)
_NAMEPLATE_FALLBACK = tuple(
    field for field in _NAMEPLATE_FIELDS if field != "vehicle.engine_model"
)

VEHICLE_SOURCE_MATERIALS = (
    MaterialDeclaration(
        document_type="vehicle_license",
        display_name="机动车行驶证",
        fields=_VEHICLE_LICENSE_FALLBACK,
        guidance=(
            "识别机动车行驶证正反面，正反面可能拼在同一张图中。"
            "分别读取号牌号码、车辆识别代号、发动机号码、车辆类型、品牌型号、"
            "使用性质、注册日期、发证日期、所有人和燃料种类。"
        ),
        scoped_fields=((VEHICLE_SCOPE, _VEHICLE_LICENSE_FIELDS),),
        scoped_guidance=(
            (
                VEHICLE_SCOPE,
                (
                    "读取正面号牌号码、车辆识别代号、发动机号码、车辆类型、使用性质、"
                    "品牌型号、注册日期、发证日期、所有人和燃料种类。"
                    "车辆识别代号不得读取车辆型号或发动机号码；号牌号码不得读取档案编号或条形码数字；"
                    "车辆类型保留票面完整原文；注册日期只读取‘注册日期’，不得使用发证日期或检验有效期代替。"
                    "‘品牌型号’栏按票面完整原文读取，品牌名和型号代码一起写入 vehicle.brand_model。"
                    "行驶证上只有‘发动机号码’，没有‘发动机型号’，不要把发动机号码当作发动机型号，"
                    "也不要用它拼出车辆型号。"
                ),
            ),
        ),
        hints=("行驶证", "行驶证正面", "行驶证反面", "证件照", "车源资料", "车辆资料"),
        # 行驶证被报废置换和车源审核共用；车源业务只认行驶证本身，
        # 不沿用报废置换的“行驶证或车辆资料”口径。
        scoped_names=((VEHICLE_SCOPE, "机动车行驶证"),),
        # 槽位留空：车源页面上传的图片数量和顺序不固定，按次序兜底会把铭牌
        # 误判成登记证书。图片类型由页面文案与模型识别共同确定。
        scoped_vehicle_fields=(),
    ),
    MaterialDeclaration(
        document_type="registration_certificate",
        display_name="机动车登记证书",
        fields=_REGISTRATION_FALLBACK,
        guidance=(
            "识别机动车登记证书第 1、2 页，读取车辆识别代号、车辆型号、车辆品牌、"
            "发动机型号、燃料种类、排量或功率，以及页脚实际印刷的页码。"
        ),
        scoped_fields=((VEHICLE_SCOPE, _REGISTRATION_FIELDS),),
        scoped_guidance=(
            (
                VEHICLE_SCOPE,
                (
                    "读取注册登记机动车信息栏的车辆识别代号、车辆型号、车辆品牌、"
                    "‘发动机型号’和‘燃料种类’，以及排量或功率栏的千瓦数。"
                    "登记证书没有‘品牌型号’栏目，vehicle.brand_model 必须由‘车辆品牌’栏"
                    "直接接上‘车辆型号’栏组成，中间不加空格或符号"
                    "（车辆品牌‘解放牌’＋车辆型号‘CA4250P66M25T1A1E6’→‘解放牌CA4250P66M25T1A1E6’）。"
                    "vehicle.model_code 只输出‘车辆型号’栏的型号代码本身，不含中文品牌名。"
                    "第 11 项‘发动机号码’绝不能作为发动机型号；车辆识别代号不能读取车辆型号。"
                    "排放标准只在证件明确印刷‘国四/国五/国六’或‘Ⅳ/Ⅴ/Ⅵ’时写入 vehicle.emission_standard。"
                    "registration.covered_pages 只输出图片页脚实际印刷的页码数组，"
                    "例如页脚同时出现‘第1页’和‘第2页’时输出 [1,2]，不要按上传顺序推测。"
                ),
            ),
        ),
        hints=("登记证书", "机动车登记证", "登记证", "证件照", "车源资料", "车辆资料"),
        scoped_vehicle_fields=(),
    ),
    MaterialDeclaration(
        document_type="vehicle_nameplate",
        display_name="车辆铭牌",
        fields=_NAMEPLATE_FALLBACK,
        guidance=(
            "识别车身或驾驶室内的车辆铭牌，读取车辆识别代号、整车型号、发动机型号、"
            "发动机功率和排放标准。"
        ),
        scoped_fields=((VEHICLE_SCOPE, _NAMEPLATE_FIELDS),),
        scoped_guidance=(
            (
                VEHICLE_SCOPE,
                (
                    "读取铭牌上打刻或印刷的车辆识别代号、整车型号、发动机型号、"
                    "发动机功率（千瓦）和排放标准。"
                    "铭牌没有页码和轴数，不要为不存在的栏目写入字段；"
                    "车辆识别代号不能读取底盘号以外的其他编号；"
                    "排放标准只在铭牌明确标注‘国四/国五/国六’或‘Ⅳ/Ⅴ/Ⅵ’时写入；"
                    "vehicle.model_code 只读取整车型号代码本身，不含中文品牌名。"
                    "vehicle.brand_model 按铭牌印刷的品牌与整车型号原样拼接，中间不加空格或符号。"
                ),
            ),
        ),
        hints=("车辆铭牌", "铭牌", "证件照", "车源资料", "车辆资料"),
        scoped_vehicle_fields=(),
    ),
)

VEHICLE_SOURCE_MATERIAL_POLICY = MaterialPolicy(
    mode="enforce",
    materials=(
        MaterialRequirement(
            "vehicle_license",
            VEHICLE_SCOPE,
            display_name="机动车行驶证",
        ),
        MaterialRequirement(
            "registration_certificate",
            VEHICLE_SCOPE,
            required_pages=(1, 2),
            display_name="机动车登记证书第 1、2 页",
            alternative_group=SUPPLEMENT_GROUP,
        ),
        MaterialRequirement(
            "vehicle_nameplate",
            VEHICLE_SCOPE,
            display_name="车辆铭牌",
            alternative_group=SUPPLEMENT_GROUP,
        ),
    ),
)


def _material_routes() -> tuple[RouteDeclaration, ...]:
    """材料字段 → 领域字段：同名直通，另加行驶证车辆类型的双目标映射。"""
    generated = [
        RouteDeclaration(material.document_type, field, (field,), scope=VEHICLE_SCOPE)
        for material in VEHICLE_SOURCE_MATERIALS
        for field in material.fields_for_scope(VEHICLE_SCOPE)
        if field.startswith(f"{VEHICLE_SCOPE}.")
        and field not in _ROUTE_EXCEPTIONS.get(material.document_type, frozenset())
    ]
    return tuple(generated) + (
        # 行驶证的“车辆类型”既支撑页面“车辆类型”（模糊归类为业务大类），
        # 也支撑页面“行驶证车辆类型”（票面原文），两个控件归一化方式不同。
        RouteDeclaration(
            "vehicle_license",
            "vehicle.type",
            ("vehicle.type", "vehicle.license_vehicle_type"),
            scope=VEHICLE_SCOPE,
        ),
    )


VEHICLE_SOURCE_PACK = BusinessExtensionPack(
    business_type="vehicle_source",
    title="车源审核",
    scopes=(VEHICLE_SCOPE,),
    sections=(SectionDeclaration(VEHICLE_SCOPE, VEHICLE_SECTION_TITLE),),
    fields=(
        FieldDeclaration(
            key="vehicle.type",
            writable=True,
            label="车辆类型",
            section=VEHICLE_SCOPE,
            aliases=("车辆类型",),
            page_section="unknown",
            mode="PARALLEL",
            sources=("vehicle_license", "registration_certificate"),
            normalizer="vehicle_type",
            required=True,
        ),
        FieldDeclaration(
            key="vehicle.plate_no",
            writable=True,
            label="车牌号码",
            section=VEHICLE_SCOPE,
            aliases=("车牌号码", "号牌号码"),
            page_section="unknown",
            mode="SINGLE_SOURCE",
            sources=("vehicle_license",),
            normalizer="plate",
            required=True,
        ),
        FieldDeclaration(
            key="vehicle.vin",
            writable=True,
            label="VIN",
            section=VEHICLE_SCOPE,
            aliases=("VIN", "车架号", "车辆识别代号"),
            page_section="unknown",
            mode="PARALLEL",
            sources=("vehicle_license", "registration_certificate", "vehicle_nameplate"),
            normalizer="vin",
            # 行驶证是唯一基准；辅助材料按后 8 位比对，减少打刻不清导致的误报。
            authority=(
                AuthorityRule("image", "行驶证", ("vehicle_license",), required=True),
                AuthorityRule("page", "申请页面"),
                AuthorityRule(
                    "image",
                    "登记证书或车辆铭牌",
                    ("registration_certificate", "vehicle_nameplate"),
                    "suffix8",
                ),
            ),
            required=True,
        ),
        FieldDeclaration(
            key="vehicle.engine_no",
            writable=True,
            label="发动机号",
            section=VEHICLE_SCOPE,
            aliases=("发动机号", "发动机号码"),
            page_section="unknown",
            mode="SINGLE_SOURCE",
            sources=("vehicle_license",),
            normalizer="identifier",
            required=True,
        ),
        FieldDeclaration(
            key="vehicle.license_vehicle_type",
            writable=True,
            label="行驶证车辆类型",
            section=VEHICLE_SCOPE,
            aliases=("行驶证车辆类型",),
            page_section="unknown",
            mode="SINGLE_SOURCE",
            sources=("vehicle_license",),
            normalizer="text",
            required=True,
        ),
        FieldDeclaration(
            key="vehicle.brand_model",
            writable=True,
            label="品牌型号",
            section=VEHICLE_SCOPE,
            aliases=("品牌型号",),
            page_section="unknown",
            mode="PARALLEL",
            sources=("vehicle_license", "registration_certificate", "vehicle_nameplate"),
            normalizer="identifier",
            authority=(
                AuthorityRule("image", "行驶证", ("vehicle_license",), required=True),
                AuthorityRule("page", "申请页面"),
                AuthorityRule(
                    "image",
                    "登记证书或车辆铭牌",
                    ("registration_certificate", "vehicle_nameplate"),
                ),
            ),
            required=True,
        ),
        FieldDeclaration(
            key="vehicle.usage_nature",
            writable=True,
            label="使用性质",
            section=VEHICLE_SCOPE,
            aliases=("使用性质",),
            page_section="unknown",
            mode="SINGLE_SOURCE",
            sources=("vehicle_license",),
            normalizer="text",
            required=True,
        ),
        FieldDeclaration(
            key="vehicle.registration_date",
            writable=True,
            label="注册日期",
            section=VEHICLE_SCOPE,
            aliases=("注册日期",),
            page_section="unknown",
            mode="SINGLE_SOURCE",
            sources=("vehicle_license",),
            normalizer="date",
            required=True,
        ),
        FieldDeclaration(
            key="vehicle.issue_date",
            writable=True,
            label="发证日期",
            section=VEHICLE_SCOPE,
            aliases=("发证日期",),
            page_section="unknown",
            mode="SINGLE_SOURCE",
            sources=("vehicle_license",),
            normalizer="date",
            # 时效：发证日期必须在审核日往前推 90 天内。超期交人工复核，不自动
            # 判不通过。注册日期不受时效限制——业务只要求发证日期。
            max_age_days=90,
            required=True,
        ),
        FieldDeclaration(
            key="vehicle.owner",
            writable=True,
            label="所有人",
            section=VEHICLE_SCOPE,
            aliases=("所有人",),
            page_section="unknown",
            mode="SINGLE_SOURCE",
            sources=("vehicle_license",),
            normalizer="party_name",
            required=True,
        ),
        FieldDeclaration(
            key="vehicle.model",
            writable=True,
            label="车型",
            section=VEHICLE_SCOPE,
            aliases=("车型",),
            page_section="unknown",
            # 页面下拉值由 vehicle_model_consistency 规则解析后与材料比对。
            # 该字段不参与常规字段比对（mode 为 None），它的核验条目来自
            # 规则结论的投影，见 field_check_bindings。
        ),
        FieldDeclaration(
            key="vehicle.fuel_type",
            writable=True,
            label="燃料种类",
            section=VEHICLE_SCOPE,
            aliases=("燃料种类",),
            page_section="unknown",
            mode="PARALLEL",
            # 行驶证和登记证书都有“燃料种类”栏，登记证书上是第 13 项。
            # 登记证书与铭牌二选一，所以这里允许单一来源成立。
            sources=("vehicle_license", "registration_certificate"),
            normalizer="fuel_type",
            required=True,
        ),
        FieldDeclaration(
            key="vehicle.engine_model",
            writable=True,
            label="发动机型号",
            section=VEHICLE_SCOPE,
            aliases=("发动机型号",),
            page_section="unknown",
            mode="PARALLEL",
            # 行驶证上只有“发动机号码”，没有“发动机型号”，所以基准是
            # 登记证书或车辆铭牌；行驶证不在这条链上。
            sources=("registration_certificate", "vehicle_nameplate"),
            normalizer="engine_model",
            authority=(
                AuthorityRule(
                    "image",
                    "登记证书或车辆铭牌",
                    ("registration_certificate", "vehicle_nameplate"),
                    required=True,
                ),
                AuthorityRule("page", "申请页面"),
            ),
            required=True,
        ),
        # 以下三个字段只供 vehicle_model_consistency 规则读取，不从页面采集，
        # 也不参与字段级比对（mode 为 None）。
        FieldDeclaration(
            key="vehicle.model_code",
            label="整车型号",
            section=VEHICLE_SCOPE,
            page_section="unknown",
        ),
        FieldDeclaration(
            key="vehicle.power_kw",
            label="发动机功率",
            section=VEHICLE_SCOPE,
            page_section="unknown",
        ),
        FieldDeclaration(
            key="vehicle.emission_standard",
            label="排放标准",
            section=VEHICLE_SCOPE,
            page_section="unknown",
        ),
    ),
    materials=VEHICLE_SOURCE_MATERIALS,
    # 页面上的图片分组标题 → 业务分区。「证件照」是唯一的上传区域：行驶证
    # 正反面、登记证书第 1、2 页和第 3、4 页、车辆铭牌都在这一组里，传几张由
    # 审核员按业务要求决定，具体是哪份材料由图片下方的小标题区分。
    # 「车况承诺书」和「车况照片」归到 other，前端选图时直接排除。
    page_groups=(
        PageGroupDeclaration("证件照", VEHICLE_SCOPE, "证件照"),
        PageGroupDeclaration("行驶证", VEHICLE_SCOPE, "行驶证"),
        PageGroupDeclaration("行驶证信息", VEHICLE_SCOPE, "行驶证信息"),
        PageGroupDeclaration("行驶证资料", VEHICLE_SCOPE, "行驶证资料"),
        PageGroupDeclaration("登记证书", VEHICLE_SCOPE, "登记证书"),
        PageGroupDeclaration("机动车登记证书", VEHICLE_SCOPE, "机动车登记证书"),
        PageGroupDeclaration("车辆铭牌", VEHICLE_SCOPE, "车辆铭牌"),
        PageGroupDeclaration("铭牌", VEHICLE_SCOPE, "铭牌"),
        PageGroupDeclaration("车源资料", VEHICLE_SCOPE, "车源资料"),
        PageGroupDeclaration("车源信息", VEHICLE_SCOPE, "车源信息"),
        PageGroupDeclaration("车辆资料", VEHICLE_SCOPE, "车辆资料"),
        PageGroupDeclaration("车身与车籍信息", VEHICLE_SCOPE, "车身与车籍信息"),
        PageGroupDeclaration("补充信息", VEHICLE_SCOPE, "补充信息"),
        PageGroupDeclaration("车况承诺书", "other", "车况承诺书"),
        PageGroupDeclaration("车况照片", "other", "车况照片"),
        PageGroupDeclaration("其他资料", "other", "其他资料"),
        PageGroupDeclaration("其他图片", "other", "其他图片"),
    ),
    routes=_material_routes(),
    regions=(
        # 车源审核不分地区：页面地址是 /vehicle-source，业务配置取默认地区。
        # 没有地区政策，因此不声明 replacement_policy。
        RegionDeclaration(
            Region.DEFAULT,
            admin_paths=("/vehicle-source",),
            # 地址认不出来时的兜底（页面改版换路径）。取审核页自身的标题和
            # 区块名，不要用某个字段名——字段会改版。
            page_anchors=("车源审核", "行驶证信息"),
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
            "vehicle_model_consistency",
            kind="RULE",
            stage="POST_COMPARE",
            failure_policy="MANUAL_REVIEW",
        ),
    ),
    binding_declarations=(
        CapabilityBinding("material_completeness"),
        CapabilityBinding("vehicle_model_consistency"),
    ),
    # 车型的三条规则结论同时投影成「车型」这一个页面字段的核验条目，
    # 审核员在字段视图里就能看到结论，不必切到页面外核验。
    field_check_bindings=(
        ("VEHICLE-MODEL-POWER", "vehicle.model"),
        ("VEHICLE-MODEL-CODE", "vehicle.model"),
        ("VEHICLE-MODEL-EMISSION", "vehicle.model"),
    ),
    # 车源审核只核对声明的 13 个字段：页面上的售价、底价、是否带挂等控件
    # 没有已配置的核验来源，也不应该出现在审核目录里。
    review_declared_fields_only=True,
    page_interaction=True,
    # 字段级回填：审核员可以把材料值写回页面控件。动作本身由浏览器执行，
    # 这里声明的是"这个业务允许提出写回请求"，页面动作注册表校验它存在。
    page_action_ids=("fill_review_fields",),
    # **只放开文本框**。下拉（车辆类型、使用性质、车型、燃料种类）和日期
    # 控件（注册日期、发证日期）在写回器里还没有验证过，一律交人工填写；
    # 声明为可写的字段渲染成这些控件时，字段条目上不会出现回填按钮。
    writable_control_kinds=("text", "textarea", "number"),
    # 页面指纹锚点。车源页面上没有 `old_vehicle.*` / `application.id`，
    # 不声明的话指纹恒为空串，页面写回和原图定位会被全部拒绝。
    identity_anchors=("vehicle.vin", "vehicle.plate_no", "vehicle.owner"),
    record_identity_fields=("vehicle.vin",),
    material_policy=VEHICLE_SOURCE_MATERIAL_POLICY,
)
