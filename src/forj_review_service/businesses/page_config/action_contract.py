"""Explicit contracts for implemented browser actions and model mappings."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _ActionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ActionMapping(_ActionContract):
    mapping_id: str
    source_kind: Literal["MODEL_CANDIDATE", "REVIEW_RESULT"]
    source_property: str
    source_option_property: str | None = None
    target_field: str
    order: int = Field(ge=0)
    depends_on: tuple[str, ...] = ()
    converter_id: Literal["text", "positive_number"] = "text"
    missing_policy: Literal["BLOCK", "SKIP", "RESTORE_ORIGINAL"] = "BLOCK"
    wait_condition_id: Literal["none", "options_ready"] = "none"


class ActionBinding(_ActionContract):
    action_id: str
    handler_id: str
    contract_version: Literal["1.0"] = "1.0"
    enabled: bool = True
    label: str
    mappings: tuple[ActionMapping, ...] = ()

    @model_validator(mode="after")
    def supported_action(self):
        if self.action_id != self.handler_id or self.handler_id not in {
            "fill_review_fields",
            "verify_invoice",
            "set_affiliation",
            "vehicle_model_fill",
        }:
            raise ValueError("未注册的动作处理器")
        if self.handler_id != "vehicle_model_fill" and self.mappings:
            raise ValueError("该处理器不支持车型字段映射")
        ids = [m.mapping_id for m in self.mappings]
        orders = [m.order for m in self.mappings]
        if len(set(ids)) != len(ids) or len(set(orders)) != len(orders):
            raise ValueError("动作映射编号或次序重复")
        previous = set()
        # Legacy properties remain valid for immutable published definitions.
        # The model handler now executes only the model ID/name mapping.
        allowed = {
            "brandName",
            "brandId",
            "seriesName",
            "seriesId",
            "modelName",
            "modelId",
            "config.driveForm",
            "config.fuelType",
            "config.emissionStandard",
            "config.maxHp",
            "config.engineBrand",
            "config.engineType",
            "config.packingCaseLength",
        }
        for m in sorted(self.mappings, key=lambda m: m.order):
            if set(m.depends_on) - previous:
                raise ValueError("动作依赖必须指向更早的映射，不能循环或悬空")
            if m.source_kind != "MODEL_CANDIDATE" or m.source_property not in allowed:
                raise ValueError("未实现的动作来源属性")
            if m.source_option_property and m.source_option_property not in allowed:
                raise ValueError("未实现的选项来源属性")
            previous.add(m.mapping_id)
        return self
