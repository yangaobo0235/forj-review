"""Versioned contracts; generated content is data, never executable code."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    TypeAdapter,
    field_validator,
    model_validator,
)

from forj_configuration_agent.contracts.pending import Pending


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


def unique_identifiers(values):
    if len(values) != len(set(values)):
        raise ValueError("选择范围存在重复标识")
    return values


def http_url(value: str) -> str:
    parsed = TypeAdapter(HttpUrl).validate_python(value)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("页面和材料地址不能包含账户凭据")
    # Preserve signed URLs and the exact browser page identity.
    return value


class DiscoveredField(Contract):
    candidate_id: str = Field(min_length=1, max_length=200)
    label: str
    section: str = ""
    group_label: str = ""
    control_kind: str = "text"
    editable: bool = False
    value: str | None = None
    locator_id: str = ""
    locator_name: str = ""


class MaterialSample(Contract):
    sample_id: str = Field(min_length=1, max_length=200)
    url: str = Field(max_length=16384)
    label: str = ""
    group: str = ""

    _http_url = field_validator("url")(http_url)


class PageDiscoverySnapshot(Contract):
    schema_version: Literal["1.0"] = "1.0"
    snapshot_id: str
    page_instance_id: str
    page_url: str
    title: str = ""
    structure_digest: str
    fields: list[DiscoveredField] = Field(default_factory=list, max_length=500)
    samples: list[MaterialSample] = Field(default_factory=list, max_length=32)
    diagnostics: list[str] = Field(default_factory=list)
    headings: list[str] = Field(default_factory=list, max_length=100)

    _http_url = field_validator("page_url")(http_url)

    @model_validator(mode="after")
    def unique_snapshot_identifiers(self):
        unique_identifiers([field.candidate_id for field in self.fields])
        unique_identifiers([sample.sample_id for sample in self.samples])
        return self


class CaptureCommand(Contract):
    client_request_id: str = Field(min_length=1, max_length=160)
    extension_id: str = Field(pattern=r"^[a-p]{32}$")
    snapshot: PageDiscoverySnapshot


class CapturedPage(Contract):
    capture_id: str
    extension_id: str
    snapshot: PageDiscoverySnapshot
    created_at: str


class CreateSession(Contract):
    client_request_id: str = Field(min_length=1, max_length=160)
    snapshot: PageDiscoverySnapshot
    capture_id: str | None = None
    page_id: str | None = None
    rollback_version: str | None = None
    selected_fields: list[str] = Field(default_factory=list)
    selected_samples: list[str] = Field(default_factory=list)
    required_samples: list[str] = Field(default_factory=list)

    _unique = field_validator("selected_fields", "selected_samples", "required_samples")(unique_identifiers)


class RevisionCommand(Contract):
    client_request_id: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=0)


class MessageCommand(RevisionCommand):
    text: str = Field(min_length=1, max_length=16000)


class RefreshPageCommand(RevisionCommand):
    snapshot: PageDiscoverySnapshot
    capture_id: str | None = None
    selected_fields: list[str] = Field(default_factory=list)
    selected_samples: list[str] = Field(default_factory=list)
    required_samples: list[str] = Field(default_factory=list)

    _unique = field_validator("selected_fields", "selected_samples", "required_samples")(unique_identifiers)


class DraftOperation(Contract):
    op: Literal[
        "create_page", "set_page_metadata", "upsert_field", "remove_field",
        "upsert_material", "remove_material", "set_rule_bindings", "set_material_requirements",
        "set_collection_binding", "set_write_policy",
    ]
    value: dict[str, JsonValue]


class EditCommand(RevisionCommand):
    operations: list[DraftOperation] = Field(min_length=1, max_length=100)


class InteractionCommand(RevisionCommand):
    answer: str = Field(min_length=1, max_length=16000)


class ConfirmationCommand(RevisionCommand):
    candidate_digest: str
    report_id: str


class BrowserResultCommand(RevisionCommand):
    snapshot_id: str
    page_instance_id: str
    page_url: str
    structure_digest: str
    candidate_digest: str
    fields: dict[str, Annotated[int, Field(strict=True, ge=0)]] = Field(default_factory=dict)
    control_kinds: dict[str, str] = Field(default_factory=dict)
    samples: dict[str, str] = Field(default_factory=dict)
    matched_page_id: str = ""
    diagnostics: list[str] = Field(default_factory=list)


class CaseSetCommand(Contract):
    case_set_id: str
    suite: dict[str, JsonValue]


class CapabilityGap(Contract):
    requirement_id: str | None = None
    missing_capability: str
    evidence_refs: list[str]
    next_step: str = "保留草稿，由开发人员评估规则、Schema、控件或集成扩展"


class SessionRecord(Contract):
    schema_version: Literal["1.0"] = "1.0"
    session_id: str
    revision: int = 0
    activation_only: bool = False
    state: Literal["DRAFTING", "NEEDS_INPUT", "READY_FOR_CONFIRMATION", "SAVED_PENDING_RESTART", "EFFECTIVE", "BLOCKED"] = "DRAFTING"
    turn_status: Literal["COMPLETED", "RUNNING", "QUEUED", "WAITING", "INTERRUPTED", "CANCELLED", "FAILED"] = "COMPLETED"
    turn_id: str | None = None
    snapshot: PageDiscoverySnapshot
    capture_id: str | None = None
    selected_fields: list[str] = Field(default_factory=list)
    selected_samples: list[str] = Field(default_factory=list)
    required_samples: list[str] = Field(default_factory=list)
    baseline_digest: str
    draft: dict[str, JsonValue] = Field(default_factory=dict)
    messages: list[dict[str, JsonValue]] = Field(default_factory=list)
    requirements: list[dict[str, JsonValue]] = Field(default_factory=list)
    decisions: list[dict[str, JsonValue]] = Field(default_factory=list)
    capability_gaps: list[CapabilityGap] = Field(default_factory=list)
    tool_results: dict[str, JsonValue] = Field(default_factory=dict)
    pending: Pending | None = None
    report: dict[str, JsonValue] | None = None
    receipt: dict[str, JsonValue] | None = None
    model_calls: int = 0
    tool_calls: int = 0
    usage: dict[str, int] = Field(default_factory=dict)
    failure_counts: dict[str, int] = Field(default_factory=dict)
    updated_at: str = ""
    error: str | None = None

    @field_validator("pending", mode="before")
    @classmethod
    def migrate_legacy_question(cls, value):
        if isinstance(value, dict) and value.get("kind") == "question":
            # Earlier versions accepted a scalar options value. Keep free-text answers usable.
            options = value.get("options")
            value = {**value, "options": [o for o in options if isinstance(o, str)] if isinstance(options, list) else []}
        return value


class AgentError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)
