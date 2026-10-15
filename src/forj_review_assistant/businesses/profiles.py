"""业务审核配置。

主要职责：定义不同业务、地区和版本的字段与规则开关。
修改日期：2026-08-26
修改人：wuyi
"""

from dataclasses import dataclass, replace

from forj_review_assistant.businesses.material_policies import (
    DEFAULT_RETRY_POLICY,
    MaterialPolicy,
    RetryPolicy,
)
from forj_review_assistant.businesses.packs import (
    BUSINESS_PACKS,
    SCRAP_REPLACEMENT_PACK,
    TRANSFER_PACK,
    VEHICLE_SOURCE_PACK,
)
from forj_review_assistant.businesses.replacement_policies import ReplacementPolicy
from forj_review_assistant.capabilities.specs import (
    CapabilityBinding,
    CapabilitySpec,
    ExternalCheckSpec,
)
from forj_review_assistant.contracts.review import (
    BusinessId,
    BusinessType,
    Region,
    RegionId,
    business_identifier,
)


@dataclass(frozen=True)
class SectionDefinition:
    id: str
    title: str
    fields: tuple[str, ...]


@dataclass(frozen=True)
class BusinessProfile:
    business_type: BusinessId
    region: RegionId
    version: str
    required_fields: tuple[str, ...]
    sections: tuple[SectionDefinition, ...]
    rules_configured: bool
    unconfigured_message: str | None = None
    material_policy: MaterialPolicy | None = None
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY
    external_checks: tuple[ExternalCheckSpec, ...] = ()
    rule_groups: tuple[str, ...] = ()
    page_actions: tuple[str, ...] = ()
    page_action_ids: tuple[str, ...] = ()
    # 允许浏览器写回的控件类型；空元组表示不限制（历史口径，见扩展包声明）。
    writable_control_kinds: tuple[str, ...] = ()
    replacement_policy: ReplacementPolicy | None = None
    capability_specs: tuple[CapabilitySpec, ...] = ()
    binding_declarations: tuple[CapabilityBinding, ...] = ()
    page_interaction: bool = False
    # 页面字段 → 投影到它上面的规则检查项。
    field_check_bindings: tuple[tuple[str, str], ...] = ()
    # 「一键验真」的触发字段；None 表示本业务不做验真。由业务声明给出。
    invoice_verification_field: str | None = None
    # 审核字段就是声明的字段；页面上其他控件不进审核目录。
    review_declared_fields_only: bool = False
    pack_declaration: object | None = None
    page_id: str = ""

    @property
    def capabilities(self) -> tuple[CapabilitySpec, ...]:
        # New profiles may declare their complete capability set directly via
        # ``capability_specs``/bindings without using the legacy selector
        # fields. Accept that shape as the canonical form.
        if self.capability_specs and not self.external_checks and not self.rule_groups:
            specs = list(self.capability_specs)
            if self.material_policy and not any(
                spec.capability_id == "material_completeness" for spec in specs
            ):
                specs.append(CapabilitySpec("material_completeness", kind="MATERIAL", stage="INPUT_COVERAGE"))
            return tuple(specs)
        templates = {spec.capability_id: spec for spec in self.capability_specs}
        specs: list[CapabilitySpec] = []
        for external in self.external_checks:
            template = templates.get(external.check_id, CapabilitySpec(
                external.check_id,
                kind="EXTERNAL",
                stage="EVIDENCE",
                dependencies=("old_vehicle",),
                timeout_seconds=60,
            ))
            specs.append(replace(
                template,
                capability_id=external.check_id,
                kind="EXTERNAL",
                required=external.mode == "REQUIRED",
                stage="EVIDENCE",
            ))
        for group in self.rule_groups:
            template = templates.get(group, CapabilitySpec(
                group,
                kind="RULE",
                stage="FINAL_REVIEW" if group == "affiliation_subject" else "POST_COMPARE",
            ))
            specs.append(replace(
                template,
                capability_id=group,
                kind="RULE",
                stage="FINAL_REVIEW" if group == "affiliation_subject" else template.stage,
            ))
        if self.material_policy:
            template = templates.get("material_completeness", CapabilitySpec(
                "material_completeness", kind="MATERIAL", stage="INPUT_COVERAGE"
            ))
            specs.append(replace(
                template,
                capability_id="material_completeness",
                kind="MATERIAL",
                stage="INPUT_COVERAGE",
            ))
        return tuple(specs)

    @property
    def capability_bindings(self) -> tuple[CapabilityBinding, ...]:
        """Stable declarative view consumed by planners and tooling."""
        return tuple(
            CapabilityBinding(spec.capability_id, enabled=True, required=spec.required)
            for spec in self.capabilities
        )

    @property
    def bindings(self) -> tuple[CapabilityBinding, ...]:
        """Canonical capability declarations exposed to the planner.

        The legacy ``external_checks``/``rule_groups`` fields are still
        accepted at the profile boundary for migration, but application code
        should consume this stable binding view.
        """
        generated = self.capability_bindings
        if not self.binding_declarations:
            return generated
        declared_map = {item.capability_id: item for item in self.binding_declarations}
        if len(declared_map) != len(self.binding_declarations):
            raise ValueError("Profile 能力绑定标识重复")
        # Legacy selectors determine requiredness only at this boundary.
        # Explicit enabled/parameters survive partial declarations.
        bindings = []
        for item in generated:
            declared = declared_map.pop(item.capability_id, item)
            if self.external_checks or self.rule_groups:
                declared = replace(declared, required=item.required)
            bindings.append(declared)
        return (*bindings, *declared_map.values())

    @property
    def enabled_page_actions(self) -> tuple[str, ...]:
        """Canonical page action ids declared by this profile.

        ``page_actions`` remains a boundary-only compatibility field for
        historical profile constructors.
        """
        return self.page_action_ids or self.page_actions

    def allows_field_write(self, control_type: str | None) -> bool:
        """该控件类型是否允许审核员写回。

        没声明过控件类型的业务不限制（历史口径）；声明过的业务只放开列出的
        类型——写回器对下拉、日期这类复合控件的验证程度和文本框不同，没验证
        过的一律交人工填写，而不是先写进去再看回读结果。
        """
        if not self.writable_control_kinds:
            return True
        return str(control_type or "").strip().lower() in self.writable_control_kinds





