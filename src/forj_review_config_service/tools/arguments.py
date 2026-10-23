"""One strict runtime contract per tool; the catalog uses these same schemas."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from forj_review_config_service.contracts.models import ConfigurationError, DraftOperation

Text = Annotated[str, Field(min_length=1, max_length=16000)]
Identifier = Annotated[str, Field(min_length=1, max_length=200)]


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ConfigurationArguments(Arguments):
    page_id: Identifier
    field_keys: list[Identifier] = Field(
        default_factory=list,
        max_length=30,
        description="已知目标字段时填写；只返回这些字段及其材料、实际依赖和维护限制",
    )
    material_types: list[Identifier] = Field(
        default_factory=list,
        max_length=30,
        description="需要调查的新证据材料类型；例如invoice。与field_keys一起填写可一次查齐来源说明",
    )


class AnalyzeArguments(Arguments):
    sample_ids: list[Identifier] = Field(min_length=1, max_length=32)
    focus: str = Field(max_length=16000)


class RegionArguments(Arguments):
    sample_id: Identifier
    region: Text


class ReferenceArguments(Arguments):
    fields: list[Identifier] = Field(
        default_factory=list,
        max_length=500,
        description="复制get_page_snapshot.fields中真实candidate_id，不是配置字段key；只引用实际需要的现场",
    )
    samples: list[Identifier] = Field(
        default_factory=list,
        max_length=32,
        description="复制get_page_snapshot.samples中真实sample_id；不代表材料必传",
    )


class EditArguments(Arguments):
    expected_revision: int = Field(ge=0)
    operations: list[DraftOperation] = Field(min_length=1, max_length=100)


class DecisionArguments(Arguments):
    requirement_id: Identifier | None = None
    candidate_id: Identifier | None = None
    field_key: Identifier | None = None
    sample_id: Identifier | None = None
    material: Identifier | None = None
    status: Literal["RESOLVED", "NEEDS_INPUT", "UNSUPPORTED"]
    reason: Text
    evidence_refs: list[Identifier] = Field(min_length=1, max_length=500)


class ReplayArguments(Arguments):
    case_set_id: Identifier


class QuestionArguments(Arguments):
    question: Text
    options: list[Text] = Field(max_length=30)


class PropertyAssertion(Arguments):
    target_kind: Literal["field", "material", "page"]
    target_key: str = ""
    property: str = Field(default="", pattern=r"^(?:/[^~]*(?:~[01][^~]*)*)?$", max_length=500)
    operator: Literal["equals", "exists", "absent"] = "equals"
    expected: JsonValue = None


class RequirementGoal(Arguments):
    text: Text
    target_refs: list[Identifier] = Field(default_factory=list, max_length=100)
    acceptance: list[Text] = Field(default_factory=list, max_length=30)
    assertions: list[PropertyAssertion] = Field(default_factory=list, max_length=50)
    depends_on: list[Identifier] = Field(default_factory=list, max_length=100)


class RequirementArguments(Arguments):
    message_id: Identifier = Field(
        description="pending_messages 中的真实消息 ID，直接复制；不是需求 ID"
    )
    action: Literal["ACCEPT", "REPLACE", "WITHDRAW", "QUERY"] = Field(
        description="ACCEPT 新目标；REPLACE 更正旧目标；WITHDRAW 撤回旧目标；QUERY 仅查询"
    )
    requirement_ids: list[Identifier] = Field(
        default_factory=list,
        max_length=100,
        description="仅 REPLACE/WITHDRAW 填已有 ACTIVE 原目标 ID；ACCEPT/QUERY 必须为空，不能填写 message_id",
    )
    acceptance: list[Text] = Field(default_factory=list, max_length=30)
    goals: list[RequirementGoal] = Field(
        default_factory=list,
        max_length=50,
        description="一句话可拆多目标；更正只替换指定目标；属性预期必须来自明确业务要求",
    )


class RouteArguments(Arguments):
    requirement_ids: list[Identifier] = Field(
        default_factory=list,
        max_length=100,
        description="本次调查覆盖的有效目标；空列表表示全部有效目标。混合配置/代码目标必须分别调查，不能静默只发布一部分",
    )
    route: Literal["CONFIG", "LOCAL_CODE"]
    reason: Text
    evidence_refs: list[Identifier] = Field(
        min_length=1,
        max_length=50,
        description="复制get_configuration与get_configuration_capabilities结果中的evidence_id；不是供应商最初生成的tool call ID",
    )
    operations: list[DraftOperation] = Field(
        default_factory=list,
        max_length=100,
        description="CONFIG 必须提供拟执行的完整操作，后端复用实际维护校验预检，不修改草稿；LOCAL_CODE 留空",
    )


class DraftArguments(Arguments):
    field_keys: list[Identifier] = Field(default_factory=list, max_length=30)


class ResultArguments(Arguments):
    tool_result_id: Identifier
    pointer: str = Field(
        default="",
        max_length=1000,
        pattern=r"^(?:/(?:[^~]|~[01])*)*$",
        description="JSON Pointer；先按目录选择子树，例如 /editable/materials/0 或 /schema/$defs/FieldConfig，避免整页翻读",
    )
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=5000, ge=1, le=12000)


TOOL_ARGUMENTS = {
    name: Arguments
    for name in (
        "get_page_snapshot",
        "find_templates",
        "get_configuration_capabilities",
        "inspect_candidate",
        "validate_candidate",
        "request_collection_trial",
        "submit_candidate",
    )
}
TOOL_ARGUMENTS.update(
    get_configuration=ConfigurationArguments,
    analyze_materials=AnalyzeArguments,
    inspect_material_region=RegionArguments,
    edit_draft=EditArguments,
    record_decision=DecisionArguments,
    run_replay=ReplayArguments,
    ask_user=QuestionArguments,
)
TOOL_ARGUMENTS.update(manage_requirements=RequirementArguments, assess_route=RouteArguments)
TOOL_ARGUMENTS.update(read_draft=DraftArguments, read_tool_result=ResultArguments)
TOOL_ARGUMENTS.update(reference_page_items=ReferenceArguments)


def validate_arguments(name: str, args) -> dict:
    if name not in TOOL_ARGUMENTS:
        raise ConfigurationError("UNKNOWN_TOOL", "未注册的工具", 422)
    try:
        return TOOL_ARGUMENTS[name].model_validate(args).model_dump(exclude_none=True)
    except ValidationError as exc:
        # Do not persist Pydantic's input values, which may contain private page data.
        issues = [".".join(map(str, e["loc"])) + ":" + e["type"] for e in exc.errors()]
        raise ConfigurationError(
            "INVALID_ARGUMENTS", "工具参数不符合契约：" + "; ".join(issues)[:1500], 422
        ) from exc


def argument_schema(name: str) -> dict:
    schema = TOOL_ARGUMENTS[name].model_json_schema()
    # Inline references so compatible tool providers receive self-contained schemas.
    definitions = schema.pop("$defs", {})

    def inline(value):
        if isinstance(value, list):
            return [inline(v) for v in value]
        if not isinstance(value, dict):
            return value
        if value.get("$ref", "").endswith("/JsonValue"):
            return {}  # Arbitrary JSON values are validated by DraftOperation at runtime.
        if "$ref" in value:
            return inline(definitions[value["$ref"].rsplit("/", 1)[1]])
        return {k: inline(v) for k, v in value.items() if k != "title"}

    return inline(schema)
