"""Raw browser facts are checked identically by CLI and configuration UI."""

from datetime import UTC, datetime


def proof_is_current(proof):
    try:
        expires = datetime.fromisoformat(proof["expires_at"])
        return expires.tzinfo is not None and datetime.now(UTC) < expires
    except (ValueError, TypeError, KeyError):
        return False


def evaluate_collection(
    manifest: dict, facts: dict, page_id: str, selected_samples, sample_mapping: dict
) -> dict:
    missing = [
        f["key"]
        for f in manifest["fields"]
        if (
            type(facts.get("fields", {}).get(f["key"])) is not int or facts["fields"][f["key"]] != 1
        )
    ]
    allowed = set(manifest.get("writable_control_kinds", []))
    invalid = [
        key
        for key in sorted(manifest.get("writable_fields", []))
        if facts.get("control_kinds", {}).get(key) not in allowed
    ]
    missing_samples = [
        sample
        for sample in selected_samples
        if sample not in sample_mapping
        or facts.get("samples", {}).get(sample) != sample_mapping[sample]
    ]
    identity_ok = facts.get("matched_page_id") == page_id
    diagnostics = facts.get("diagnostics", [])
    return {
        "ok": not missing
        and not invalid
        and not diagnostics
        and not missing_samples
        and identity_ok,
        "missing_or_ambiguous": missing,
        "unsupported_controls": invalid,
        "diagnostics": diagnostics,
        "missing_material_bindings": missing_samples,
        "page_identity_matched": identity_ok,
    }
