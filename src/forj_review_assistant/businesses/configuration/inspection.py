"""Deterministic effective configuration views for local authors and tooling."""

import hashlib
import json
from dataclasses import asdict

from pydantic_core import to_jsonable_python

from forj_review_assistant.businesses.profiles import BusinessProfile


def digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def effective_configuration(profile: BusinessProfile) -> dict:
    """Expand inheritance without leaking executable handlers or request data."""
    result = {
        "page_id": profile.page_id or f"{profile.business_type}:{profile.region}",
        "business_type": str(profile.business_type), "region": str(profile.region),
        "version": profile.version, "required_fields": list(profile.required_fields),
        "rules_configured": profile.rules_configured,
        "capabilities": {s.capability_id: asdict(s) for s in profile.capabilities},
        "bindings": {b.capability_id: asdict(b) for b in profile.bindings},
        "field_checks": {},
    }
    for check_id, field in profile.field_check_bindings:
        result["field_checks"].setdefault(field, []).append(check_id)
    pack = profile.pack_declaration
    if pack:
        declaration = asdict(pack)
        # Compilation provenance is useful for editing but is not executed.
        seeds = declaration.pop("material_extraction_seeds")
        result.update(
            fields={f.key: asdict(f) for f in pack.fields},
            materials={m.document_type: asdict(m) for m in pack.materials},
            extraction_inputs={
                m["document_type"]: {"fields": m["fields"], "scoped_fields": m["scoped_fields"]}
                for m in (seeds or [])
            },
            declaration=declaration,
        )
    else:
        result.update(fields={}, materials={}, extraction_inputs={}, declaration=None)
    # JSON values, rather than mixed tuple/list/Enum representations, make
    # saved previews and comparisons identical across process restarts.
    return to_jsonable_python(result)


def profile_fingerprint(profile: BusinessProfile) -> str:
    return digest(effective_configuration(profile))


def configuration_snapshot(profiles, active_profiles) -> dict:
    active = {(p.page_id, p.version) for p in active_profiles}
    pages = []
    for profile in sorted(profiles, key=lambda p: (p.page_id, p.version)):
        effective = effective_configuration(profile)
        pages.append({
            "active": (profile.page_id, profile.version) in active,
            "fingerprint": digest(effective), "effective": effective,
        })
    return {"schema_version": "1.0", "pages": pages, "fingerprint": digest(pages)}


def _mapping_diff(before: dict, after: dict) -> dict:
    return {
        "added": {key: after[key] for key in sorted(after.keys() - before.keys())},
        "removed": {key: before[key] for key in sorted(before.keys() - after.keys())},
        "changed": {
            key: {"before": before[key], "after": after[key]}
            for key in sorted(before.keys() & after.keys()) if before[key] != after[key]
        },
    }


def compare_configurations(before: dict, after: dict) -> dict:
    """Compare active pages by stable identity, even when versions differ."""
    def active(snapshot):
        return {
            p["effective"]["page_id"]: p["effective"]
            for p in snapshot["pages"] if p["active"]
        }

    old, new = active(before), active(after)
    changes = _mapping_diff(old, new)
    for page_id, change in tuple(changes["changed"].items()):
        prior, current = change["before"], change["after"]
        changes["changed"][page_id] = {
            "before_version": prior["version"], "after_version": current["version"],
            "fields": _mapping_diff(prior["fields"], current["fields"]),
            "materials": _mapping_diff(prior["materials"], current["materials"]),
            "field_checks": _mapping_diff(prior["field_checks"], current["field_checks"]),
            "capabilities": _mapping_diff(prior["capabilities"], current["capabilities"]),
            "effective_changes": _mapping_diff(prior, current),
        }
    return {"before": before["fingerprint"], "after": after["fingerprint"], "pages": changes}
