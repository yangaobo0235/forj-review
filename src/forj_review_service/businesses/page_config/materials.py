"""Keep authored material requirements and the upload checklist consistent."""

from dataclasses import replace

from forj_review_service.businesses.material_policies import (
    MaterialPolicy,
    MaterialPresenceGroup,
    MaterialRequirement,
)


def compile_presence_groups(base: MaterialPolicy) -> tuple[MaterialPresenceGroup, ...]:
    """Keep explicit alternatives; derive upload groups for uncovered requirements."""
    groups = list(base.presence_groups)
    requirements = base.materials
    covered = {
        (item.document_type, item.business_scope)
        for group in groups
        for option in group.alternatives
        for item in option
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
        existing = next(
            (
                index
                for index, group in enumerate(groups)
                if key[0] == "alternative"
                and all(
                    item.alternative_group == key[1]
                    for option in group.alternatives
                    for item in option
                )
            ),
            None,
        )
        if existing is not None:
            group = groups[existing]
            groups[existing] = replace(group, alternatives=(*group.alternatives, *alternatives))
        else:
            groups.append(
                MaterialPresenceGroup(
                    key=":".join(key),
                    display_name="或".join(member.display_name for member in members),
                    alternatives=alternatives,
                )
            )
    return tuple(groups)
