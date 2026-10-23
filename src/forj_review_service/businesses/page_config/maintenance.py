"""Model-independent operations shared by manual tools and the assistant."""

from copy import deepcopy

from forj_review_service.businesses.page_action_catalog import PAGE_ACTION_SPECS
from forj_review_service.businesses.page_config.compiler import compile_page
from forj_review_service.businesses.page_config.schema import FieldConfig, PageDefinition
from forj_review_service.businesses.rules.catalog import (
    REPLACEMENT_POLICY_FIELDS,
    RULE_FIELDS,
    RULE_OPTIONAL_FIELDS,
)
from forj_review_service.contracts.errors import ConfigurationInputError

EDITABLE_FIELD_PROPERTIES = frozenset(
    {
        "key",
        "label",
        "aliases",
        "section",
        "page_section",
        "section_required",
        "locator_id",
        "locator_name",
        "occurrence",
        "evidence",
        "normalizer",
        "mode",
        "max_age_days",
    }
)
ORDINARY_MODES = {"SINGLE_SOURCE", "AVAILABLE_EVIDENCE", "PARALLEL", "PAGE_AUXILIARY"}
BINDING_PROPERTIES = frozenset({"label", "aliases", "locator_id", "locator_name", "occurrence"})

POLICY_DATE_PROPERTIES = frozenset(
    {
        "invoice_date_from",
        "invoice_date_to",
        "disposal_deadline",
        "disposal_date_from",
    }
)
EDITABLE_PAGE_OPTIONS = frozenset(
    {
        "paths",
        "anchors",
        "review_tab",
        "scopes",
        "sections",
        "page_groups",
        "identity_anchors",
        "record_identity_fields",
        "writable_control_kinds",
        "material_policy",
        "replacement_policy",
        "rule_bindings",
        "presentation",
        "actions",
        "page_field_composites",
        "qr_field_bindings",
        "invoice_verification_field",
        "ignored_image_labels",
    }
)


class FieldMaintenanceError(ConfigurationInputError):
    pass


def field_dependencies(page: dict, key: str, *, include_mode: bool = True) -> list[str]:
    dependencies = []
    settings = page.get("settings") or {}
    if settings.get("replacement_policy") and key in REPLACEMENT_POLICY_FIELDS:
        dependencies.append("地区政策")
    for action in PAGE_ACTION_SPECS:
        if action.action_id in page.get("page_actions", []) and key in action.writable_fields:
            dependencies.append("页面动作：" + action.action_id)
    for name in ("identity_anchors", "record_identity_fields"):
        if key in page.get(name, []):
            dependencies.append(name)
    for spec in settings.get("capability_specs", []):
        rule = spec["capability_id"]
        if key in (*RULE_FIELDS.get(rule, ()), *RULE_OPTIONAL_FIELDS.get(rule, ())):
            dependencies.append("业务规则：" + rule)
    for item in page.get("fields", []):
        if item.get("material_field") == key:
            dependencies.append("字段证据：" + item["key"])
        if item["key"] == key:
            if (
                not item.get("reviewable", True)
                or include_mode
                and item.get("mode") in {"SYSTEM", "DERIVED"}
            ):
                dependencies.append("开发接入字段")
            dependencies.extend("规则检查：" + check for check in item.get("checks", []))
            if item.get("task_projector"):
                dependencies.append("专用任务投影：" + item["task_projector"])
    for item in settings.get("page_field_composites", []):
        if key in (item["primary_field"], item["secondary_field"], item["material_field"]):
            dependencies.append("组合字段：" + item["check_id"])
    if settings.get("invoice_verification_field") == key:
        dependencies.append("发票验真触发字段")
    for item in settings.get("qr_field_bindings", []):
        if item["field"] == key:
            dependencies.append("二维码绑定：" + item["page_field"])
    for item in (settings.get("material_policy") or {}).get("field_sources", []):
        if item["field"] == key:
            dependencies.append("材料证据完整性")
    for material in page.get("materials", []):
        if key in ((material.get("details") or {}).get("derived_field") or []):
            dependencies.append("材料派生：" + material["document_type"])
    if key in (settings.get("presentation") or {}).get("hidden_fields", []):
        dependencies.append("专用展示策略")
    return list(dict.fromkeys(dependencies))


def comparison_configurable(page: dict, field: dict) -> bool:
    """A plain page value can gain evidence without inventing an implementation dependency."""
    return (
        field.get("mode") == "SYSTEM"
        and field.get("reviewable", True)
        and not field.get("evidence")
        and not field.get("authority")
        and not field.get("material_field")
        and not field_dependencies(page, field["key"], include_mode=False)
    )


