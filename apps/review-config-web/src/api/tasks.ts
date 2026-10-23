import type { ConfigurationTask } from "@forj/configuration-contracts";
import { request } from "./client";

/** 任务结论与对话由配置服务投影；缺少投影时不能改用模型原始消息或自行推断。 */
export async function readTask(taskId: string, signal?: AbortSignal): Promise<ConfigurationTask> {
  const value = await request<ConfigurationTask>(
    `/tasks/${encodeURIComponent(taskId)}`,
    undefined,
    undefined,
    signal,
  );
  const view = value?.task;
  if (
    !view ||
    typeof view.label !== "string" ||
    typeof view.stage !== "string" ||
    !Number.isInteger(view.progress_step) ||
    typeof view.can_publish !== "boolean" ||
    typeof view.complete !== "boolean" ||
    !Array.isArray(view.coverage) ||
    !view.coverage.every(
      (item) =>
        item &&
        typeof item.requirement_id === "string" &&
        typeof item.text === "string" &&
        typeof item.resolved === "boolean" &&
        typeof item.verified === "boolean",
    ) ||
    !Array.isArray(value.chat_messages) ||
    !value.chat_messages.every(
      (message) =>
        message &&
        ["assistant", "user"].includes(message.role) &&
        typeof message.content === "string",
    )
  )
    throw new Error("配置服务返回的任务视图不完整，请更新并重启本机配置后端后重试");
  return value;
}
