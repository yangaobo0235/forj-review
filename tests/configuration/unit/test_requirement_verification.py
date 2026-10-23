"""Objective goal coverage and independent changes, including field removal."""

from forj_review_config_service.application.requirement_checks import requirement_checks
from forj_review_config_service.application.tasks import manage_requirements, receive_message
from forj_review_config_service.contracts.models import TaskRecord
from forj_review_config_service.tools.arguments import RequirementArguments


def task():
    return TaskRecord(
        task_id="goals",
        baseline_digest="base",
        draft={
            "page_id": "page",
            "fields": [{"key": "invoice", "label": "开票日期"}, {"key": "vin", "label": "VIN"}],
        },
    )


def interpret(record, text, **kwargs):
    message = receive_message(record, text)
    args = RequirementArguments(message_id=message["message_id"], **kwargs).model_dump()
    return manage_requirements(record, args)


def resolve(record, requirement):
    record.decisions.append(
        {
            "requirement_id": requirement["requirement_id"],
            "status": "RESOLVED",
            "revision": record.revision,
            "intent_revision": record.intent_revision,
            "requirement_revision": requirement["requirement_revision"],
            "evidence_revision": record.evidence_revision,
        }
    )


def test_model_verdict_does_not_pass_wrong_candidate_and_records_real_difference():
    r = task()
    interpret(
        r,
        "修改开票日期名称",
        action="ACCEPT",
        goals=[
            {
                "text": "名称改成发票日期",
                "assertions": [
                    {
                        "target_kind": "field",
                        "target_key": "invoice",
                        "property": "/label",
                        "expected": "发票日期",
                    }
                ],
            }
        ],
    )
    resolve(r, r.requirements[0])
    baseline = r.draft
    assert not requirement_checks(r, baseline)[0]["ok"]
    r.draft = {**r.draft, "fields": [{"key": "invoice", "label": "发票日期"}, r.draft["fields"][1]]}
    r.revision += 1
    check = requirement_checks(r, baseline)[0]
    assert check["ok"] and check["assertions"][0]["changed"]
    assert check["assertions"][0]["before"] == "开票日期"


def test_partial_correction_preserves_unaffected_goal_and_its_verifiable_decision():
    r = task()
    interpret(
        r,
        "两项名称都调整",
        action="ACCEPT",
        goals=[
            {
                "text": "发票名称",
                "assertions": [
                    {
                        "target_kind": "field",
                        "target_key": "invoice",
                        "property": "/label",
                        "expected": "开票日期",
                    }
                ],
            },
            {
                "text": "VIN名称",
                "assertions": [
                    {
                        "target_kind": "field",
                        "target_key": "vin",
                        "property": "/label",
                        "expected": "VIN",
                    }
                ],
            },
        ],
    )
    first, second = r.requirements
    resolve(r, first)
    resolve(r, second)
    interpret(r, "第一项暂时撤回", action="WITHDRAW", requirement_ids=[first["requirement_id"]])
    assert first["status"] == "WITHDRAWN" and second["status"] == "ACTIVE"
    checks = requirement_checks(r)
    assert len(checks) == 1 and checks[0]["ok"]


def test_removal_checks_object_absence_and_does_not_confuse_null_with_missing():
    r = task()
    interpret(
        r,
        "删除普通字段",
        action="ACCEPT",
        goals=[
            {
                "text": "删除invoice",
                "assertions": [
                    {"target_kind": "field", "target_key": "invoice", "operator": "absent"}
                ],
            }
        ],
    )
    resolve(r, r.requirements[0])
    assert not requirement_checks(r)[0]["ok"]
    r.draft["fields"] = [f for f in r.draft["fields"] if f["key"] != "invoice"]
    assert requirement_checks(r)[0]["ok"]
    r.evidence_revision += 1
    assert not requirement_checks(r)[0]["ok"]


def test_query_preserves_intent_and_missing_dependency_blocks_coverage():
    r = task()
    interpret(
        r,
        "修改名称",
        action="ACCEPT",
        goals=[
            {
                "text": "名称",
                "assertions": [{"target_kind": "field", "target_key": "vin", "operator": "exists"}],
            }
        ],
    )
    requirement = r.requirements[0]
    resolve(r, requirement)
    revision = r.intent_revision
    interpret(r, "现在怎样了", action="QUERY")
    assert r.intent_revision == revision and requirement_checks(r)[0]["ok"]
    requirement["depends_on"] = ["unavailable"]
    assert not requirement_checks(r)[0]["ok"]
