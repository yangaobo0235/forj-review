"""Typed relational options and the frozen page-v2 digest projection.

The authoring/API declaration is independent of storage. Implementation metadata
is reconstructed from the versioned code catalog, never trusted from the DB.
"""

from copy import deepcopy
from dataclasses import asdict
from typing import Literal

from pydantic import Field, model_validator

from forj_review_service.businesses.page_config.action_contract import ActionBinding, ActionMapping
from forj_review_service.businesses.page_config.schema import (
    Declaration,
    PageDefinition,
    PageSettings,
)
from forj_review_service.capabilities.specs import CapabilitySpec
from forj_review_service.contracts.errors import ConfigurationInputError


def capability_spec(capability_id):
    from forj_review_service.businesses.rules.catalog import rule_spec

    if capability_id == "material_completeness":
        return CapabilitySpec(capability_id, kind="MATERIAL", stage="EVIDENCE_VALIDATION")
    if capability_id == "scrap_certificate_qr":
        return CapabilitySpec(
            capability_id,
            kind="EXTERNAL",
            stage="EVIDENCE",
            dependencies=("old_vehicle",),
            timeout_seconds=60,
        )
    if capability_id in {"verify_invoice", "set_affiliation"}:
        base = rule_spec(capability_id)
        from dataclasses import replace

        return replace(base, required=False, failure_policy="SAFE_DEGRADE")
    return rule_spec(capability_id)


class RuleBinding(Declaration):
    binding_id: str
    capability_id: str
    contract_version: Literal["1.0"] = "1.0"
    enabled: bool = True
    required: bool
    inputs: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def supported_contract(self):
        capability_spec(self.capability_id)
        # Current handlers take the page's typed policy/field declarations.
        # Do not advertise arbitrary parameters that no handler consumes.
        if self.inputs or self.parameters:
            raise ConfigurationInputError(
                "该版本能力不支持自定义输入/参数；请使用已有字段和专用政策配置"
            )
        if self.binding_id != self.capability_id:
            raise ConfigurationInputError(
                "当前能力只支持每页绑定一次，binding_id 必须等于 capability_id"
            )
        return self


def vehicle_model_action():
    pairs = [
        ("brand", "brandName", "spec.brand", "brandId"),
        ("series", "seriesName", "spec.series", "seriesId"),
        ("model", "modelName", "vehicle.model", "modelId"),
        ("drive_form", "config.driveForm", "spec.drive_form", None),
        ("fuel_type", "config.fuelType", "spec.fuel_type", None),
        ("emission", "config.emissionStandard", "spec.emission_standard", None),
        ("hp", "config.maxHp", "spec.max_hp", None),
        ("engine_brand", "config.engineBrand", "spec.engine_brand", None),
        ("engine", "config.engineType", "spec.engine_model", None),
        ("cargo", "config.packingCaseLength", "spec.cargo_length", None),
    ]
    return ActionBinding(
        action_id="vehicle_model_fill",
        handler_id="vehicle_model_fill",
        label="车型回填",
        mappings=tuple(
            ActionMapping(
                mapping_id=key,
                source_kind="MODEL_CANDIDATE",
                source_property=source,
                source_option_property=option,
                target_field=target,
                order=order,
                depends_on=(() if order == 0 else (pairs[order - 1][0],)),
                converter_id="positive_number" if key == "cargo" else "text",
                missing_policy="RESTORE_ORIGINAL" if key == "cargo" else "BLOCK",
                wait_condition_id="options_ready" if option else "none",
            )
            for order, (key, source, target, option) in enumerate(pairs)
        ),
    )


def split_definition(document):
    """Return formal options. Legacy defaults are excluded, not stored twice."""
    d = PageDefinition.model_validate(document).model_dump(mode="json", exclude={"active"})
    settings = d.pop("settings")
    specs = {s["capability_id"]: s for s in settings.pop("capability_specs")}
    bindings = settings.pop("binding_declarations")
    rules = []
    for b in bindings:
        spec = specs.pop(b["capability_id"])
        expected = asdict(capability_spec(b["capability_id"]))
        # Pydantic's JSON representation uses lists for tuples.
        import json

        expected = json.loads(json.dumps(expected))
        if spec != expected:
            raise ConfigurationInputError(f"能力 {b['capability_id']} 的实现契约与登记目录不一致")
        rules.append(
            RuleBinding(
                binding_id=b["capability_id"],
                capability_id=b["capability_id"],
                enabled=b["enabled"],
                required=spec["required"] if b["required"] is None else b["required"],
                parameters=dict(b["parameters"]),
            ).model_dump(mode="json")
        )
    if specs:
        raise ConfigurationInputError("能力声明缺少绑定")
    retry = settings.pop("retry_policy")
    if retry != {
        "semantic_retry_count": 1,
        "qr_web_retry_count": 1,
        "minimum_retry_window_seconds": 2.0,
    }:
        raise ConfigurationInputError("重试预算由框架环境配置维护，不能在页面中修改")
    policy = settings.get("material_policy")
    if policy:
        for requirement in policy["field_sources"]:
            if requirement.pop("any_of_groups", []):
                raise ConfigurationInputError("any_of_groups 尚无执行实现")
    replacement = settings.get("replacement_policy")
    if replacement:
        replacement.pop("region", None)
        replacement.pop("version", None)
    actions = d.pop("actions")
    d.pop("page_actions")
    for key in ("extends", "remove_fields", "remove_materials", "enable_rules", "disable_rules"):
        d.pop(key)
    options = {
        k: v
        for k, v in d.items()
        if k
        not in {
            "schema_version",
            "page_id",
            "business_type",
            "region",
            "version",
            "title",
            "enabled",
            "fields",
            "materials",
        }
    }
    options.update(settings, rule_bindings=rules, actions=actions)
    return options


def expand_options(options, *, region, version):
    """Frozen schema-2/page-v2 projection used to verify formal digests."""
    options = deepcopy(options)
    rules = [RuleBinding.model_validate(r) for r in options.pop("rule_bindings")]
    if len({r.binding_id for r in rules}) != len(rules):
        raise ConfigurationInputError("规则绑定编号重复")
    actions = [ActionBinding.model_validate(a) for a in options.pop("actions")]
    settings = {k: options.pop(k) for k in PageSettings.model_fields if k in options}
    settings["capability_specs"] = [asdict(capability_spec(r.capability_id)) for r in rules]
    settings["binding_declarations"] = [
        dict(capability_id=r.capability_id, enabled=r.enabled, required=r.required, parameters=[])
        for r in rules
    ]
    settings["retry_policy"] = {
        "semantic_retry_count": 1,
        "qr_web_retry_count": 1,
        "minimum_retry_window_seconds": 2.0,
    }
    policy = settings.get("material_policy")
    if policy:
        for r in policy["field_sources"]:
            r["any_of_groups"] = []
    replacement = settings.get("replacement_policy")
    if replacement:
        replacement.update(region=region, version=version)
    return {
        **options,
        "settings": settings,
        "actions": [a.model_dump(mode="json") for a in actions],
        "page_actions": [
            a.action_id for a in actions if a.enabled and a.action_id != "vehicle_model_fill"
        ],
    }
