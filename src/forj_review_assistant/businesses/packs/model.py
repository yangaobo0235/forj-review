"""业务扩展包：一个审核业务的声明式描述。

字段标签、证据策略、提取白名单、材料路由和浏览器采集元数据均从声明派生。
原生 Python 业务包和声明式页面配置编译器共享此不可变运行时模型；本模块不
负责读取配置文件、执行模型调用或操作页面 DOM。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from forj_review_assistant.businesses.material_policies import (
    DEFAULT_RETRY_POLICY,
    MaterialPolicy,
    RetryPolicy,
)
from forj_review_assistant.businesses.replacement_policies import ReplacementPolicy
from forj_review_assistant.capabilities.specs import CapabilityBinding, CapabilitySpec
from forj_review_assistant.contracts.review import RegionId

EvidenceMode = Literal[
    "SYSTEM", "SINGLE_SOURCE", "AVAILABLE_EVIDENCE", "PARALLEL", "PAGE_AUXILIARY", "DERIVED"
]


@dataclass(frozen=True)
class AuthorityRule:
    """证据权威链上的一环。

    多条规则按顺序构成权威链：**链首来源是唯一比较基准**，其余来源是比对
    目标，各自按自己的 `match` 窗口与基准比对。链首缺失时结论降级为人工
    复核，不会退让给链上的下一条来源——这正是"最高标准"的含义。

    权威由业务声明，不由票数推断；因此新增字段只需声明权威链，不必再写
    一个专用的裁决函数。`label` 只用于生成面向审核员的理由文本。
    """

    source: Literal["qr_page", "page", "image"]
    label: str
    document_types: tuple[str, ...] = ()
    match: Literal["full", "suffix8"] = "full"
    # 该来源缺失时直接人工复核（例如页面值缺失就没有可比对的对象）。
    required: bool = False

    def __post_init__(self) -> None:
        if self.source not in ("qr_page", "page", "image"):
            raise ValueError(f"未知权威来源：{self.source}")
        if self.match not in ("full", "suffix8"):
            raise ValueError(f"未知比较窗口：{self.match}")


@dataclass(frozen=True)
class FieldDeclaration:
    """一个字段的全部声明。"""

    key: str
    label: str
    # Profile 里的展示分区。与 `page_section` 是两件事：这里决定后端把字段
    # 归到哪个分区，`page_section` 决定它在页面 DOM 的哪个区域。
    section: str = ""
    # 页面上的中文别名，供前端 DOM 采集使用。第一个是页面上最常见的写法。
    aliases: tuple[str, ...] = ()
    # 字段在页面 DOM 中所属的区域；未识别时为 "unknown"。
    page_section: str = "unknown"
    # 采集时必须落在 `page_section` 指定的区域内，否则判定为歧义。
    section_required: bool = False
    # 该字段在页面上是可核验的控件（False 表示只读展示）。
    reviewable: bool = True
    # 证据策略；None 表示该字段只用于展示或写回，不参与材料比对。
    mode: EvidenceMode | None = None
    sources: tuple[str, ...] = ()
    # 多材料来源设为 False 时，所有声明材料都必须提供有效值；页面不替代材料。
    allow_single_evidence: bool = True
    uncertain_requires_review: bool = True
    page_evidence_required: bool = True
    normalizer: str = "default"
    authority: tuple[AuthorityRule, ...] = ()
    # 该页面字段与另一个材料字段共用同一份证据策略（如发票代码与号码双值）。
    material_field: str | None = None
    # 该字段进入 Profile 的必审清单。
    required: bool = False
    # 是否允许审核员把这个字段的值写回页面控件。默认不允许：写回是不可逆的
    # 页面操作，每个字段都要单独声明。浏览器侧还有字段白名单和控件类型两道闸，
    # 声明为可写不等于页面上一定会出现回填按钮。
    writable: bool = False
    # 日期时效：字段的日期必须落在「审核日往前推 N 天」到今天之间（含两端）。
    # None 表示不检查。超出不自动判不通过，而是降为人工复核——时效是政策口径，
    # 边上的一天之差通常要靠人判断。同一个页面在不同日期审核可能得到不同结果，
    # 这是政策本身的性质。
    max_age_days: int | None = None
    locator_name: str = ""
    locator_id: str = ""
    occurrence: int = 1


@dataclass(frozen=True)
class MaterialDeclaration:
    """一类材料的声明。"""

    document_type: str
    display_name: str
    fields: tuple[str, ...] = ()
    guidance: str = ""
    # 业务范围（业务分区）对白名单、读取指引和显示名的覆写。
    scoped_fields: tuple[tuple[str, tuple[str, ...]], ...] = ()
    scoped_guidance: tuple[tuple[str, str], ...] = ()
    # 一份材料被多个业务共用时（如行驶证），不同业务的称呼可能不同，
    # 识别提示词里的称呼按业务分区覆写，避免车源审核的行驶证提示词
    # 沿用报废置换的“行驶证或车辆资料”。
    scoped_names: tuple[tuple[str, str], ...] = ()
    # 页面分组文字，供前端识别该材料的图片区域。
    hints: tuple[str, ...] = ()
    # 该材料允许出现的业务分区；空元组表示不限制。
    allowed_scopes: tuple[str, ...] = ()
    # 该材料不属于业务分区（如营业执照、身份证）：字段按材料自身白名单原样
    # 通过，不参与旧车/新车的分区判定。
    scope_independent: bool = False
    # 该材料上跟随页面分区落位的字段后缀：材料写作 vehicle.X（或旧/新前缀）
    # 时落到 {当前分区}.X。只列出的后缀跟随分区；未列出且没有显式路由规则的
    # 字段一律丢弃，避免材料上的无关字段冒充领域字段。
    scoped_vehicle_fields: tuple[str, ...] = ()
    # 上传槽位：(业务分区, 组内序号)。页面上同一分区的材料按固定顺序排列，
    # 类型识别不确定时用槽位兜底；前后端共用这一份，不要各写一遍。
    slots: tuple[tuple[str, int], ...] = ()
    # 票面只有一个号码的材料（数电发票）：`(号码字段, 派生字段)`。
    #
    # 数电机动车销售发票和二手车销售统一发票票面都只有一个「数电号码」，
    # 没有单独的「发票代码」；但审核页面上有两个控件。这里声明"代码由号码
    # 派生"，模型只提取一次号码，另一个字段由确定性兼容层补齐——让模型分别
    # 生成两个值，它会给出两个互相冲突的答案，而且冲突是它自己造出来的。
    # None 表示该材料没有这种关系。
    derived_field: tuple[str, str] | None = None

    def fields_for_scope(self, scope: str) -> tuple[str, ...]:
        for key, fields in self.scoped_fields:
            if key == scope:
                return fields
        return self.fields

    def guidance_for_scope(self, scope: str) -> str:
        for key, guidance in self.scoped_guidance:
            if key == scope:
                return guidance
        return self.guidance

    def name_for_scope(self, scope: str) -> str:
        for key, name in self.scoped_names:
            if key == scope:
                return name
        return self.display_name


@dataclass(frozen=True)
class RouteDeclaration:
    """材料字段 → 领域字段的路由规则。

    `scope` 为空表示适用于该材料的所有分区；`targets` 里的 `{scope}` 会被
    当前分区替换。同一条规则可以把一个材料字段映射到多个领域字段
    （例如发票购买方名称同时支撑新车所有人和客户名称）。
    """

    document_type: str
    source_field: str
    targets: tuple[str, ...]
    scope: str = ""
    group_order: int | None = None


@dataclass(frozen=True)
class SectionDeclaration:
    """PageProfile 里的一个字段分区。"""

    key: str
    title: str


@dataclass(frozen=True)
class CompositeFieldDeclaration:
    """页面上的组合字段：两个控件先互相校验，再一起与材料比较。

    例：发票代码与发票号码、新车车架号与 OCR 新车车架号。这两个控件描述的是
    同一件事，页面自己填岔了就没有可比对的对象，所以必须先查页面内部一致性。

    **字段键是业务知识**，声明在扩展包里。两个业务各有一对发票代码/号码，
    键并不相同，写死在引擎里的表就得跟着业务长。
    """

    check_id: str
    label: str
    primary_field: str
    secondary_field: str
    # 与材料比较时使用哪一个字段的策略（归一化方式和证据来源都取它的）。
    material_field: str
    primary_label: str
    secondary_label: str


@dataclass(frozen=True)
class PageGroupDeclaration:
    """页面上的图片分组标题 → 业务分区。

    同一个分区可能对应多个标题（生产页面改过文案），因此是标题到分区的多对一映射。
    """

    label: str
    scope: str
    title: str
    occurrence: int = 0


@dataclass(frozen=True)
class RegionDeclaration:
    """同一业务在不同地区的差异。

    字段、材料和能力绑定在地区之间共用；只有地区政策、页面地址和版本可能
    不同。地区政策能力 ID 由地区推导（`{region}_replacement_policy`），
    不需要单独声明。
    """

    region: RegionId
    version: str = "1.0"
    replacement_policy: ReplacementPolicy | None = None
    # 该地区审核页面的 URL 路径。
    admin_paths: tuple[str, ...] = ()
    # 页面特征文案：该审核页上**全部**出现的区块标题或字段标签，用于地址
    # 认不出来时（改版、或一个地址挂多个业务）判定这是哪个页面。
    #
    # 只能取详情区的文字。侧边栏菜单、页签标题、底部版权在同一个后台的每张
    # 页面上都有；`document.body.innerText` 把列表页和侧边栏一起收进来，用
    # 这些当特征必然误判。要求全部命中而不是命中任一：只命中的一个词会因为
    # 某次改版从页面上消失，届时不是识别失败，而是**静默认成另一个业务**，
    # 然后拿错的规则去审单子。
    page_anchors: tuple[str, ...] = ()

    def policy_capability_id(self) -> str | None:
        return f"{self.region}_replacement_policy" if self.replacement_policy else None


@dataclass(frozen=True)
class TaskPresentation:
    """Business-owned visibility; it never changes evidence or rule conclusions."""

    hidden_fields: tuple[str, ...] = ()
    hidden_check_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class BusinessExtensionPack:
    """一个业务、地区、版本的完整声明。"""

    business_type: str
    title: str = ""
    # 该业务认可的图片业务分区（如旧车 / 新车）。采集到的图片落在这些
    # 分区之外时，字段一律不路由，转为人工复核。
    scopes: tuple[str, ...] = ()
    sections: tuple[SectionDeclaration, ...] = ()
    fields: tuple[FieldDeclaration, ...] = ()
    materials: tuple[MaterialDeclaration, ...] = ()
    # 页面上的图片分组标题 → 业务分区。
    page_groups: tuple[PageGroupDeclaration, ...] = ()
    # 材料字段 → 领域字段的路由规则。
    routes: tuple[RouteDeclaration, ...] = ()
    # 各地区变体；字段与材料在地区之间共用。
    regions: tuple[RegionDeclaration, ...] = ()
    # 能力绑定与规格（地区无关部分）。地区政策能力由 RegionDeclaration 推导。
    capability_specs: tuple[CapabilitySpec, ...] = ()
    binding_declarations: tuple[CapabilityBinding, ...] = ()
    # 允许的页面动作 ID 与是否启用页内交互。
    page_action_ids: tuple[str, ...] = ()
    page_interaction: bool = False
    # 允许浏览器写回的控件类型（`ReviewFieldSnapshot.control_type` 的取值，
    # 如 text / textarea / number / select / date）。**空元组表示不限制**，
    # 供没有声明过类型的业务沿用历史口径；新增业务应当显式声明，只放开
    # 浏览器写回器验证过的类型，没验证过的一律交人工填写。
    writable_control_kinds: tuple[str, ...] = ()
    # 页面指纹锚点：能唯一标识"这条审核记录"的字段。浏览器用有值的锚点拼出
    # 指纹，页面写回和原图定位都以它为前提；一个强锚点（`*.vin` 或
    # `application.id`）都没有时指纹为空，这些操作全部拒绝执行。
    # **必须按业务声明**：写死成某一个业务的字段名会让别的业务永远拿不到指纹。
    identity_anchors: tuple[str, ...] = ()
    record_identity_fields: tuple[str, ...] = ()
    # 规则检查项 → 页面字段：(check_id, field)。规则结论同时投影成该字段的
    # 核验条目，审核员在字段视图里就能看到结论，不必到页面外核验里找。
    # 一个字段可以绑定多条检查，投影时按最坏状态合并。
    field_check_bindings: tuple[tuple[str, str], ...] = ()
    # 页面上的组合字段（两个控件描述同一件事，先互校再比材料）。
    page_field_composites: tuple[CompositeFieldDeclaration, ...] = ()
    # 「一键验真」的触发字段：该字段的页面值与材料一致时，才值得去点页面上
    # 的验真按钮。None 表示本业务不做验真（那就不该绑定 `verify_invoice`）。
    # 触发字段由业务声明——各业务的发票号码字段键并不相同。
    invoice_verification_field: str | None = None
    # 本业务的审核字段就是声明的字段：页面上其他控件不进审核目录。
    # False（默认）表示未配置核验来源的页面控件仍保留一条“请人工核对”条目，
    # 避免页面控件被静默忽略。
    review_declared_fields_only: bool = False
    # 该业务的材料要求和受控执行预算。
    material_policy: MaterialPolicy | None = None
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY
    presentation: TaskPresentation = TaskPresentation()
    def region(self, region: RegionId) -> RegionDeclaration | None:
        return next((item for item in self.regions if item.region == region), None)

    def check_bindings_by_field(self) -> dict[str, tuple[str, ...]]:
        """页面字段 → 投影到它上面的规则检查项，保持声明顺序。"""
        grouped: dict[str, list[str]] = {}
        for check_id, field in self.field_check_bindings:
            grouped.setdefault(field, []).append(check_id)
        return {field: tuple(check_ids) for field, check_ids in grouped.items()}

    def capabilities_for(self, declaration: RegionDeclaration) -> tuple[CapabilitySpec, ...]:
        """该地区的完整能力规格：共用部分 + 地区政策能力。"""
        specs = list(self.capability_specs)
        policy_id = declaration.policy_capability_id()
        if policy_id is not None:
            specs.append(
                CapabilitySpec(
                    policy_id,
                    kind="RULE",
                    stage="POST_COMPARE",
                    failure_policy="MANUAL_REVIEW",
                )
            )
        return tuple(specs)

    def bindings_for(self, declaration: RegionDeclaration) -> tuple[CapabilityBinding, ...]:
        """该地区的完整能力绑定：共用部分 + 地区政策能力。"""
        bindings = list(self.binding_declarations)
        policy_id = declaration.policy_capability_id()
        if policy_id is not None:
            bindings.append(CapabilityBinding(policy_id))
        return tuple(bindings)

    def field(self, key: str) -> FieldDeclaration | None:
        return next((item for item in self.fields if item.key == key), None)

    def required_keys(self) -> tuple[str, ...]:
        """必审字段，按声明顺序。"""
        return tuple(item.key for item in self.fields if item.required)

    def section_field_keys(self) -> dict[str, tuple[str, ...]]:
        """分区 → 必审字段键。"""
        grouped: dict[str, list[str]] = {}
        for item in self.fields:
            if item.required:
                grouped.setdefault(item.section, []).append(item.key)
        return {key: tuple(value) for key, value in grouped.items()}

    def labels(self) -> dict[str, str]:
        """字段键 → 中文标签。"""
        return {item.key: item.label for item in self.fields}

    def writable_field_keys(self) -> tuple[str, ...]:
        """允许写回的字段键，保持声明顺序。

        这是浏览器写回白名单的唯一来源；浏览器侧还有控件类型闸，声明为可写
        的字段在页面上渲染成下拉/日期控件时同样不允许写回。
        """
        return tuple(item.key for item in self.fields if item.writable)

    def material(self, document_type: str) -> MaterialDeclaration | None:
        return next(
            (item for item in self.materials if item.document_type == document_type),
            None,
        )


def slot_document_types(pack: BusinessExtensionPack) -> dict[tuple[str, int], str]:
    """(业务分区, 组内序号) → 材料类型；用于类型识别不确定时兜底。"""
    return {
        slot: material.document_type
        for material in pack.materials
        for slot in material.slots
    }


def material_types_for_scope(pack: BusinessExtensionPack, scope: str) -> frozenset[str]:
    """业务声明的「属于某个分区的材料类型」集合。

    唯一来源是业务的**材料要求声明**（`MaterialDeclaration.material_policy` 的
    `MaterialRequirement.business_scope`），不是手抄一份名单、也不能从
    `MaterialDeclaration.allowed_scopes` 推导：行驶证和登记证书两个分区都能
    出现，它们的 `allowed_scopes` 是**空元组**（表示不限制），照那个推导只会
    得到回收证明一个，会把「二维码扫哪些图」「哪些图的正文必须留到核验结束」
    这两件事的范围改错，而且不报错。

    调用方需要匹配旧版前端的角色别名时，用 `routing.scope_hint_types`。
    """
    policy = pack.material_policy
    if policy is None:
        return frozenset()
    return frozenset(
        requirement.document_type
        for requirement in policy.materials
        if requirement.business_scope == scope
    )


def validate_pack_references(pack: BusinessExtensionPack) -> None:
    """校验声明内部的交叉引用是否指向真实存在的字段键。

    此前这些引用**没有任何校验**：写错一个字段键不会报错，只会让那条观察值
    在 `field_policy()` 处被静默丢弃——审核员看到的是「该字段没有材料证据」，
    而不是「配置写错了」。两种情况的处理方式完全不同，所以这里在启动期
    直接失败，并指出是哪个声明的哪一处。

    与 `_validate_scope_ownership`（分区归属）同一套做法：宁可服务起不来，
    也不要带着一个静默错误去审单子。
    """
    field_keys = {item.key for item in pack.fields}
    scopes = set(pack.scopes)
    problems: list[str] = []

    def require(field_key: str, where: str) -> None:
        if field_key and field_key not in field_keys:
            problems.append(f"{where} 引用了未声明的字段键「{field_key}」")

    seen_fields: set[str] = set()
    for item in pack.fields:
        if item.key in seen_fields:
            problems.append(f"字段键重复声明：「{item.key}」")
        seen_fields.add(item.key)
        # 该字段与另一个材料字段共用证据策略，两者都必须存在。
        if item.material_field:
            require(item.material_field, f"字段「{item.key}」的 material_field")

    seen_materials: set[str] = set()
    for material in pack.materials:
        if material.document_type in seen_materials:
            problems.append(f"材料类型重复声明：「{material.document_type}」")
        seen_materials.add(material.document_type)
        if material.derived_field is not None:
            number_field, derived = material.derived_field
            require(number_field, f"材料「{material.document_type}」派生关系的号码字段")
            require(derived, f"材料「{material.document_type}」派生关系的派生字段")
        for slot_scope, _ in material.slots:
            if slot_scope not in scopes:
                problems.append(
                    f"材料「{material.document_type}」的上传槽位用了未声明的分区「{slot_scope}」"
                )

    for rule in pack.routes:
        if rule.scope and rule.scope not in scopes:
            problems.append(
                f"材料「{rule.document_type}」的路由限定了未声明的分区「{rule.scope}」"
            )
        # `{scope}` 会按当前分区展开，所以展开后的键都要存在——这正是
        # 「只声明了新车字段、却让路由对旧车也生效」会踩到的坑。
        # 路由声明了 `scope` 时只对该分区生效，不能按全部分区展开。
        expanded_scopes = (rule.scope,) if rule.scope else tuple(scopes)
        targets = {target for target in rule.targets if "{scope}" not in target}
        targets |= {
            target.replace("{scope}", scope)
            for target in rule.targets
            if "{scope}" in target
            for scope in expanded_scopes
        }
        for target in sorted(targets):
            require(target, f"材料「{rule.document_type}」到「{rule.source_field}」的路由目标")

    for composite in pack.page_field_composites:
        require(composite.primary_field, f"组合字段「{composite.check_id}」的 primary_field")
        require(composite.secondary_field, f"组合字段「{composite.check_id}」的 secondary_field")
        require(composite.material_field, f"组合字段「{composite.check_id}」的 material_field")

    for check_id, field_key in pack.field_check_bindings:
        require(field_key, f"检查项「{check_id}」的 field_check_bindings")

    for anchor in pack.identity_anchors:
        require(anchor, "页面指纹锚点 identity_anchors")
    for anchor in pack.record_identity_fields:
        require(anchor, "记录身份 record_identity_fields")
        if anchor not in pack.identity_anchors:
            problems.append(f"记录身份字段「{anchor}」不在 identity_anchors 中")

    for hidden in pack.presentation.hidden_fields:
        require(hidden, "展示策略 hidden_fields")

    if pack.invoice_verification_field:
        require(pack.invoice_verification_field, "发票验真触发字段 invoice_verification_field")

    if pack.material_policy:
        for requirement in pack.material_policy.field_sources:
            require(requirement.field, "材料证据要求 field_sources")

    if problems:
        detail = "\n  - ".join(problems)
        raise ValueError(f"业务声明「{pack.business_type}」存在无效引用：\n  - {detail}")


def build_collect_manifest(pack: BusinessExtensionPack) -> dict[str, object]:
    """生成前端页面采集清单。

    前端据此完成字段别名匹配、图片业务分区判定和材料分组，不再各自维护
    一份表。只包含需要在页面上采集的字段（有中文别名的）。
    """
    return {
        "business_type": pack.business_type,
        # 该业务认定的材料分区。前端据此判断图片是否属于审核材料、以及
        # 哪些分区的图片优先入选。
        "scopes": list(pack.scopes),
        "page_sections": [
            {"key": key, "title": next((section.title for section in pack.sections if section.key == key), key)}
            for key in dict.fromkeys(item.page_section for item in pack.fields if item.page_section != "unknown")
        ],
        "page_groups": [
            {"label": item.label, "scope": item.scope, "title": item.title,
             **({"occurrence": item.occurrence} if item.occurrence else {})}
            for item in pack.page_groups
        ],
        "fields": [
            {
                "key": item.key,
                "label": item.label,
                "aliases": list(item.aliases),
                "section": item.page_section,
                "section_required": item.section_required,
                "reviewable": item.reviewable,
                "locator_name": item.locator_name, "locator_id": item.locator_id, "occurrence": item.occurrence,
            }
            for item in pack.fields
            if item.aliases
        ],
        # 允许写回的字段：**单独一张表，不能从 `fields` 里筛**。上面那张表只
        # 收录有中文别名的采集字段，而挂靠这类只用于操作、没有别名的字段同样
        # 允许写回；按 `fields` 过滤会静默丢掉它们。
        "writable_fields": list(pack.writable_field_keys()),
        # 允许写回的控件类型；空列表表示不限制（历史业务口径）。
        "writable_control_kinds": list(pack.writable_control_kinds),
        # 页面指纹锚点：浏览器用它判断"还是不是同一条审核记录"。
        "identity_anchors": list(pack.identity_anchors),
        "record_identity_fields": list(pack.record_identity_fields),
        "materials": [
            {
                "document_type": item.document_type,
                "label": item.display_name,
                "hints": list(item.hints),
                # 槽位按 material 分组下发，前端据此在类型不确定时兜底。
                "slots": [list(slot) for slot in item.slots],
            }
            for item in pack.materials
        ],
    }