def field_removable(page: dict, field: dict) -> bool:
    """A plain SYSTEM input has no implementation consumer to retain it."""
    return comparison_configurable(page, field) or not field_dependencies(page, field["key"])


def validate_field_change(baseline: dict, candidate: dict) -> None:
    """Check the final diff as well as operations; rejects forged draft payloads."""
    before = PageDefinition.model_validate(baseline).model_dump(mode="json")
    after = PageDefinition.model_validate(candidate).model_dump(mode="json")
    if before["settings"].get("replacement_policy"):
        before["settings"]["replacement_policy"]["version"] = after["version"]
    if before["schema_version"] != "2.0" or after["schema_version"] != "2.0":
        raise FieldMaintenanceError("日常字段维护必须使用完整配置")
    excluded = {"version", "active", "fields"}
    if {k: v for k, v in before.items() if k not in excluded} != {
        k: v for k, v in after.items() if k not in excluded
    }:
        raise FieldMaintenanceError("字段维护不能修改页面身份、材料、规则或写回权限")
    original = {field["key"]: field for field in before["fields"]}
    updated = {field["key"]: field for field in after["fields"]}
    for key in original.keys() - updated.keys():
        dependencies = field_dependencies(before, key)
        if not field_removable(before, original[key]):
            raise FieldMaintenanceError(f"字段 {key} 不能删除；依赖：{'、'.join(dependencies)}")
    regions = (
        {s["key"] for s in before["sections"]}
        | {f["page_section"] for f in before["fields"]}
        | {f["section"] for f in before["fields"]}
    )
    for key, value in updated.items():
        prior = original.get(key)
        if prior is None:
            defaults = FieldConfig(key=key, label=value["label"]).model_dump(mode="json")
            protected = set(value) - EDITABLE_FIELD_PROPERTIES
            if any(value[k] != defaults[k] for k in protected):
                raise FieldMaintenanceError(f"新增字段 {key} 不能声明专用规则或写回权限")
        elif any(value[k] != prior[k] for k in set(value) - EDITABLE_FIELD_PROPERTIES):
            raise FieldMaintenanceError(f"字段 {key} 包含不可修改的开发属性")
        if prior == value:
            continue
        if (prior is None or value["evidence"] != prior["evidence"]) and any(
            not source["comparison"] for source in value["evidence"]
        ):
            raise FieldMaintenanceError("专用规则输入映射需要由开发维护")
        if value["page_section"] != "unknown" and value["page_section"] not in regions:
            raise FieldMaintenanceError("页面新分区需要由开发接入")
        if value["section"] and value["section"] not in regions:
            raise FieldMaintenanceError("审核新分区需要由开发接入")
        if prior is None or value["mode"] != prior["mode"]:
            if value["mode"] not in ORDINARY_MODES:
                raise FieldMaintenanceError("普通字段只能选择已支持的证据比较模式")
        if prior is not None and field_dependencies(before, key):
            comparison_change = (
                comparison_configurable(before, prior)
                and value["mode"] in ORDINARY_MODES
                and bool(value["evidence"])
                and all(
                    value[k] == prior[k]
                    for k in set(value) - BINDING_PROPERTIES - {"mode", "evidence", "normalizer"}
                )
            )
            if not comparison_change and any(
                value[k] != prior[k] for k in set(value) - BINDING_PROPERTIES
            ):
                raise FieldMaintenanceError(f"字段 {key} 有业务依赖，只能调整名称和页面定位")
    compile_page(PageDefinition.model_validate(after))