def build_profile(pack, region: RegionId, *, page_id: str | None = None) -> BusinessProfile:
    """从业务声明生成某个地区的 Profile。

    字段、材料、能力和页面动作在地区之间共用，只有地区政策不同。地区政策为
    空时（如车源审核）不推导地区政策能力，`capabilities_for` 只返回共用部分。
    """
    declaration = pack.region(region)
    if declaration is None:
        raise ValueError(f"{pack.business_type} 声明中缺少地区：{region}")
    titles = {section.key: section.title for section in pack.sections}
    return BusinessProfile(
        page_id=page_id or f"{pack.business_type}:{region}",
        pack_declaration=pack,
        business_type=business_identifier(pack.business_type),
        region=declaration.region,
        version=declaration.version,
        required_fields=pack.required_keys(),
        sections=tuple(
            SectionDefinition(key, titles.get(key, key), fields)
            for key, fields in pack.section_field_keys().items()
        ),
        rules_configured=True,
        material_policy=pack.material_policy,
        retry_policy=pack.retry_policy,
        page_action_ids=pack.page_action_ids,
        writable_control_kinds=pack.writable_control_kinds,
        replacement_policy=declaration.replacement_policy,
        capability_specs=pack.capabilities_for(declaration),
        binding_declarations=pack.bindings_for(declaration),
        page_interaction=pack.page_interaction,
        field_check_bindings=pack.field_check_bindings,
        invoice_verification_field=pack.invoice_verification_field,
        review_declared_fields_only=pack.review_declared_fields_only,
    )


def build_scrap_profile(region: Region) -> BusinessProfile:
    """从业务声明生成某个地区的报废置换 Profile。"""
    return build_profile(SCRAP_REPLACEMENT_PACK, region)


SCRAP_REPLACEMENT_QINGDAO = build_scrap_profile(Region.QINGDAO)

# 车源审核不分地区，页面地址是 /vehicle-source，配置取默认地区。
VEHICLE_SOURCE_DEFAULT = build_profile(VEHICLE_SOURCE_PACK, Region.DEFAULT)

# 一致性审核不分地区：青岛与长春两个页面地址（/consistency-qingdao、
# /consistency-changchun）用的是同一套规则，因此和车源审核一样取默认地区，
# 由页面识别声明把两个地址都挂到这一条上。规则尚未配置，先立座位。
CONSISTENCY_DEFAULT = BusinessProfile(
    business_type=BusinessType.CONSISTENCY,
    region=Region.DEFAULT,
    version="1.0",
    required_fields=(),
    sections=(),
    rules_configured=False,
    unconfigured_message="一致性审核规则尚未配置，请人工复核",
    external_checks=(),
    rule_groups=(),
    page_actions=(),
)

SCRAP_REPLACEMENT_CHANGCHUN = build_scrap_profile(Region.CHANGCHUN)

# 过户审核与一致性审核同址（两个地址都一样），靠列表页状态筛选进到各自的页面。
# 同样不分地区，因此和车源审核一样取默认地区。
TRANSFER_DEFAULT = build_profile(TRANSFER_PACK, Region.DEFAULT)

BUSINESS_PROFILES = (
    *(build_profile(pack, region.region) for pack in BUSINESS_PACKS.values() for region in pack.regions),
    CONSISTENCY_DEFAULT,
)
