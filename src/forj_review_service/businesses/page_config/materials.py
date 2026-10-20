"""Keep authored material requirements and the upload checklist consistent."""

from dataclasses import replace

from forj_review_service.businesses.material_policies import (
    MaterialPolicy,
    MaterialPresenceGroup,
    MaterialRequirement,
)
from forj_review_service.businesses.page_config.schema import PageDefinition


def compile_presence_groups(
    definition: PageDefinition,
    base: MaterialPolicy | None,
    requirements: tuple[MaterialRequirement, ...],
) -> tuple[MaterialPresenceGroup, ...]:
    """Preserve upload-only alternatives while applying explicit material edits.

    Upload facts and extraction completeness remain separate: an untouched
    upload requirement can have its own count or identity-card side. Only
    explicitly authored properties replace those details.
    """
    edits = {item.document_type: item for item in definition.materials}
    removed = set(definition.remove_materials)
    # Moving a member between alternative groups requires rebuilding both groups.
    regroup = {
        requirement.alternative_group
        for requirement in (*requirements, *(base.materials if base else ()))
        if requirement.alternative_group
        and requirement.document_type in edits
        and "alternative_group" in edits[requirement.document_type].model_fields_set
    }

    def updated(requirement: MaterialRequirement) -> MaterialRequirement | None:
        if requirement.document_type in removed:
            return None
        item = edits.get(requirement.document_type)
        if item is None:
            return requirement
        if "required" in item.model_fields_set and not item.required and not item.alternative_group:
            return None
        if "scopes" in item.model_fields_set and item.scopes:
            if requirement.business_scope not in item.scopes:
                return None
        changes = {
            key: getattr(item, key)
            for key in ("minimum_count", "required_pages", "alternative_group")
            if key in item.model_fields_set
        }
        if item.label is not None:
            changes.update(display_name=item.label, presence_name="")
        return replace(requirement, **changes)

    groups = []
    for group in base.presence_groups if base else ():
        alternatives = []
        for option in group.alternatives:
            values = []
            for requirement in option:
                value = updated(requirement)
                if value is None or requirement.alternative_group in regroup:
                    continue
                if value.alternative_group not in regroup:
                    values.append(value)
            if values:
                alternatives.append(tuple(values))
        if alternatives:
            groups.append(replace(group, alternatives=tuple(alternatives)))

    covered = {
        (item.document_type, item.business_scope)
        for group in groups for option in group.alternatives for item in option
    }
    pending: dict[tuple[str, ...], list[MaterialRequirement]] = {}
    for requirement in requirements:
        if (requirement.document_type, requirement.business_scope) in covered:
            continue
        key = (
            ("alternative", requirement.alternative_group)
            if requirement.alternative_group
            else ("required", requirement.business_scope, requirement.document_type)
        )
        pending.setdefault(key, []).append(requirement)
    for key, members in pending.items():
        alternatives = tuple((member,) for member in members)
        # A newly required alternative joins the inherited OR group, rather
        # than creating a second group that would also have to be satisfied.
        existing = next((
            index for index, group in enumerate(groups)
            if key[0] == "alternative" and all(
                item.alternative_group == key[1]
                for option in group.alternatives for item in option
            )
        ), None)
        if existing is not None:
            group = groups[existing]
            groups[existing] = replace(group, alternatives=(*group.alternatives, *alternatives))
        else:
            groups.append(MaterialPresenceGroup(
                key=":".join(key),
                display_name="或".join(member.display_name for member in members),
                alternatives=alternatives,
            ))
    return tuple(groups)
