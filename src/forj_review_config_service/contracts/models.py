"""Versioned contracts; generated content is data, never executable code."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    PrivateAttr,
    TypeAdapter,
    field_validator,
    model_validator,
)

from forj_review_config_service.contracts.pending import Pending
from forj_review_config_service.contracts.tasks import Requirement, Route, RouteAssessment


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

    source_kind: Literal["http", "unsupported"] = "http"
    section: str = ""
    group_path: list[str] = Field(default_factory=list, max_length=20)
    locator: dict[str, str] = Field(default_factory=dict, max_length=20)

    @model_validator(mode="after")
    def image_source(self):
        if self.source_kind == "unsupported":
            if self.url:
                raise ValueError("不支持的图片来源不能保存二进制或临时地址")
        else:
            http_url(self.url)
        return self


class PageDiscoverySnapshot(Contract):
    schema_version: Literal["2.0"] = "2.0"
    snapshot_id: str
    page_instance_id: str
    page_url: str
    title: str = ""
    detected_page_id: str = ""
    configuration_version: str = Field(default="", max_length=160)
    record_identity: dict[
        Annotated[str, Field(max_length=200)], Annotated[str, Field(max_length=500)]
    ] = Field(default_factory=dict, max_length=20)
    structure_digest: str
    content_digest: str = ""
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


class CreateTask(Contract):
    client_request_id: str = Field(min_length=1, max_length=160)
    snapshot: PageDiscoverySnapshot | None = None
    capture_id: str | None = None
    page_id: str | None = None
    rollback_version: str | None = None
    reference_fields: list[str] = Field(default_factory=list)
    reference_samples: list[str] = Field(default_factory=list)
    initial_message: str | None = Field(default=None, min_length=1, max_length=16000)
    parent_task_id: str | None = None

    _unique = field_validator("reference_fields", "reference_samples")(unique_identifiers)


class RevisionCommand(Contract):
    client_request_id: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=0)
    expected_row_version: int | None = Field(default=None, ge=0)


class MessageCommand(RevisionCommand):
    reference_fields: list[str] = Field(default_factory=list, max_length=500)
    reference_samples: list[str] = Field(default_factory=list, max_length=32)
    text: str = Field(min_length=1, max_length=16000)
    expected_intent_revision: int | None = Field(default=None, ge=0)


class RefreshPageCommand(RevisionCommand):
    snapshot: PageDiscoverySnapshot
    capture_id: str | None = None
    reference_fields: list[str] = Field(default_factory=list)
    reference_samples: list[str] = Field(default_factory=list)

    _unique = field_validator("reference_fields", "reference_samples")(unique_identifiers)


class DraftOperation(Contract):
    op: Literal[
        "upsert_field",
        "remove_field",
        "set_collection_binding",
        "set_page_options",
        "upsert_material",
        "remove_material",
        "set_field_permissions",
        "set_field_comparison",
        "set_material_policy",
        "set_policy_dates",
        "set_presentation",
    ]
    value: dict[str, JsonValue]


class ReferenceCommand(RevisionCommand):
    fields: list[str] = Field(default_factory=list, max_length=500)
    samples: list[str] = Field(default_factory=list, max_length=32)


class LocalReportCommand(RevisionCommand):
    report: dict[str, JsonValue]


class RebaseCommand(RevisionCommand):
    expected_intent_revision: int = Field(ge=0)
    baseline_digest: str
    resolutions: dict[str, Literal["current", "candidate"]] = Field(default_factory=dict)


class SplitCommand(RevisionCommand):
    expected_intent_revision: int = Field(ge=0)
    requirement_ids: list[str] = Field(min_length=1, max_length=100)
    independence_reason: str = Field(min_length=1, max_length=4000)


class AdoptionCommand(RevisionCommand):
    task_id: str
    snapshot_id: str
    page_instance_id: str
    page_url: str
    page_id: str
    version: str
    effective_digest: str
    fields: dict[str, Annotated[int, Field(strict=True, ge=0)]]
    diagnostics: list[str] = Field(default_factory=list)


class EditCommand(RevisionCommand):
    operations: list[DraftOperation] = Field(min_length=1, max_length=100)


class MappingCommand(RevisionCommand):
    fields: dict[str, str] = Field(default_factory=dict)
    materials: dict[str, str] = Field(default_factory=dict)
    requirements: list[str] = Field(default_factory=list)


class ReplayCommand(RevisionCommand):
    case_set_id: str


class InteractionCommand(RevisionCommand):
    reference_fields: list[str] = Field(default_factory=list, max_length=500)
    reference_samples: list[str] = Field(default_factory=list, max_length=32)
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


class TaskRecord(Contract):
    _persisted_message_count: int = PrivateAttr(default=0)
    _persisted_window_size: int = PrivateAttr(default=0)
    _persisted_window_digest: str | None = PrivateAttr(default=None)
    schema_version: Literal["1.0"] = "1.0"
    task_id: str
    revision: int = 0
    row_version: int = 0
    evidence_revision: int = Field(default=0, ge=0)
    intent_revision: int = Field(default=0, ge=0)
    route: Route = "UNDECIDED"
    route_assessment: RouteAssessment | None = None
    waiting_reason: str | None = None
    archive: dict[str, JsonValue] | None = None
    parent_task_id: str | None = None
    baseline_version: str | None = None
    local_report: dict[str, JsonValue] | None = None
    child_tasks: list[dict[str, JsonValue]] = Field(default_factory=list)
    capture_transition: dict[str, JsonValue] | None = None
    preview: dict[str, JsonValue] | None = None
    activation: dict[str, JsonValue] | None = None
    adoption_task: dict[str, JsonValue] | None = None
    adoption: dict[str, JsonValue] | None = None
    investigated_tools: list[str] = Field(default_factory=list)
    activation_only: bool = False
    state: Literal[
        "DRAFTING", "NEEDS_INPUT", "READY_FOR_CONFIRMATION", "PUBLISHED", "BLOCKED", "CLOSED"
    ] = "DRAFTING"
    turn_status: Literal[
        "COMPLETED", "RUNNING", "QUEUED", "WAITING", "INTERRUPTED", "CANCELLED", "FAILED"
    ] = "COMPLETED"
    turn_id: str | None = None
    snapshot: PageDiscoverySnapshot | None = None
    capture_id: str | None = None
    reference_fields: list[str] = Field(default_factory=list)
    reference_samples: list[str] = Field(default_factory=list)
    baseline_digest: str
    draft: dict[str, JsonValue] = Field(default_factory=dict)
    messages: list[dict[str, JsonValue]] = Field(default_factory=list)
    pending_messages: list[str] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    decisions: list[dict[str, JsonValue]] = Field(default_factory=list)
    capability_gaps: list[CapabilityGap] = Field(default_factory=list)
    tool_results: dict[str, JsonValue] = Field(default_factory=dict)
    pending: Pending | None = None
    report: dict[str, JsonValue] | None = None
    receipt: dict[str, JsonValue] | None = None
    model_id: str = ""
    max_model_calls: int = 24
    error_code: str | None = None
    model_calls: int = 0
    tool_calls: int = 0
    usage: dict[str, int] = Field(default_factory=dict)
    failure_counts: dict[str, int] = Field(default_factory=dict)
    updated_at: str = ""
    error: str | None = None


class ConfigurationError(Exception):
    def __init__(
        self, code: str, message: str, status: int = 409, *, diagnostic: dict | None = None
    ):
        self.code, self.message, self.status = code, message, status
        self.diagnostic = diagnostic or {}
        super().__init__(message)
