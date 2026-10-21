"""业务审核配置。

主要职责：定义不同业务、地区和版本的字段与规则开关。
修改日期：2026-08-26
"""

from dataclasses import dataclass

from forj_review_service.businesses.material_policies import (
    DEFAULT_RETRY_POLICY,
    MaterialPolicy,
    RetryPolicy,
)
from forj_review_service.businesses.packs.model import BusinessExtensionPack
from forj_review_service.businesses.replacement_policies import ReplacementPolicy
from forj_review_service.capabilities.specs import (
    CapabilityBinding,
    CapabilitySpec,
)
from forj_review_service.contracts.review import (
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
    pack_declaration: BusinessExtensionPack | None = None
    page_id: str = ""

    @property
    def capabilities(self) -> tuple[CapabilitySpec, ...]:
        """Complete capability definitions, compiled before execution."""
        return self.capability_specs

    @property
    def bindings(self) -> tuple[CapabilityBinding, ...]:
        """One explicit declaration per capability; omitted bindings use spec defaults."""
        declared = {item.capability_id: item for item in self.binding_declarations}
        if len(declared) != len(self.binding_declarations):
            raise ValueError("Profile 能力绑定标识重复")
        unknown = declared.keys() - {spec.capability_id for spec in self.capability_specs}
        if unknown:
            raise ValueError("Profile 绑定了未定义能力：" + ", ".join(sorted(unknown)))
        return tuple(
            declared.get(spec.capability_id, CapabilityBinding(spec.capability_id))
            for spec in self.capability_specs
        )

    @property
    def enabled_page_actions(self) -> tuple[str, ...]:
        return self.page_action_ids

    def allows_field_write(self, control_type: str | None) -> bool:
        """该控件类型是否允许审核员写回。

        没声明过控件类型的业务不限制（历史口径）；声明过的业务只放开列出的
        类型——写回器对下拉、日期这类复合控件的验证程度和文本框不同，没验证
        过的一律交人工填写，而不是先写进去再看回读结果。
        """
        if not self.rules_configured:
            return False
        if not self.writable_control_kinds:
            return True
        return str(control_type or "").strip().lower() in self.writable_control_kinds


def build_profile(
    pack: BusinessExtensionPack, region: RegionId, *, page_id: str | None = None
) -> BusinessProfile:
    """从业务声明生成某个地区的 Profile。

    字段、材料、能力和页面动作在地区之间共用，只有地区政策不同。地区政策为
    空时不推导地区政策能力，`capabilities_for` 只返回共用部分。
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


# 一致性审核不分地区：青岛与长春两个页面地址（/consistency-qingdao、
# /consistency-changchun）使用默认地区，
# 由页面识别声明把两个地址都挂到这一条上。规则尚未配置，先立座位。
CONSISTENCY_DEFAULT = BusinessProfile(
    business_type=BusinessType.CONSISTENCY,
    region=Region.DEFAULT,
    version="1.0",
    required_fields=(),
    sections=(),
    rules_configured=False,
    unconfigured_message="一致性审核规则尚未配置，请人工复核",
)
