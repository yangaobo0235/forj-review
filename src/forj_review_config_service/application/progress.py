"""Evidence-backed execution summaries; no model reasoning or fabricated progress."""

import json
from datetime import UTC, datetime

TOOL_TITLES = {
    "manage_requirements": "整理修改目标",
    "read_draft": "核对当前草稿",
    "get_configuration": "查阅正式配置与维护限制",
    "get_configuration_capabilities": "检查当前部署能力",
    "read_tool_result": "查阅相关配置依据",
    "get_page_snapshot": "检查采集目录",
    "reference_page_items": "引用页面证据",
    "analyze_materials": "检查材料样本",
    "inspect_material_region": "核对材料局部内容",
    "assess_route": "核对修改路径",
    "edit_draft": "生成配置修改",
    "record_decision": "记录修改依据",
    "inspect_candidate": "检查候选差异",
    "validate_candidate": "编译候选配置",
    "run_replay": "回放审核样例",
    "find_templates": "查找独立验证样例",
    "ask_user": "等待业务确认",
    "request_collection_trial": "等待页面只读验证",
    "submit_candidate": "提交发布前验证",
}


def tool_activity(name, args, result=None):
    """Only describe facts returned by an operation; never infer business success."""
    title = TOOL_TITLES.get(name, "执行配置操作")
    targets = args.get("field_keys", []) or args.get("sample_ids", [])
    if args.get("sample_id"):
        targets = [args["sample_id"]]
    target = "、".join(targets[:6])
    detail = f"处理对象：{target}" if target else "按当前目标核对相关依据。"
    if result is None:
        if name in {"analyze_materials", "inspect_material_region"}:
            detail += (
                " 检查重点：" + args.get("focus", args.get("region", "材料类别与相关栏位"))[:240]
            )
        return {"title": title, "detail": detail}
    if result.get("status") == "FAILED":
        return {
            "title": title,
            "detail": result["message"],
            "next_action": "保留已有结果，核对失败原因后继续。",
        }
    if name == "get_configuration":
        fields = result.get("maintenance", {}).get("fields", {})
        labels = {
            item["key"]: item.get("label", item["key"])
            for item in result.get("editable", {}).get("fields", [])
        }
        properties = {
            "label": "字段名称",
            "aliases": "识别别名",
            "locator_id": "页面定位标识",
            "locator_name": "页面定位名称",
            "occurrence": "控件位置",
            "evidence": "材料证据来源",
            "mode": "审核方式",
            "normalizer": "比较方式",
            "key": "字段标识",
            "section": "审核分区",
            "page_section": "页面分区",
            "section_required": "分区要求",
            "max_age_days": "材料时效",
        }
        detail = "已读取目标页面配置。"
        if fields:
            detail += " ".join(
                f"{labels.get(key, key)} 当前可维护：{'、'.join(properties.get(p, p) for p in value.get('editable_properties', [])) or '无'}。"
                for key, value in fields.items()
            )
    elif name == "get_configuration_capabilities":
        detail = "已取得当前部署的操作能力和配置约束；具体修改仍需核对目标字段。"
    elif name == "get_page_snapshot":
        detail = f"已读取 {len(result.get('fields', []))} 个字段、{len(result.get('samples', []))} 张图片的采集目录；尚不代表图片已完成分析。"
    elif name in {"analyze_materials", "inspect_material_region"}:
        observations = result.get("observations", [])
        categories = []
        for item in observations:
            if not isinstance(item, dict):
                continue
            category = item.get("material_category") or item.get("category")
            if isinstance(category, str):
                categories.append(f"{item.get('sample_id', '')}：{category[:100]}")
        detail = "材料模型已返回观察结果。" + (
            "；".join(categories) if categories else f"共 {len(observations)} 条观察。"
        )
        detail += " 观察结果需结合业务口径核对，不能据此直接认定审核通过。"
    elif name == "edit_draft":
        detail = f"草稿修订为 {result.get('revision', 0)}，本次产生 {len(result.get('changes', []))} 项差异，尚未发布。"
    elif name == "assess_route":
        detail = (
            "配置操作预检通过，尚未修改草稿。"
            if result.get("route") == "CONFIG"
            else "已记录需要开发接续的具体限制；线上未执行代码。"
        )
    elif name == "read_draft":
        detail = f"已读取草稿修订 {result.get('revision', 0)}；读取不产生配置修改。"
    elif name in {"inspect_candidate", "validate_candidate"}:
        detail = "候选检查已返回，请核对具体检查结果；编译完成不代表业务验收或发布完成。"
    elif name == "ask_user":
        detail = "已提出需要确认的业务问题，请在对话中选择或直接描述。"
    elif name == "request_collection_trial":
        detail = "已请求浏览器只读验证，等待页面返回事实。"
    else:
        detail = "操作已返回结果。" if not target else f"已完成读取：{target}。"
    if result.get("_execution", {}).get("reused_from"):
        detail = "复用本轮相同版本、相同范围的已有结果。" + detail
    return {"title": title, "detail": detail}


async def emit_progress(store, record, event, data):
    data = {"occurred_at": datetime.now(UTC).isoformat(), **data}
    await store.put(record, event, data)


def turn_summary(record):
    if record.turn_status == "CANCELLED":
        detail = "本轮处理已暂停。"
    elif record.pending:
        detail = {
            "question": "等待你确认业务问题，请在对话中回答。",
            "browser": "等待浏览器返回只读验证结果。",
            "confirmation": "等待你审阅验证结果并明确确认发布。",
        }.get(record.pending["kind"], "等待后续处理。")
    elif record.error:
        detail = record.error
    elif record.receipt:
        detail = "已产生发布记录，请继续核对实际加载状态。"
    else:
        detail = "本轮处理已结束。"
    detail += f" 当前草稿修订 {record.revision}。"
    if not record.receipt:
        detail += "已有草稿保留。" if record.revision else "尚未生成配置修改。"
    findings = []
    for message in reversed(record.messages):
        if message.get("_run_id") != record.turn_id:
            continue
        for call in message.get("tool_calls", []):
            name = call["function"]["name"]
            result = record.tool_results.get(call["id"])
            if name not in {
                "get_configuration",
                "analyze_materials",
                "inspect_material_region",
                "edit_draft",
            } or not isinstance(result, dict):
                continue
            # Only normalized, successfully executed calls can have one of these results.
            try:
                args = json.loads(call["function"]["arguments"])
            except ValueError:
                continue
            if not isinstance(args, dict):
                continue
            finding = tool_activity(name, args, result)["detail"]
            if finding not in findings:
                findings.append(finding)
        if len(findings) >= 4:
            break
    return {
        "title": "本轮处理结果",
        "detail": detail,
        "findings": list(reversed(findings[:4])),
        "next_action": "查看修改结果与待办。"
        if record.revision
        else "核对调查依据和待确认项后继续。",
    }
