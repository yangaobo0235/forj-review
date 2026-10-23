import type { ConfigurationEvent, ConfigurationTask } from "@forj/configuration-contracts";

export const stateLabels: Record<string, string> = {
  DRAFTING: "配置草稿",
  NEEDS_INPUT: "等待补充",
  READY_FOR_CONFIRMATION: "待确认保存",
  PUBLISHED: "已发布",
  CLOSED: "已关闭",
  BLOCKED: "需要处理",
};

export function questionOptions(options: unknown): string[] {
  return Array.isArray(options)
    ? options.filter(
        (option): option is string => typeof option === "string" && Boolean(option.trim()),
      )
    : [];
}

const toolLabels: Record<string, string> = {
  get_page_snapshot: "阅读页面字段",
  find_templates: "查找已有页面",
  get_configuration: "读取已有配置",
  get_configuration_capabilities: "检查可用审核能力",
  read_draft: "核对待改字段",
  read_tool_result: "查阅相关配置依据",
  manage_requirements: "整理修改目标",
  assess_route: "判断配置修改方式",
  reference_page_items: "关联现场依据",
  analyze_materials: "识别图片材料",
  inspect_material_region: "核对材料细节",
  edit_draft: "更新配置草稿",
  record_decision: "记录字段映射依据",
  validate_candidate: "校验页面配置",
  inspect_candidate: "检查配置差异",
  run_replay: "回放独立验收样例",
  submit_candidate: "提交候选验证",
};

export function progressLabel(events: ConfigurationEvent[]) {
  for (const event of [...events].reverse()) {
    if (event.data.activity) return event.data.activity.title;
    if (event.type === "turn_stopped")
      return (
        { CANCELLED: "处理已暂停", WAITING: "等待后续处理", COMPLETED: "本轮处理已结束" }[
          String(event.data.status) as "CANCELLED" | "WAITING" | "COMPLETED"
        ] ?? "本轮处理已中断"
      );
    if (["tool_started", "tool_completed"].includes(event.type))
      return toolLabels[String(event.data.name)] ?? "正在核对配置";
    if (event.type === "progress" && typeof event.data.stage === "string") return event.data.stage;
    if (event.type === "turn_started") return "正在整理页面和业务要求";
  }
  return "正在分析业务要求";
}

export const isRunning = (session: ConfigurationTask | null) =>
  Boolean(session && ["RUNNING", "QUEUED"].includes(session.turn_status));

export function displayTime(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
