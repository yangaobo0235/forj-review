"""Authoring contract: a field owns its collection and evidence mapping.

Only data crosses this boundary. Executable handlers, DOM scripts and Python
import paths are deliberately not configuration values.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, model_validator

from forj_review_service.businesses.material_policies import MaterialPolicy, RetryPolicy
from forj_review_service.businesses.packs.model import (
    CompositeFieldDeclaration,
    EvidenceMode,
    QrFieldBinding,
    TaskPresentation,
    TaskProjectorId,
)
from forj_review_service.businesses.page_action_catalog import PageActionId
from forj_review_service.businesses.page_config.action_contract import ActionBinding
from forj_review_service.businesses.replacement_policies import ReplacementPolicy
from forj_review_service.capabilities.specs import CapabilityBinding, CapabilitySpec
from forj_review_service.contracts.review import BusinessId, Identifier, RegionId

Key = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$", max_length=160)
]
PageId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.:-]{0,159}$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Normalizer = Literal[
    "default",
    "text",
    "identifier",
    "plate",
    "vin",
    "invoice_number",
    "vehicle_type",
    "engine_model",
    "fuel_type",
    "fuel_type_exact",
    "jiefang_flag",
    "emission_standard",
    "amount",
    "date",
    "party_name",
    "phone",
]


class Declaration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class EvidenceSource(Declaration):
    material: Identifier
    field: Key
    scope: str = ""
    group_order: int | None = Field(default=None, ge=1)
    comparison: StrictBool = True


class Authority(Declaration):
    source: Literal["qr_page", "page", "image", "capability"]
    label: Text
    document_types: tuple[Identifier, ...] = ()
    match: Literal["full", "suffix8"] = "full"
    required: StrictBool = False


class FieldConfig(Declaration):
    key: Key
    # Every complete page owns the labels and evidence for its fields.
    label: Text | None = None
    section: str = ""
    aliases: tuple[Text, ...] = ()
    page_section: str = "unknown"
    section_required: StrictBool = False
    reviewable: StrictBool = True
    mode: EvidenceMode | None = None
    evidence: tuple[EvidenceSource, ...] = ()
    normalizer: Normalizer = "default"
    authority: tuple[Authority, ...] = ()
    required: StrictBool = True
    writable: StrictBool = False
    allow_single_evidence: StrictBool = True
    uncertain_requires_review: StrictBool = Field(
        default=True,
        description="必须为 true；不确定读值不能证明通过或冲突",
    )
    page_evidence_required: StrictBool = True
    material_field: Key | None = None
    max_age_days: int | None = Field(default=None, ge=1)
    locator_name: str = ""
    locator_id: str = ""
    occurrence: int = Field(default=1, ge=1)
    checks: tuple[Text, ...] = ()
    # Read immutable history; compiler drops this retired projector before runtime.
    task_projector: TaskProjectorId | Literal["owner_type"] = ""

    @model_validator(mode="after")
    def protect_uncertain_evidence(self):
        if not self.uncertain_requires_review:
            raise ValueError("uncertain_requires_review 必须为 true，不能关闭识别不确定性保护")
        return self

    @model_validator(mode="before")
    @classmethod
    def default_evidence_mode(cls, value):
        if isinstance(value, dict) and "mode" not in value and value.get("evidence"):
            return {**value, "mode": "PARALLEL"}
        return value


class MaterialDetails(Declaration):
    """Material routing implementation plus scoped extraction instructions."""

    scoped_extra_fields: tuple[tuple[str, tuple[Key, ...]], ...] = ()
    scoped_guidance: tuple[tuple[str, str], ...] = ()
    scoped_names: tuple[tuple[str, str], ...] = ()
    scope_independent: StrictBool = False
    scoped_vehicle_fields: tuple[str, ...] = ()
    slots: tuple[tuple[str, int], ...] = ()
    derived_field: tuple[str, str] | None = None


class MaterialConfig(Declaration):
    document_type: Identifier
    label: Text | None = None
    hints: tuple[Text, ...] = ()
    guidance: str = ""
    scopes: tuple[Identifier, ...] = ()
    required: StrictBool = False
    minimum_count: int = Field(default=1, ge=1)
    required_pages: tuple[Annotated[int, Field(ge=1)], ...] = ()
    alternative_group: str = ""
    # Additional values consumed only by a registered business rule.
    extra_fields: tuple[Key, ...] = ()
    remove_extra_fields: tuple[Key, ...] = ()
    details: MaterialDetails | None = None

    @model_validator(mode="after")
    def validate_extra_fields(self):
        if set(self.extra_fields) & set(self.remove_extra_fields):
            raise ValueError("同一提取字段不能同时增加和删除")
        for values in (self.extra_fields, self.remove_extra_fields):
            if len(values) != len(set(values)):
                raise ValueError("extra_fields/remove_extra_fields 存在重复标识")
        return self


class Section(Declaration):
    key: Identifier
    title: Text


class PageGroup(Declaration):
    label: Text
    scope: str
    title: Text
    occurrence: int = Field(default=0, ge=0)


class PageSettings(Declaration):
    """Complete declarative development settings; contains no executable handlers."""

    material_policy: MaterialPolicy | None = None
    replacement_policy: ReplacementPolicy | None = None
    capability_specs: tuple[CapabilitySpec, ...] = ()
    binding_declarations: tuple[CapabilityBinding, ...] = ()
    page_field_composites: tuple[CompositeFieldDeclaration, ...] = ()
    qr_field_bindings: tuple[QrFieldBinding, ...] = ()
    invoice_verification_field: Key | None = None
    ignored_image_labels: tuple[str, ...] = ()
    retry_policy: RetryPolicy = RetryPolicy()
    presentation: TaskPresentation = TaskPresentation()

    @model_validator(mode="before")
    @classmethod
    def strict_nested_declarations(cls, value):
        from forj_review_service.businesses.page_config.strict_data import validate_declaration_data

        if isinstance(value, dict):
            for key, item in value.items():
                if key in cls.model_fields:
                    validate_declaration_data(item, cls.model_fields[key].annotation, key)
        return value

    @model_validator(mode="after")
    def validate_settings(self):
        policy = self.material_policy
        requirements = (
            (
                *policy.materials,
                *(
                    r
                    for group in policy.presence_groups
                    for option in group.alternatives
                    for r in option
                ),
            )
            if policy
            else ()
        )
        for item in requirements:
            if item.minimum_count < 1 or any(page < 1 for page in item.required_pages):
                raise ValueError("材料 minimum_count 和 required_pages 必须为正整数")
        if (
            not 0 <= self.retry_policy.semantic_retry_count <= 3
            or not 0 <= self.retry_policy.qr_web_retry_count <= 3
            or self.retry_policy.minimum_retry_window_seconds < 0
        ):
            raise ValueError("重试次数范围 0–3，重试窗口不能为负数")
        return self


class PageDefinition(Declaration):
    schema_version: Literal["2.0"] = "2.0"
    page_id: PageId
    business_type: BusinessId
    region: RegionId = "default"
    version: Text = "1.0"
    active: StrictBool = True
    # Omit the default from page-v2 canonical bytes to preserve existing versions.
    enabled: StrictBool = Field(default=True, exclude_if=lambda value: value is True)
    title: Text
    paths: tuple[Text, ...]
    anchors: tuple[Text, ...] = ()
    review_tab: Text | None = None
    extends: None = None
    scopes: tuple[Identifier, ...] = ()
    sections: tuple[Section, ...] = ()
    page_groups: tuple[PageGroup, ...] = ()
    materials: tuple[MaterialConfig, ...] = ()
    fields: tuple[FieldConfig, ...] = ()
    remove_fields: tuple[Key, ...] = ()
    remove_materials: tuple[Identifier, ...] = ()
    enable_rules: tuple[Identifier, ...] = ()
    disable_rules: tuple[Identifier, ...] = ()
    identity_anchors: tuple[Key, ...] = ()
    record_identity_fields: tuple[Key, ...] = ()
    page_actions: tuple[PageActionId, ...] = ()
    actions: tuple[ActionBinding, ...] = ()
    writable_control_kinds: tuple[Literal["text", "textarea", "number", "select", "date"], ...] = ()
    review_declared_fields_only: StrictBool = True
    settings: PageSettings | None = None

    @model_validator(mode="after")
    def validate_structure(self):
        if self.settings is None or not self.scopes or not self.fields:
            raise ValueError("完整配置必须包含 settings、scopes 和完整 fields")
        from dataclasses import replace

        actions = self.actions
        if "actions" not in self.model_fields_set:
            actions = tuple(
                ActionBinding(action_id=a, handler_id=a, label=a) for a in self.page_actions
            )
            if self.business_type == "vehicle_source":
                from forj_review_service.businesses.page_config.action_contract import (
                    vehicle_model_action,
                )

                actions = (*actions, vehicle_model_action())
        elif "page_actions" in self.model_fields_set and tuple(self.page_actions) != tuple(
            a.action_id for a in actions if a.enabled and a.action_id != "vehicle_model_fill"
        ):
            raise ValueError("page_actions 是 actions 的只读投影，不能独立更改")
        object.__setattr__(self, "actions", actions)
        if len({a.action_id for a in actions}) != len(actions):
            raise ValueError("动作编号重复")
        object.__setattr__(
            self,
            "page_actions",
            tuple(
                a.action_id for a in actions if a.enabled and a.action_id != "vehicle_model_fill"
            ),
        )
        fields_by_key = {f.key: f for f in self.fields}
        for action in actions:
            if action.enabled:
                for mapping in action.mappings:
                    target = fields_by_key.get(mapping.target_field)
                    if target is None or not target.writable:
                        raise ValueError(
                            f"动作映射目标 {mapping.target_field} 必须存在并显式授权 writable"
                        )

        specs = {s.capability_id: s for s in self.settings.capability_specs}
        bindings = tuple(
            replace(b, required=specs[b.capability_id].required)
            if b.required is None and b.capability_id in specs
            else b
            for b in self.settings.binding_declarations
        )
        policy = self.settings.replacement_policy
        if policy:
            policy = replace(policy, region=self.region, version=self.version)
        object.__setattr__(
            self,
            "settings",
            self.settings.model_copy(
                update={
                    "binding_declarations": bindings,
                    "replacement_policy": policy,
                }
            ),
        )
        if any(
            (
                self.extends,
                self.remove_fields,
                self.remove_materials,
                self.enable_rules,
                self.disable_rules,
            )
        ):
            raise ValueError("完整配置不能使用继承、删除清单或增量规则开关")
        for item in self.fields:
            if item.label is None:
                raise ValueError(f"完整字段 {item.key} 必须声明 label")
        if any(item.remove_extra_fields for item in self.materials):
            raise ValueError("完整配置不使用 remove_extra_fields")
        if any(
            item.required
            or item.minimum_count != 1
            or item.required_pages
            or item.alternative_group
            for item in self.materials
        ):
            raise ValueError("完整配置的上传要求只在 settings.material_policy 声明")
        if not self.paths or any(
            not path.startswith("/")
            or "?" in path
            or "#" in path
            or "//" in path
            or (path != "/" and path.endswith("/"))
            for path in self.paths
        ):
            raise ValueError("paths 必须是非空的页面路径，不含查询参数、片段或末尾斜杠")
        for name, values in (
            ("fields", [item.key for item in self.fields]),
            ("materials", [item.document_type for item in self.materials]),
            ("sections", [item.key for item in self.sections]),
            ("scopes", self.scopes),
            ("remove_fields", self.remove_fields),
            ("remove_materials", self.remove_materials),
            ("enable_rules", self.enable_rules),
            ("disable_rules", self.disable_rules),
            ("page_actions", self.page_actions),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} 存在重复标识")
        if set(self.remove_fields) & {item.key for item in self.fields}:
            raise ValueError("同一字段不能同时修改和删除")
        if set(self.remove_materials) & {item.document_type for item in self.materials}:
            raise ValueError("同一材料不能同时修改和删除")
        if set(self.enable_rules) & set(self.disable_rules):
            raise ValueError("同一规则不能同时启用和停用")
        return self
