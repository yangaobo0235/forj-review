"""Compile an authoring declaration to the existing immutable runtime pack."""

from dataclasses import fields as dataclass_fields
from dataclasses import replace

from forj_review_assistant.businesses.configuration.schema import PageDefinition
from forj_review_assistant.businesses.material_policies import MaterialPolicy, MaterialRequirement
from forj_review_assistant.businesses.packs.model import (
    AuthorityRule,
    BusinessExtensionPack,
    FieldDeclaration,
    MaterialDeclaration,
    PageGroupDeclaration,
    RegionDeclaration,
    RouteDeclaration,
    SectionDeclaration,
    validate_pack_references,
)
from forj_review_assistant.businesses.profiles import BusinessProfile, build_profile
from forj_review_assistant.businesses.rules.catalog import (
    REPLACEMENT_POLICY_CHECKS,
    RULE_DEFINITIONS,
    RULE_FIELDS,
    TASK_PROJECTOR_RULES,
    rule_spec,
)
from forj_review_assistant.capabilities.specs import CapabilityBinding, CapabilitySpec


def _unique(values):
    return tuple(dict.fromkeys(values))


def _extraction_seeds(pack: BusinessExtensionPack) -> dict[str, MaterialDeclaration]:
    if pack.material_extraction_seeds is not None:
        return {item.document_type: item for item in pack.material_extraction_seeds}
    # Native packs predate provenance. A route owns its extraction input;
    # un-routed values remain explicit inputs for native handlers.
    result = {}
    for material in pack.materials:
        routes = [r for r in pack.routes if r.document_type == material.document_type]
        page_fields = {"registration.covered_pages"} if pack.material_policy and any(
            r.document_type == material.document_type and r.required_pages
            for r in pack.material_policy.materials
        ) else set()
        generated = {r.source_field for r in routes} | page_fields
        result[material.document_type] = replace(
            material,
            fields=tuple(f for f in material.fields if f not in generated),
            scoped_fields=tuple(
                (scope, tuple(f for f in values if f not in page_fields and not any(
                    r.source_field == f and (not r.scope or r.scope == scope) for r in routes
                )))
                for scope, values in material.scoped_fields
            ),
        )
    return result


