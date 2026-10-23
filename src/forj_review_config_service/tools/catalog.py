"""One registry defines actual model tools and their strict argument schemas."""

from forj_review_config_service.tools.arguments import argument_schema

TOOL_DESCRIPTIONS = {
    "read_draft": "按field_keys读取当前草稿字段及页面选项；省略字段时只返回字段摘要，查完整字段前明确目标",
    "read_tool_result": "按原始tool_result_id和JSON pointer读取目录中的相关子树；仅子树过大时按next_offset分页。不顺序翻读整页配置或全部Schema，不重复调用有副作用的工具",
    "manage_requirements": "先解释当前用户消息：ACCEPT新增目标，REPLACE更正指定原目标，WITHDRAW撤回，QUERY仅查询；绑定message_id，只有有效目标参与验收，不能通过撤回未完成目标使验证通过",
    "assess_route": "先读取并引用get_configuration与get_configuration_capabilities。CONFIG必须提供operations，由实际编辑内核预检；不能用通用Schema推翻字段维护限制。LOCAL_CODE引用具体限制，不能因预算或一次失败认定需要代码。预检不修改草稿",
    "get_page_snapshot": "读取页面候选、材料描述和诊断；字段原值仅显示已引用项；先查候选再按需引用，不要求用户全量勾选",
    "reference_page_items": "按需引用当前现场字段或材料作为调查依据；不代表保留审核项、修改目标或必传要求。引用后重新读取现场可见字段原值",
    "find_templates": "列出实际模板及独立验收样例集",
    "get_configuration": "优先填写field_keys和material_types，一次查询目标字段、相关材料说明、实际依赖和维护限制。省略筛选返回整页目录，按pointer补查；source/editable/effective是同页不同视图，不需全部读取",
    "get_configuration_capabilities": "查询实际操作能力、规则与Schema目录；通常按maintenance直接编辑，属性结构不清时按schema目录pointer读取相关定义，无需翻读全部Schema",
    "analyze_materials": "按sample_ids读取本任务所需图片并自动记录引用，不全量分析；图片不可读须补充，不猜测",
    "inspect_material_region": "重新查看样本并聚焦指定位置，不生成裁剪上传",
    "edit_draft": "已有字段名称/label用upsert_field，value={key:稳定字段键,label:新名称}；无专用依赖的SYSTEM输入接入材料比较用set_field_comparison，value={key,mode,evidence,normalizer}；upsert_material可修改guidance和details.scoped_guidance，保留其他details；remove_field/set_collection_binding；优先用set_material_policy/set_policy_dates/set_presentation修改材料策略、政策日期和任务展示（不是字段label）；其他已有选项用set_page_options；set_field_permissions单独提出权限变化。禁止页面身份、实现代码或未支持参数。返回实际差异，改动使旧验证失效",
    "record_decision": "记录规则依据。字段映射写 candidate_id/field_key，材料写 sample_id/material；用户要求处理写 requirement_id/status。来源 evidence_refs 必填；不能伪造用户确认",
    "inspect_candidate": "编译候选，返回具体变化与摘要；COMPILED只代表编译/依赖检查，完整发布验证仍需提交。用户先保留候选且缺现场时说明待补项并结束本轮",
    "validate_candidate": "编译验证，不等于已完成或已保存",
    "request_collection_trial": "请求浏览器按候选清单只读试采集",
    "run_replay": "对固定独立基准执行真实审核图；不可修改基准以通过",
    "ask_user": "只有查证后业务仍不明确时才提问；独立调用，暂停等待真实回答",
    "submit_candidate": "提交强制验证，验证通过后由用户单独确认保存；不能声称已启用",
}


def configuration_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": argument_schema(name),
            },
        }
        for name, description in TOOL_DESCRIPTIONS.items()
    ]
