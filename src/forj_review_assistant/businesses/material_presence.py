"""必传图片是否已上传；不以字段识别成功或失败决定上传状态。"""

from forj_review_assistant.businesses.material_policies import MaterialPolicy, MaterialRequirement
from forj_review_assistant.businesses.routing import normalize_document_type
from forj_review_assistant.contracts.recognition import (
    AgentBatchResult,
    DocumentClassification,
    MaterialChecklistItem,
    MaterialPresenceGroupResult,
    MaterialPresenceReport,
)
from forj_review_assistant.contracts.review import ReviewRequest


def evaluate_presence(
    request: ReviewRequest,
    policy: MaterialPolicy | None,
    batch: AgentBatchResult | None = None,
) -> MaterialPresenceReport | None:
    """按材料种类检查上传；字段、页码 OCR 和模型终态不决定上传与否。"""
    if policy is None or policy.mode == "disabled" or not policy.presence_groups:
        return None
    facts = {item.target_id: item for item in batch.document_classifications} if batch else {}
    required_types = {
        item.document_type for group in policy.presence_groups
        for option in group.alternatives for item in option
    }
    # 明确的单图标签本身就是上传事实，图片下载/字段提取失败不抹掉它。
    for image in request.images:
        image_id = str(image.image_id or image.index)
        if image_id in facts:
            continue
        document_type = next((
            normalized for raw in (image.document_type_hint, image.category_hint)
            if (normalized := normalize_document_type(raw)) in required_types
        ), None)
        if document_type:
            facts[image_id] = DocumentClassification(
                target_id=image_id, document_type=document_type,
                business_scope=image.business_scope,
                identity_sides=image.identity_sides_hint if document_type == "identity_card" else [],
            )
    diagnostics = request.collection_diagnostics
    notices = []
    if diagnostics.image_overflow:
        notices.append("页面图片超过采集上限，尚未确认的材料请查看原页面")
    if diagnostics.collection_issues:
        notices.append("页面采集存在异常，尚未确认的材料请查看原页面")

    def item_result(requirement: MaterialRequirement) -> MaterialChecklistItem:
        matches: set[str] = set()
        unresolved: set[str] = set()
        for image in request.images:
            image_id = str(image.image_id or image.index)
            fact = facts.get(image_id)
            if image.business_scope not in {requirement.business_scope, "unknown"}:
                continue
            if fact is None or fact.business_scope == "unknown":
                unresolved.add(image_id)
            elif fact.document_type == requirement.document_type:
                if requirement.identity_side and not fact.identity_sides:
                    unresolved.add(image_id)
                elif not requirement.identity_side or requirement.identity_side in fact.identity_sides:
                    matches.add(image_id)
        status = (
            "PRESENT" if len(matches) >= requirement.minimum_count
            else "UNCERTAIN" if unresolved or notices else "MISSING"
        )
        reason = {
            "PRESENT": "已提供对应材料图片，字段识别结果另行核对",
            "UNCERTAIN": "存在尚未确认类型、面别或分区的图片，请核对原图",
            "MISSING": "未发现对应材料图片",
        }[status]
        # 页码仍由原有证据质量检查负责，上传清单只展示材料类别。
        display_name = requirement.presence_name or requirement.display_name
        return MaterialChecklistItem(
            key=f"{requirement.business_scope}:{requirement.document_type}"
                + (f":{requirement.identity_side}" if requirement.identity_side else ""),
            display_name=display_name, material_type=requirement.document_type,
            business_scope=requirement.business_scope, status=status,
            image_ids=sorted(matches or unresolved), reason=reason,
        )

    groups = []
    for group in policy.presence_groups:
        options = [[item_result(item) for item in option] for option in group.alternatives]
        option_statuses = [
            "PRESENT" if all(item.status == "PRESENT" for item in option)
            else "MISSING" if any(item.status == "MISSING" for item in option)
            else "UNCERTAIN" for option in options
        ]
        status = (
            "PRESENT" if "PRESENT" in option_statuses
            else "UNCERTAIN" if "UNCERTAIN" in option_statuses else "MISSING"
        )
        groups.append(MaterialPresenceGroupResult(
            key=group.key, display_name=group.display_name, status=status, alternatives=options,
        ))
    status = (
        "INCOMPLETE" if any(group.status == "MISSING" for group in groups) else
        "UNCERTAIN" if any(group.status == "UNCERTAIN" for group in groups) else "COMPLETE"
    )
    return MaterialPresenceReport(status=status, groups=groups, notices=notices)


