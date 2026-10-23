"""审核结论的稳定原因类别；不从中文说明反推业务状态。"""

from typing import Literal

ReasonCode = Literal[
    "VALUE_MISMATCH",
    "RULE_VIOLATION",
    "EVIDENCE_MISSING",
    "RECOGNITION_UNCERTAIN",
    "AUTHORITY_MISSING",
    "PAGE_VALUE_MISSING",
    "POLICY_REVIEW",
    "EXTERNAL_UNAVAILABLE",
    "SYSTEM_INPUT",
]

DECISION_POLICY = {
    "version": "2",
    "CONFLICT": "可靠、可比较的证据已证明取值不一致，或明确违反硬性规则；不等于自动驳回",
    "INSUFFICIENT": "证据缺失、识别不确定、外部核验未完成，或规则明确要求人工确认",
    "MATCH": "所需证据和检查完整且满足规则；系统输入字段仅表示已填写，不表示材料核验通过",
    "priority": "已确认冲突优先；不确定读值不能证明通过或冲突，也不能掩盖可靠来源已确认的冲突",
    "policy_exceptions": "现有政策人工复核例外保留；硬性规则与人工例外须使用已注册能力，不可自行改算法",
    "uncertain_requires_review": "必须为 true；不能通过配置关闭识别不确定性保护",
}


def default_reason_codes(status: str) -> list[ReasonCode]:
    if status == "CONFLICT":
        return ["RULE_VIOLATION"]
    if status in {"INSUFFICIENT", "REVIEW_REQUIRED"}:
        return ["EVIDENCE_MISSING"]
    return []
