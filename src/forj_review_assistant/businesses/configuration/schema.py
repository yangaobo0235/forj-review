"""Authoring contract: a field owns its collection and evidence mapping.

Only data crosses this boundary. Executable handlers, DOM scripts and Python
import paths are deliberately not configuration values.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, model_validator

from forj_review_assistant.businesses.packs.model import EvidenceMode
from forj_review_assistant.contracts.review import BusinessId, Identifier, RegionId

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
    "amount",
    "date",
    "party_name",
]


class Declaration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class ProfileReference(Declaration):
    page_id: PageId
    version: Text


class EvidenceSource(Declaration):
    material: Identifier
    field: Key
    scope: str = ""
    group_order: int | None = Field(default=None, ge=1)


class Authority(Declaration):
    source: Literal["qr_page", "page", "image", "capability"]
    label: Text
    document_types: tuple[Identifier, ...] = ()
    match: Literal["full", "suffix8"] = "full"
    required: StrictBool = False


class FieldConfig(Declaration):
    key: Key
    # Omitted properties inherit from a template. A new field must supply label.
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
    uncertain_requires_review: StrictBool = True
    page_evidence_required: StrictBool = True
    material_field: Key | None = None
    max_age_days: int | None = Field(default=None, ge=1)
    locator_name: str = ""
    locator_id: str = ""
    occurrence: int = Field(default=1, ge=1)
    checks: tuple[Text, ...] = ()


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


class Section(Declaration):
    key: Identifier
    title: Text


class PageGroup(Declaration):
    label: Text
    scope: str
    title: Text
    occurrence: int = Field(default=0, ge=0)


class PageDefinition(Declaration):
    schema_version: Literal["1.0"] = "1.0"
    page_id: PageId
    business_type: BusinessId
    region: RegionId = "default"
    version: Text = "1.0"
    active: StrictBool = True
    title: Text
    paths: tuple[Text, ...]
    anchors: tuple[Text, ...] = ()
    extends: ProfileReference | None = None
    scopes: tuple[Identifier, ...] = ()
    sections: tuple[Section, ...] = ()
    page_groups: tuple[PageGroup, ...] = ()
    materials: tuple[MaterialConfig, ...] = ()
    fields: tuple[FieldConfig, ...] = ()
    remove_fields: tuple[Key, ...] = ()
    enable_rules: tuple[Identifier, ...] = ()
    disable_rules: tuple[Identifier, ...] = ()
    identity_anchors: tuple[Key, ...] = ()
    record_identity_fields: tuple[Key, ...] = ()
    page_actions: tuple[Literal["fill_review_fields"], ...] = ()
    writable_control_kinds: tuple[Literal["text", "textarea", "number"], ...] = ()
    review_declared_fields_only: StrictBool = True

    @model_validator(mode="after")
    def validate_structure(self):
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
            ("enable_rules", self.enable_rules),
            ("disable_rules", self.disable_rules),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} 存在重复标识")
        if set(self.remove_fields) & {item.key for item in self.fields}:
            raise ValueError("同一字段不能同时修改和删除")
        if set(self.enable_rules) & set(self.disable_rules):
            raise ValueError("同一规则不能同时启用和停用")
        return self
