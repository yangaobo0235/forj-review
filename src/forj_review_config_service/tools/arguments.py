"""One strict runtime contract per tool; the catalog uses these same schemas."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from forj_review_config_service.contracts.models import ConfigurationError, DraftOperation

Text = Annotated[str, Field(min_length=1, max_length=16000)]
Identifier = Annotated[str, Field(min_length=1, max_length=200)]


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ConfigurationArguments(Arguments):
    page_id: Identifier


class AnalyzeArguments(Arguments):
    sample_ids: list[Identifier] = Field(min_length=1, max_length=32)
    focus: str = Field(max_length=16000)


class RegionArguments(Arguments):
    sample_id: Identifier
    region: Text


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


TOOL_ARGUMENTS = {name: Arguments for name in (
    "get_page_snapshot", "find_templates", "get_configuration_capabilities", "inspect_candidate",
    "validate_candidate", "request_collection_trial", "submit_candidate")}
TOOL_ARGUMENTS.update(get_configuration=ConfigurationArguments, analyze_materials=AnalyzeArguments,
    inspect_material_region=RegionArguments, edit_draft=EditArguments, record_decision=DecisionArguments,
    run_replay=ReplayArguments, ask_user=QuestionArguments)


def validate_arguments(name: str, args) -> dict:
    if name not in TOOL_ARGUMENTS:
        raise ConfigurationError("UNKNOWN_TOOL", "未注册的工具", 422)
    try:
        return TOOL_ARGUMENTS[name].model_validate(args).model_dump(exclude_none=True)
    except ValidationError as exc:
        # Do not persist Pydantic's input values, which may contain private page data.
        issues = [".".join(map(str, e["loc"])) + ":" + e["type"] for e in exc.errors()]
        raise ConfigurationError("INVALID_ARGUMENTS", "工具参数不符合契约：" + "; ".join(issues)[:1500], 422) from exc


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