def compile_page(
    definition: PageDefinition,
    template: BusinessProfile | None = None,
) -> BusinessProfile:
    """Compile without changing templates or any process-global business tables."""
    if definition.extends:
        if template is None or template.pack_declaration is None:
            raise ValueError("extends 必须引用已配置的业务模板")
        template_id = template.page_id or f"{template.business_type}:{template.region}"
        if (template_id, template.version) != (
            definition.extends.page_id,
            definition.extends.version,
        ):
            raise ValueError("extends 与提供的模板身份或版本不一致")
        base = template.pack_declaration
        if template.replacement_policy and (
            definition.business_type != template.business_type
            or definition.region != template.region
        ):
            raise ValueError("带地区政策的模板不能跨业务或跨地区继承")
    else:
        if template is not None:
            raise ValueError("未声明 extends，不能隐式使用模板")
        base = BusinessExtensionPack(business_type=str(definition.business_type))

    scopes = definition.scopes if "scopes" in definition.model_fields_set else base.scopes
    if not scopes:
        raise ValueError("页面必须声明至少一个材料分区 scopes")
    fields = {item.key: item for item in base.fields}
    removed = set(definition.remove_fields)
    unknown_removed = removed - fields.keys()
    if unknown_removed:
        raise ValueError(f"删除了不存在的字段：{', '.join(sorted(unknown_removed))}")
    for key in removed:
        del fields[key]

    rewritten = removed | {
        item.key for item in definition.fields if "evidence" in item.model_fields_set
    }
    # Expand template placeholders before editing a field. Deleting old_vehicle.vin
    # must not accidentally delete the new_vehicle side of a shared route.
    expanded_routes = [
        replace(
            route,
            scope=scope,
            targets=tuple(target.replace("{scope}", scope) for target in route.targets),
        )
        for route in base.routes
        for scope in ((route.scope,) if route.scope else base.scopes)
    ]
    routes = [
        replace(route, targets=tuple(target for target in route.targets if target not in rewritten))
        for route in expanded_routes
    ]
    routes = [route for route in routes if route.targets]
    bindings = [item for item in base.field_check_bindings if item[1] not in removed]
    field_attributes = {item.name for item in dataclass_fields(FieldDeclaration)}
    for item in definition.fields:
        existing = fields.get(item.key)
        if existing is None and item.label is None:
            raise ValueError(f"新字段 {item.key} 必须声明 label")
        attributes = field_attributes & item.model_fields_set - {"key", "authority"}
        changes = {key: getattr(item, key) for key in attributes}
        if changes.get("label", "") is None:
            raise ValueError(f"字段 {item.key} 的 label 不能为 null")
        if "authority" in item.model_fields_set:
            changes["authority"] = tuple(
                AuthorityRule(**value.model_dump()) for value in item.authority
            )
        if existing is None:
            changes.setdefault("required", item.required)
            changes.setdefault("section", scopes[0] if len(scopes) == 1 else "")
        if "evidence" in item.model_fields_set:
            changes["sources"] = _unique(source.material for source in item.evidence)
            if "mode" not in item.model_fields_set and item.evidence:
                changes["mode"] = existing.mode if existing and existing.mode else "PARALLEL"
            for source in item.evidence:
                scope = source.scope or (scopes[0] if len(scopes) == 1 else "")
                if not scope or scope not in scopes:
                    raise ValueError(f"字段 {item.key} 的 evidence 必须指定有效 scope")
                routes.append(
                    RouteDeclaration(
                        source.material,
                        source.field,
                        (item.key,),
                        scope=scope,
                        group_order=source.group_order,
                    )
                )
        fields[item.key] = (
            replace(existing, **changes) if existing else FieldDeclaration(key=item.key, **changes)
        )
        if "checks" in item.model_fields_set:
            bindings = [binding for binding in bindings if binding[1] != item.key]
            bindings.extend((check_id, item.key) for check_id in item.checks)

    for field in fields.values():
        if not field.uncertain_requires_review and field.mode != "SYSTEM":
            raise ValueError(f"字段 {field.key} 不能关闭识别不确定性保护")

    materials = _extraction_seeds(base)
    unknown_materials = set(definition.remove_materials) - materials.keys()
    if unknown_materials:
        raise ValueError(f"删除了不存在的材料：{', '.join(sorted(unknown_materials))}")
    for document_type in definition.remove_materials:
        del materials[document_type]
    requirements = list(base.material_policy.materials if base.material_policy else ())
    requirements = [r for r in requirements if r.document_type not in definition.remove_materials]
    for item in definition.materials:
        existing = materials.get(item.document_type)
        material_scopes = item.scopes or (existing.allowed_scopes if existing else ()) or scopes
        if not set(material_scopes) <= set(scopes):
            raise ValueError(f"材料 {item.document_type} 引用了未知分区")
        if item.label is None and (existing is None or "label" in item.model_fields_set):
            raise ValueError(f"材料 {item.document_type} 必须声明非空 label")
        label = item.label or existing.display_name
        explicit_fields = set(existing.fields if existing else ()) | {
            f for _, values in (existing.scoped_fields if existing else ()) for f in values
        }
        if set(item.remove_extra_fields) - explicit_fields:
            raise ValueError(f"材料 {item.document_type} 删除了不存在的显式提取字段")
        retained = tuple(
            f for f in (existing.fields if existing else ()) if f not in item.remove_extra_fields
        )
        materials[item.document_type] = MaterialDeclaration(
            document_type=item.document_type,
            display_name=label,
            fields=_unique((*retained, *item.extra_fields)),
            guidance=item.guidance
            if "guidance" in item.model_fields_set or not existing
            else existing.guidance,
            hints=item.hints
            if "hints" in item.model_fields_set or not existing
            else existing.hints,
            allowed_scopes=material_scopes,
            # Template details (slots, scoped prompts, derived values) survive
            # unless this declaration explicitly replaces their corresponding data.
            scoped_fields=tuple(
                (scope, _unique((
                    *(f for f in values if f not in item.remove_extra_fields), *item.extra_fields,
                )))
                for scope, values in (existing.scoped_fields if existing else ())
                if scope in material_scopes
            ),
            scoped_guidance=existing.scoped_guidance
            if existing and "guidance" not in item.model_fields_set
            else (),
            scoped_names=existing.scoped_names if existing else (),
            scope_independent=existing.scope_independent if existing else False,
            scoped_vehicle_fields=existing.scoped_vehicle_fields if existing else (),
            slots=existing.slots if existing else (),
            derived_field=existing.derived_field if existing else None,
        )
        old_requirements = {
            r.business_scope: r for r in requirements if r.document_type == item.document_type
        }
        requirements = [r for r in requirements if r.document_type != item.document_type]
        optional = (
            "required" in item.model_fields_set and not item.required and not item.alternative_group
        )
        for scope in material_scopes:
            prior = old_requirements.get(scope)
            if optional or not (prior or item.required or item.alternative_group):
                continue
            changes = {
                key: getattr(item, key)
                for key in (
                    "minimum_count",
                    "required_pages",
                    "alternative_group",
                )
                if key in item.model_fields_set
            }
            requirement = prior or MaterialRequirement(item.document_type, scope)
            requirements.append(replace(requirement, display_name=label, **changes))

    extraction_seeds = tuple(materials.values())
    # Extraction allowlists are generated from field-owned evidence routes.
    # Template-only values used by custom handlers are retained.
    for document_type, material in tuple(materials.items()):
        material_routes = [route for route in routes if route.document_type == document_type]
        # The recognition contract uses this reserved key for printed page numbers.
        page_fields = (
            ("registration.covered_pages",)
            if any(r.document_type == document_type and r.required_pages for r in requirements)
            else ()
        )
        declared = _unique(
            (*material.fields, *page_fields, *(route.source_field for route in material_routes))
        )
        scoped = dict(material.scoped_fields)
        for scope in scopes:
            scoped[scope] = _unique(
                (
                    *scoped.get(scope, material.fields),
                    *page_fields,
                    *(
                        route.source_field
                        for route in material_routes
                        if not route.scope or route.scope == scope
                    ),
                )
            )
        materials[document_type] = replace(
            material, fields=declared, scoped_fields=tuple(scoped.items())
        )

    specs = {spec.capability_id: spec for spec in (template.capabilities if template else ())}
    capability_bindings = {
        binding.capability_id: binding for binding in (template.bindings if template else ())
    }
    for rule_id in definition.disable_rules:
        if template and template.replacement_policy and rule_id.endswith("_replacement_policy"):
            raise ValueError("地区政策能力由政策声明生成，不能单独停用")
        if rule_id not in specs or specs[rule_id].kind != "RULE":
            raise ValueError(f"不能停用未启用的业务规则：{rule_id}")
        del specs[rule_id]
        capability_bindings.pop(rule_id, None)
    for rule_id in definition.enable_rules:
        spec = rule_spec(rule_id)
        specs[rule_id] = spec
        capability_bindings[rule_id] = CapabilityBinding(rule_id)
    for rule_id in specs:
        missing = RULE_FIELDS.get(rule_id, frozenset()) - fields.keys()
        if missing:
            raise ValueError(f"规则 {rule_id} 依赖字段：{', '.join(sorted(missing))}")
    declared_checks = {
        check
        for rule_id in specs
        if rule_id in RULE_DEFINITIONS
        for check in RULE_DEFINITIONS[rule_id].checks
    }
    if template and template.replacement_policy:
        declared_checks.update(REPLACEMENT_POLICY_CHECKS)
    for check_id, field_key in bindings:
        if check_id not in declared_checks:
            raise ValueError(
                f"字段 {field_key} 的 checks {check_id} 没有已启用的规则生产者；"
                "请显式修改该字段的 checks 或补充替代规则"
            )

    material_policy = None
    if requirements or (base.material_policy and base.material_policy.field_sources):
        material_policy = MaterialPolicy(
            mode=base.material_policy.mode if base.material_policy else "enforce",
            materials=tuple(requirements),
            field_sources=base.material_policy.field_sources if base.material_policy else (),
        )
        specs.setdefault(
            "material_completeness",
            CapabilitySpec(
                "material_completeness",
                kind="MATERIAL",
                stage="EVIDENCE_VALIDATION",
            ),
        )
        capability_bindings.setdefault(
            "material_completeness", CapabilityBinding("material_completeness")
        )

    if template and template.replacement_policy:
        # RegionDeclaration generates this pair; copying it from the effective
        # Profile would register it twice on the derived pack.
        policy_rule_id = f"{definition.region}_replacement_policy"
        specs.pop(policy_rule_id, None)
        capability_bindings.pop(policy_rule_id, None)

    template_region = base.region(template.region) if template else None
    pack = replace(
        base,
        business_type=str(definition.business_type),
        title=definition.title,
        scopes=scopes,
        fields=tuple(fields.values()),
        materials=tuple(materials.values()),
        material_extraction_seeds=extraction_seeds,
        routes=tuple(routes),
        sections=tuple(SectionDeclaration(**item.model_dump()) for item in definition.sections)
        if "sections" in definition.model_fields_set
        else base.sections,
        page_groups=tuple(
            PageGroupDeclaration(**item.model_dump()) for item in definition.page_groups
        )
        if "page_groups" in definition.model_fields_set
        else base.page_groups,
        regions=(
            RegionDeclaration(
                definition.region,
                version=definition.version,
                admin_paths=definition.paths,
                page_anchors=definition.anchors,
                review_tab=(definition.review_tab or "")
                if "review_tab" in definition.model_fields_set
                else template_region.review_tab if template_region else "",
                replacement_policy=replace(template.replacement_policy, version=definition.version)
                if template and template.replacement_policy
                else None,
            ),
        ),
        material_policy=material_policy,
        capability_specs=tuple(specs.values()),
        binding_declarations=tuple(capability_bindings.values()),
        field_check_bindings=tuple(bindings),
        presentation=replace(base.presentation, groups=tuple(
            group for group in base.presentation.groups
            if group.capability_id not in definition.disable_rules
        )),
        identity_anchors=definition.identity_anchors
        if "identity_anchors" in definition.model_fields_set
        else base.identity_anchors,
        record_identity_fields=definition.record_identity_fields
        if "record_identity_fields" in definition.model_fields_set
        else base.record_identity_fields,
        review_declared_fields_only=definition.review_declared_fields_only
        if "review_declared_fields_only" in definition.model_fields_set or not template
        else base.review_declared_fields_only,
        page_action_ids=definition.page_actions
        if "page_actions" in definition.model_fields_set
        else base.page_action_ids,
        writable_control_kinds=definition.writable_control_kinds
        if "writable_control_kinds" in definition.model_fields_set
        else base.writable_control_kinds,
        page_interaction=bool(definition.page_actions)
        if "page_actions" in definition.model_fields_set
        else base.page_interaction,
    )
    validate_pack_references(pack)
    material_ids = materials.keys()
    for field in pack.fields:
        projector_rule = TASK_PROJECTOR_RULES.get(field.task_projector)
        if projector_rule and projector_rule not in specs:
            raise ValueError(f"字段 {field.key} 的任务投影依赖未启用规则 {projector_rule}")
        needs_page = field.mode not in (None, "DERIVED") and field.page_evidence_required
        if (
            needs_page or field.locator_id or field.locator_name or field.key in pack.identity_anchors
        ) and not field.aliases:
            raise ValueError(f"页面字段 {field.key} 缺少 aliases；定位 ID/name 不能代替页面别名")
        if set(field.sources) - material_ids:
            raise ValueError(f"字段 {field.key} 引用了未声明的材料")
        if field.mode not in (None, "SYSTEM", "DERIVED") and not field.sources:
            raise ValueError(f"字段 {field.key} 缺少材料证据来源")
        for authority in field.authority:
            if set(authority.document_types) - material_ids:
                raise ValueError(f"字段 {field.key} 的权威链引用了未声明的材料")
        if field.writable and not pack.page_action_ids:
            raise ValueError(f"字段 {field.key} 声明了 writable，但未启用页面动作")
        if (
            field.required
            and field.mode is None
            and not any(key == field.key for _, key in bindings)
        ):
            raise ValueError(f"必审字段 {field.key} 缺少 evidence、比较模式或规则检查绑定")
    for route in pack.routes:
        if route.document_type not in material_ids:
            raise ValueError(f"evidence 引用了未声明的材料：{route.document_type}")
    if pack.material_policy:
        for requirement in pack.material_policy.field_sources:
            selectors = (*requirement.all_of, *(
                selector for group in requirement.any_of_groups for selector in group
            ))
            if any(s.document_type and s.document_type not in material_ids for s in selectors):
                raise ValueError(f"字段 {requirement.field} 的材料证据要求引用了未声明的材料")
    for group in pack.page_groups:
        inherited_independent_scopes = {
            item.scope for item in base.page_groups if item.scope not in base.scopes
        }
        if group.scope not in {*scopes, "other", *inherited_independent_scopes}:
            raise ValueError(f"图片分组 {group.label} 引用了未知分区")
    if not pack.fields:
        raise ValueError("配置中没有审核字段")
    if not any(field.required or field.reviewable and field.aliases for field in pack.fields):
        raise ValueError("配置中没有可展示的审核字段")
    if "fill_review_fields" in pack.page_action_ids and (
        not pack.writable_control_kinds or not pack.record_identity_fields
    ):
        raise ValueError("页面写回必须声明控件类型和独立记录身份字段")
    return build_profile(pack, definition.region, page_id=definition.page_id)
