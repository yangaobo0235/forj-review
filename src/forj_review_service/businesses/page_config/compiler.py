"""Compile an authoring declaration to the existing immutable runtime pack."""

from dataclasses import fields as dataclass_fields
from dataclasses import replace

from forj_review_service.businesses.material_policies import MaterialPolicy
from forj_review_service.businesses.packs.model import (
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
from forj_review_service.businesses.page_action_catalog import PAGE_ACTION_SPECS
from forj_review_service.businesses.page_config.materials import compile_presence_groups
from forj_review_service.businesses.page_config.schema import PageDefinition
from forj_review_service.businesses.profiles import BusinessProfile, build_profile
from forj_review_service.businesses.rules.catalog import (
    REPLACEMENT_POLICY_CHECKS,
    RULE_DEFINITIONS,
    RULE_FIELDS,
    TASK_PROJECTOR_RULES,
    rule_spec,
)
from forj_review_service.capabilities.specs import CapabilityBinding, CapabilitySpec
from forj_review_service.contracts.errors import ConfigurationInputError


def _unique(values):
    return tuple(dict.fromkeys(values))


def _compile_fields(
    definition: PageDefinition,
    base: BusinessExtensionPack,
    scopes: tuple[str, ...],
) -> tuple[dict[str, FieldDeclaration], list[RouteDeclaration], list[tuple[str, str]]]:
    """字段拥有证据路由；删除或重写字段时同步处理其路由和检查绑定。"""
    fields = {}
    routes = []
    bindings = []
    field_attributes = {item.name for item in dataclass_fields(FieldDeclaration)}
    for item in definition.fields:
        attributes = field_attributes & item.model_fields_set - {"key", "authority"}
        changes = {key: getattr(item, key) for key in attributes}
        if changes.get("label", "") is None:
            raise ConfigurationInputError(f"字段 {item.key} 的 label 不能为 null")
        if "authority" in item.model_fields_set:
            changes["authority"] = tuple(
                AuthorityRule(**value.model_dump()) for value in item.authority
            )
        changes.setdefault("required", item.required)
        changes.setdefault("section", scopes[0] if len(scopes) == 1 else "")
        if "evidence" in item.model_fields_set:
            changes["sources"] = _unique(
                source.material for source in item.evidence if source.comparison
            )
            for source in item.evidence:
                scope = source.scope or (scopes[0] if len(scopes) == 1 else "")
                if not scope or scope not in scopes:
                    raise ConfigurationInputError(f"字段 {item.key} 的 evidence 必须指定有效 scope")
                material = base.material(source.material)
                if material:
                    if material.allowed_scopes and scope not in material.allowed_scopes:
                        raise ConfigurationInputError(
                            f"字段 {item.key} 的 evidence 与材料允许分区不一致"
                        )
                    if material.scope_independent and source.field != item.key:
                        raise ConfigurationInputError(
                            "主体独立材料的跨字段映射需要扩展专用路由能力"
                        )
                routes.append(
                    RouteDeclaration(
                        source.material,
                        source.field,
                        (item.key,),
                        scope=scope,
                        group_order=source.group_order,
                    )
                )
        fields[item.key] = FieldDeclaration(key=item.key, **changes)
        if "checks" in item.model_fields_set:
            bindings.extend((check_id, item.key) for check_id in item.checks)

    for field in fields.values():
        if not field.uncertain_requires_review and field.mode != "SYSTEM":
            raise ConfigurationInputError(f"字段 {field.key} 不能关闭识别不确定性保护")

    return fields, routes, bindings


def _compile_materials(base: BusinessExtensionPack, scopes, routes):
    """Derive extraction allowlists from complete seeds and field-owned evidence."""
    seeds = base.material_extraction_seeds or base.materials
    requirements = base.material_policy.materials if base.material_policy else ()
    materials = []
    for material in seeds:
        material_routes = [r for r in routes if r.document_type == material.document_type]
        page_fields = (
            ("registration.covered_pages",)
            if any(
                r.document_type == material.document_type and r.required_pages for r in requirements
            )
            else ()
        )
        scoped = dict(material.scoped_fields)
        for scope in scopes:
            scoped[scope] = _unique(
                (
                    *scoped.get(scope, material.fields),
                    *page_fields,
                    *(r.source_field for r in material_routes if not r.scope or r.scope == scope),
                )
            )
        materials.append(
            replace(
                material,
                fields=_unique(
                    (*material.fields, *page_fields, *(r.source_field for r in material_routes))
                ),
                scoped_fields=tuple(scoped.items()),
            )
        )
    policy = base.material_policy
    if policy:
        policy = replace(policy, presence_groups=compile_presence_groups(policy))
    return tuple(materials), seeds, policy


def _compile_capabilities(
    definition: PageDefinition,
    base_profile: BusinessProfile,
    fields: dict[str, FieldDeclaration],
    bindings: list[tuple[str, str]],
    material_policy: MaterialPolicy | None,
) -> tuple[tuple[CapabilitySpec, ...], tuple[CapabilityBinding, ...]]:
    """合并能力声明并校验依赖；检查绑定必须有启用的规则生产者。"""
    specs = {spec.capability_id: spec for spec in (base_profile.capabilities)}
    capability_bindings = {binding.capability_id: binding for binding in (base_profile.bindings)}
    for spec in specs.values():
        if spec.kind == "RULE" and not (
            base_profile.replacement_policy
            and spec.capability_id == f"{definition.region}_replacement_policy"
        ):
            rule_spec(spec.capability_id)
    for rule_id in specs:
        if not capability_bindings[rule_id].enabled:
            continue
        missing = RULE_FIELDS.get(rule_id, frozenset()) - fields.keys()
        if missing:
            raise ConfigurationInputError(f"规则 {rule_id} 依赖字段：{', '.join(sorted(missing))}")
    declared_checks = {
        check
        for rule_id in specs
        if capability_bindings[rule_id].enabled
        if rule_id in RULE_DEFINITIONS
        for check in RULE_DEFINITIONS[rule_id].checks
    }
    if base_profile.replacement_policy:
        declared_checks.update(REPLACEMENT_POLICY_CHECKS)
    for check_id, field_key in bindings:
        if check_id not in declared_checks:
            raise ConfigurationInputError(
                f"字段 {field_key} 的 checks {check_id} 没有已启用的规则生产者；"
                "请显式修改该字段的 checks 或补充替代规则"
            )

    if material_policy is not None:
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

    if base_profile.replacement_policy:
        # RegionDeclaration generates this pair; copying it from the effective
        # Profile would register it twice on the derived pack.
        policy_rule_id = f"{definition.region}_replacement_policy"
        specs.pop(policy_rule_id, None)
        capability_bindings.pop(policy_rule_id, None)

    return tuple(specs.values()), tuple(capability_bindings.values())


def _validate_compiled_pack(pack: BusinessExtensionPack, base: BusinessExtensionPack) -> None:
    """发布前统一校验字段、材料、投影和页面写回之间的引用。"""
    validate_pack_references(pack)
    material_ids = {item.document_type for item in pack.materials}
    disabled = {b.capability_id for b in pack.binding_declarations if not b.enabled}
    specs = {item.capability_id for item in pack.capability_specs} - disabled
    bindings = pack.field_check_bindings
    action_fields = {
        key
        for spec in PAGE_ACTION_SPECS
        if spec.action_id in pack.page_action_ids
        for key in spec.writable_fields
    }
    for field in pack.fields:
        projector_rule = TASK_PROJECTOR_RULES.get(field.task_projector)
        if projector_rule and projector_rule not in specs:
            raise ConfigurationInputError(
                f"字段 {field.key} 的任务投影依赖未启用规则 {projector_rule}"
            )
        needs_page = field.mode not in (None, "DERIVED") and field.page_evidence_required
        if (
            needs_page
            or field.locator_id
            or field.locator_name
            or field.key in pack.identity_anchors
        ) and not field.aliases:
            raise ConfigurationInputError(
                f"页面字段 {field.key} 缺少 aliases；定位 ID/name 不能代替页面别名"
            )
        if set(field.sources) - material_ids:
            raise ConfigurationInputError(f"字段 {field.key} 引用了未声明的材料")
        if field.mode not in (None, "SYSTEM", "DERIVED") and not field.sources:
            raise ConfigurationInputError(f"字段 {field.key} 缺少材料证据来源")
        for authority in field.authority:
            if set(authority.document_types) - material_ids:
                raise ConfigurationInputError(f"字段 {field.key} 的权威链引用了未声明的材料")
        if field.writable and (
            "fill_review_fields" not in pack.page_action_ids and field.key not in action_fields
        ):
            raise ConfigurationInputError(
                f"字段 {field.key} 声明了 writable，但未启用支持该字段的页面动作"
            )
        if (
            field.required
            and field.mode is None
            and not any(key == field.key for _, key in bindings)
        ):
            raise ConfigurationInputError(
                f"必审字段 {field.key} 缺少 evidence、比较模式或规则检查绑定"
            )
    for route in pack.routes:
        if route.document_type not in material_ids:
            raise ConfigurationInputError(f"evidence 引用了未声明的材料：{route.document_type}")
    if pack.material_policy:
        allowed_scopes = {*pack.scopes, *(group.scope for group in pack.page_groups)}
        requirements = (
            *pack.material_policy.materials,
            *(
                r
                for group in pack.material_policy.presence_groups
                for option in group.alternatives
                for r in option
            ),
        )
        for requirement in requirements:
            if (
                requirement.document_type not in material_ids
                or requirement.business_scope not in allowed_scopes
            ):
                raise ConfigurationInputError("材料上传要求引用了未声明的材料或分区")
        for requirement in pack.material_policy.field_sources:
            selectors = (
                *requirement.all_of,
                *(selector for group in requirement.any_of_groups for selector in group),
            )
            if any(s.document_type and s.document_type not in material_ids for s in selectors):
                raise ConfigurationInputError(
                    f"字段 {requirement.field} 的材料证据要求引用了未声明的材料"
                )
    for group in pack.page_groups:
        inherited_independent_scopes = {
            item.scope for item in base.page_groups if item.scope not in base.scopes
        }
        if group.scope not in {*pack.scopes, "other", *inherited_independent_scopes}:
            raise ConfigurationInputError(f"图片分组 {group.label} 引用了未知分区")
    if not pack.fields:
        raise ConfigurationInputError("配置中没有审核字段")
    if not any(field.required or field.reviewable and field.aliases for field in pack.fields):
        raise ConfigurationInputError("配置中没有可展示的审核字段")
    if ("fill_review_fields" in pack.page_action_ids or action_fields) and (
        not pack.writable_control_kinds or not pack.record_identity_fields
    ):
        raise ConfigurationInputError("页面写回必须声明控件类型和独立记录身份字段")


def compile_page(definition: PageDefinition) -> BusinessProfile:
    """Compile one complete declaration without external templates or defaults."""
    base = _complete_base(definition)
    base_profile = build_profile(base, definition.region, page_id=definition.page_id)
    scopes = definition.scopes
    fields, routes, bindings = _compile_fields(definition, base, scopes)
    materials, extraction_seeds, material_policy = _compile_materials(base, scopes, routes)
    specs, capability_bindings = _compile_capabilities(
        definition,
        base_profile,
        fields,
        bindings,
        material_policy,
    )
    pack = replace(
        base,
        fields=tuple(fields.values()),
        materials=materials,
        material_extraction_seeds=extraction_seeds,
        routes=tuple(routes),
        sections=tuple(SectionDeclaration(**item.model_dump()) for item in definition.sections),
        regions=(
            RegionDeclaration(
                definition.region,
                version=definition.version,
                admin_paths=definition.paths,
                page_anchors=definition.anchors,
                review_tab=definition.review_tab or "",
                replacement_policy=replace(
                    base_profile.replacement_policy, version=definition.version
                )
                if base_profile.replacement_policy
                else None,
            ),
        ),
        material_policy=material_policy,
        capability_specs=specs,
        binding_declarations=capability_bindings,
        field_check_bindings=tuple(bindings),
        identity_anchors=definition.identity_anchors,
        record_identity_fields=definition.record_identity_fields,
        review_declared_fields_only=definition.review_declared_fields_only,
        page_action_ids=definition.page_actions,
        action_bindings=definition.actions,
        writable_control_kinds=definition.writable_control_kinds,
        page_interaction=bool(definition.page_actions),
    )
    _validate_compiled_pack(pack, base)
    return build_profile(pack, definition.region, page_id=definition.page_id)


def _complete_base(definition: PageDefinition) -> BusinessExtensionPack:
    settings = definition.settings
    materials = tuple(
        MaterialDeclaration(
            document_type=item.document_type,
            display_name=item.label or item.document_type,
            fields=item.extra_fields,
            guidance=item.guidance,
            hints=item.hints,
            allowed_scopes=item.scopes,
            scoped_fields=item.details.scoped_extra_fields if item.details else (),
            scoped_guidance=item.details.scoped_guidance if item.details else (),
            scoped_names=item.details.scoped_names if item.details else (),
            scope_independent=item.details.scope_independent if item.details else False,
            scoped_vehicle_fields=item.details.scoped_vehicle_fields if item.details else (),
            slots=item.details.slots if item.details else (),
            derived_field=item.details.derived_field if item.details else None,
        )
        for item in definition.materials
    )
    policy = settings.replacement_policy
    if policy and policy.region != definition.region:
        raise ConfigurationInputError("地区政策与页面 region 不一致")
    return BusinessExtensionPack(
        business_type=str(definition.business_type),
        title=definition.title,
        scopes=definition.scopes,
        materials=materials,
        material_extraction_seeds=materials,
        page_groups=tuple(
            PageGroupDeclaration(**item.model_dump()) for item in definition.page_groups
        ),
        regions=(
            RegionDeclaration(
                definition.region,
                version=definition.version,
                replacement_policy=policy,
            ),
        ),
        capability_specs=settings.capability_specs,
        binding_declarations=settings.binding_declarations,
        material_policy=settings.material_policy,
        page_field_composites=settings.page_field_composites,
        qr_field_bindings=settings.qr_field_bindings,
        invoice_verification_field=settings.invoice_verification_field,
        ignored_image_labels=settings.ignored_image_labels,
        retry_policy=settings.retry_policy,
        presentation=settings.presentation,
    )