def validate_configurable_change(baseline: dict, candidate: dict) -> None:
    """Page identity and implementation contracts stay in code; options are editable."""
    from forj_review_service.businesses.page_config.row_contract import split_definition

    before = PageDefinition.model_validate(baseline)
    after = PageDefinition.model_validate(candidate)
    if (before.page_id, before.business_type, before.schema_version, before.region) != (
        after.page_id,
        after.business_type,
        after.schema_version,
        after.region,
    ):
        raise FieldMaintenanceError("页面身份与契约不能通过配置更改")
    original = {f.key: f.model_dump(mode="json") for f in before.fields}
    fields = [f.model_dump(mode="json") for f in after.fields]
    for value in fields:
        prior = original.get(value["key"])
        if prior:
            # Explicit permission changes remain visible in the real candidate.
            # The field projection checks every other property for all entrypoints.
            for key in ("required", "writable", "reviewable"):
                value[key] = prior[key]
    projected = before.model_dump(mode="json")
    projected.update(fields=fields, version=after.version, active=after.active)
    validate_field_change(before.model_dump(mode="json"), projected)
    original_materials = {m.document_type: m for m in before.materials}
    for material in after.materials:
        prior = original_materials.get(material.document_type)
        before_details = prior.details.model_dump() if prior and prior.details else {}
        after_details = material.details.model_dump() if material.details else {}
        if {k: v for k, v in before_details.items() if k != "scoped_guidance"} != {
            k: v for k, v in after_details.items() if k != "scoped_guidance"
        }:
            raise FieldMaintenanceError("材料处理实现属性需要由开发维护")
    split_definition(after)
    compile_page(after)


def edit_fields(draft: dict, operations: list[dict]) -> dict:
    candidate = deepcopy(draft)
    permission_changes = set()
    comparison_changes = set()
    for operation in operations:
        name, value = operation["op"], operation["value"]
        if name == "set_field_comparison":
            if value.keys() - {"key", "mode", "evidence", "normalizer"}:
                raise FieldMaintenanceError("比较配置只能修改 mode/evidence/normalizer")
            field = next((f for f in candidate["fields"] if f["key"] == value.get("key")), None)
            if (
                field is None
                or value.get("mode") not in ORDINARY_MODES
                or not value.get("evidence")
            ):
                raise FieldMaintenanceError("比较配置需要已有字段、已支持的比较模式和明确材料来源")
            field.update(value)
            comparison_changes.add(field["key"])
            continue
        if name in {
            "set_page_options",
            "upsert_material",
            "remove_material",
            "set_field_permissions",
            "set_material_policy",
            "set_policy_dates",
            "set_presentation",
        }:
            if name in {"set_material_policy", "set_policy_dates", "set_presentation"}:
                option = {
                    "set_material_policy": "material_policy",
                    "set_policy_dates": "replacement_policy",
                    "set_presentation": "presentation",
                }[name]
                if name == "set_policy_dates":
                    current = candidate.get("settings", {}).get("replacement_policy")
                    if not current or not value or value.keys() - POLICY_DATE_PROPERTIES:
                        raise FieldMaintenanceError("只能修改已有政策的日期属性")
                    value = {option: {**current, **value}}
                else:
                    value = {option: value}
                name = "set_page_options"
            if name == "set_page_options":
                from forj_review_service.businesses.page_config.row_contract import (
                    expand_options,
                    split_definition,
                )

                if not value or value.keys() - EDITABLE_PAGE_OPTIONS:
                    raise FieldMaintenanceError("页面选项包含未开放属性")
                options = {**split_definition(candidate), **value}
                candidate.update(
                    expand_options(
                        options, region=candidate["region"], version=candidate["version"]
                    )
                )
            elif name in {"upsert_material", "remove_material"}:
                from forj_review_service.businesses.page_config.schema import MaterialConfig

                existing = next(
                    (
                        m
                        for m in candidate["materials"]
                        if m["document_type"] == value.get("document_type")
                    ),
                    None,
                )
                if name == "remove_material":
                    if existing is None or set(value) != {"document_type"}:
                        raise FieldMaintenanceError("只能删除存在的材料")
                    candidate["materials"].remove(existing)
                else:
                    material = MaterialConfig.model_validate(
                        {**(existing or {}), **value}
                    ).model_dump(mode="json")
                    if existing:
                        candidate["materials"][candidate["materials"].index(existing)] = material
                    else:
                        candidate["materials"].append(material)
            else:
                if value.keys() - {"key", "required", "writable", "reviewable"}:
                    raise FieldMaintenanceError("字段权限操作仅接受 required/writable/reviewable")
                field = next((f for f in candidate["fields"] if f["key"] == value.get("key")), None)
                if field is None:
                    raise FieldMaintenanceError("字段不存在")
                field.update(value)
                permission_changes.add(field["key"])
            continue
        if name not in {"upsert_field", "remove_field", "set_collection_binding"}:
            raise FieldMaintenanceError("仅支持已有页面字段新增、修改和删除")
        if value.keys() - EDITABLE_FIELD_PROPERTIES or not isinstance(value.get("key"), str):
            raise FieldMaintenanceError("字段操作包含未开放属性或缺少 key")
        fields = candidate["fields"]
        existing = next((field for field in fields if field["key"] == value["key"]), None)
        if name == "set_collection_binding" and (
            existing is None
            or value.keys()
            - {
                "key",
                "label",
                "aliases",
                "section",
                "page_section",
                "section_required",
                "locator_id",
                "locator_name",
                "occurrence",
            }
        ):
            raise FieldMaintenanceError("页面绑定只能调整已有字段的采集属性")
        if name == "remove_field":
            if set(value) != {"key"} or existing is None:
                raise FieldMaintenanceError("只能删除存在的字段，删除操作仅接受 key")
            fields.remove(existing)
        elif existing is not None:
            existing.update(value)
        else:
            new_field = FieldConfig.model_validate(value).model_dump(mode="json")
            fields.append(new_field)
    before = {
        f["key"]: f for f in PageDefinition.model_validate(draft).model_dump(mode="json")["fields"]
    }
    for field in PageDefinition.model_validate(candidate).model_dump(mode="json")["fields"]:
        prior = before.get(
            field["key"],
            FieldConfig(key=field["key"], label=field["label"]).model_dump(mode="json"),
        )
        if field["key"] not in permission_changes and any(
            field[k] != prior[k] for k in ("required", "writable", "reviewable")
        ):
            raise FieldMaintenanceError("字段权限变化必须单独使用 set_field_permissions")
        if (
            prior.get("mode") == "SYSTEM"
            and field["mode"] != prior["mode"]
            and field["key"] not in comparison_changes
        ):
            raise FieldMaintenanceError("系统输入接入材料比较必须显式使用 set_field_comparison")
    validate_configurable_change(draft, candidate)
    return PageDefinition.model_validate(candidate).model_dump(mode="json")


