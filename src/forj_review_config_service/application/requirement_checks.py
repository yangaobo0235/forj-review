"""Check explicit property expectations against real candidate objects, never model verdicts."""

from forj_review_config_service.contracts.tasks import active_requirements


def read_property(draft, assertion):
    kind, key = assertion["target_kind"], assertion.get("target_key", "")
    if kind == "page":
        value = draft
    else:
        collection, identifier = (
            ("fields", "key") if kind == "field" else ("materials", "document_type")
        )
        matches = [item for item in draft.get(collection, []) if item.get(identifier) == key]
        if not matches:
            return None, False
        value = matches[0]
    for token in assertion["property"][1:].split("/") if assertion["property"] else []:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and token.isdecimal() and int(token) < len(value):
            value = value[int(token)]
        else:
            return None, False
    return value, True


def requirement_checks(record, baseline=None):
    decisions = [
        d
        for d in record.decisions
        if d["status"] == "RESOLVED"
        and d.get("evidence_revision", record.evidence_revision) == record.evidence_revision
    ]
    requirements = active_requirements(record)
    active = {r["requirement_id"] for r in requirements}
    checks = []
    for requirement in requirements:
        assertions = requirement.get("assertions", [])
        results = []
        for assertion in assertions:
            actual, exists = read_property(record.draft, assertion)
            operator = assertion.get("operator", "equals")
            passed = (
                not exists
                if operator == "absent"
                else exists
                if operator == "exists"
                else exists and actual == assertion.get("expected")
            )
            before, previously_exists = (
                read_property(baseline, assertion) if baseline is not None else (None, False)
            )
            results.append(
                {
                    **assertion,
                    "actual": actual,
                    "exists": exists,
                    "before": before,
                    "changed": baseline is not None
                    and (exists != previously_exists or actual != before),
                    "ok": passed,
                }
            )
        decided = any(
            d.get("requirement_id") == requirement["requirement_id"]
            and d.get("requirement_revision", requirement.get("requirement_revision", 0))
            == requirement.get("requirement_revision", 0)
            for d in decisions
        )
        dependencies_ok = not set(requirement.get("depends_on", [])) - active
        checks.append(
            {
                "requirement_id": requirement["requirement_id"],
                "ok": decided
                and bool(results)
                and all(r["ok"] for r in results)
                and dependencies_ok,
                "assertions": results,
                "decision_recorded": decided,
                "dependencies_ok": dependencies_ok,
                "reason": "属性预期与候选逐项核对；审核语义由独立业务回放另行验证"
                if results
                else "缺少明确的对象属性预期，请补充验收口径；处理意见不能代替验证",
            }
        )
    return checks
