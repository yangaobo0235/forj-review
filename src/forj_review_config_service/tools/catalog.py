"""One registry defines actual model tools and their strict argument schemas."""

from forj_review_config_service.tools.arguments import argument_schema

TOOL_DESCRIPTIONS = {
    'get_page_snapshot': '读取本会话页面现场、选择范围与诊断',
    'find_templates': '列出实际模板及独立验收样例集',
    'get_configuration': '读取指定页面的源配置、有效配置和材料/字段语义',
    'get_configuration_capabilities': '读取真实 PageDefinition JSON Schema、规则 ID 与依赖，编辑前查询',
    'analyze_materials': '查看已选样本，发现材料字段；图片不可读须补充，不猜测',
    'inspect_material_region': '重新查看样本并聚焦指定位置，不生成裁剪上传',
    'edit_draft': '维护已有页面：upsert_field/remove_field/set_collection_binding；set_page_options 修改已登记规则绑定、政策、材料要求、展示与动作；upsert_material/remove_material；set_field_permissions 单独提出权限变化。禁止页面身份、实现代码或未支持参数。所有改动使旧验证失效',
    'record_decision': '记录规则依据。字段映射写 candidate_id/field_key，材料写 sample_id/material；用户要求处理写 requirement_id/status。来源 evidence_refs 必填；不能伪造用户确认',
    'inspect_candidate': '编译候选并查看差异、清单和摘要',
    'validate_candidate': '编译验证，不等于已完成或已保存',
    'request_collection_trial': '请求浏览器按候选清单只读试采集',
    'run_replay': '对固定独立基准执行真实审核图；不可修改基准以通过',
    'ask_user': '只有查证后业务仍不明确时才提问；独立调用，暂停等待真实回答',
    'submit_candidate': '提交强制验证，验证通过后由用户单独确认保存；不能声称已启用',
}


def configuration_tools() -> list[dict]:
    return [{"type": "function", "function": {"name": name, "description": description,
             "parameters": argument_schema(name)}} for name, description in TOOL_DESCRIPTIONS.items()]