def maintenance_contract(page: dict | None = None) -> dict:
    """Describe the same limits enforced above; this is not a second capability database."""
    result = {
        "operation_inputs": {
            "upsert_field": {
                "purpose": "新增或修改字段；名称修改也用此命令",
                "value": {"key": "稳定字段键", "label": "新名称"},
            },
            "set_collection_binding": {
                "purpose": "修改已有字段页面定位",
                "properties": sorted(BINDING_PROPERTIES | {"key"}),
            },
            "set_material_policy": "value为页面材料策略对象，不是单张图片ID",
            "set_policy_dates": {
                "purpose": "修改已存在的地区政策日期",
                "properties": sorted(POLICY_DATE_PROPERTIES),
            },
            "set_presentation": "value为页面级任务展示策略；不是字段名称，字段label用upsert_field",
            "set_field_permissions": "value明确key及reviewable/required/writable；只提交本次明确变化",
            "set_field_comparison": {
                "purpose": "为无专用依赖的系统输入接入已有材料比较，不改变必审和回填权限",
                "properties": ["key", "mode", "evidence", "normalizer"],
            },
            "upsert_material": "可维护guidance及details.scoped_guidance；槽位、派生关系等实现属性不可改",
        },
        "field_properties": sorted(EDITABLE_FIELD_PROPERTIES),
        "ordinary_modes": sorted(ORDINARY_MODES),
        "page_options": sorted(EDITABLE_PAGE_OPTIONS),
        "policy_date_properties": sorted(POLICY_DATE_PROPERTIES),
        "preconditions": [
            "已有启用页面和独立完整2.0配置",
            "稳定页面身份与实现属性保持不变",
            "删除字段前核对依赖",
            "权限变化使用显式命令",
            "材料槽位、派生关系等实现由开发维护；读取指引可配置",
        ],
        "consumers": {
            "binding": "审核扩展只读采集与受控回填",
            "evidence": "审核内核证据路由与比较",
            "material_policy": "审核内核材料完整性检查",
            "replacement_policy": "地区政策日期规则",
            "presentation": "审核任务投影",
            "permissions": "动作提议与浏览器回填检查",
        },
        "checks": ["类型与编译", "最终差异与依赖", "浏览器只读采集", "冻结业务样例回放"],
    }
    if page is not None:
        result["fields"] = {
            field["key"]: {
                "dependencies": field_dependencies(page, field["key"]),
                "editable_properties": sorted(BINDING_PROPERTIES)
                if field_dependencies(page, field["key"])
                else sorted(EDITABLE_FIELD_PROPERTIES),
                "removable": field_removable(page, field),
                "comparison_configurable": comparison_configurable(page, field),
            }
            for field in page.get("fields", [])
        }
        result["policy_dates_available"] = bool(page.get("settings", {}).get("replacement_policy"))
    return result
