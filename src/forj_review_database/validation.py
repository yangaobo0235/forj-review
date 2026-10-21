"""Bounded completed-validation records shared by persistence adapters."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_code: str
    target_kind: str
    target_key: str
    check_code: str
    status: Literal["PASSED", "FAILED", "ERROR"]
    message: str = Field(max_length=4000)


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checks: list[Check] = Field(max_length=1000)
    collection: dict[str, JsonValue] | None
    replays: list[dict[str, JsonValue]] = Field(max_length=100)
    summary: dict[str, JsonValue]

    @model_validator(mode="after")
    def bounded(self):
        if len(self.model_dump_json().encode()) > 250000:
            raise ValueError("验证报告超过容量限制，请缩小修改范围")
        forbidden = {"draft", "snapshot", "messages", "images", "raw_response", "ocr_text"}

        def visit(value):
            if isinstance(value, dict):
                if value.keys() & forbidden:
                    raise ValueError("验证报告不能包含草稿、原始现场或模型载荷")
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(self.model_dump(mode="json"))
        return self
