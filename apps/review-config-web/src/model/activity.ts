import type { ConfigurationEvent } from "@forj/configuration-contracts";
import { progressLabel } from "./presentation";

export interface ActivityItem {
  id: string;
  seq: number;
  runId: string;
  title: string;
  detail: string;
  nextAction?: string;
  status: string;
  startedAt?: string;
  durationMs?: number;
  tool?: string;
  code?: string;
  callId?: string;
}

/** Pair actual lifecycle events; a successful read never becomes a successful edit. */
export function activityItems(events: ConfigurationEvent[]): ActivityItem[] {
  const items = new Map<string, ActivityItem>();
  for (const event of events) {
    const data = event.data,
      runId = event.run_id ?? String(data.turn_id ?? "legacy");
    const tool = typeof data.name === "string" ? data.name : undefined;
    const callId =
      typeof data.call_id === "string"
        ? data.call_id
        : typeof data.model_call_id === "string"
          ? data.model_call_id
          : undefined;
    if (["question_answered", "browser_task_completed"].includes(event.type)) {
      for (const item of items.values()) {
        if (
          item.runId === runId &&
          item.status === "WAITING" &&
          item.tool ===
            (event.type === "question_answered" ? "ask_user" : "request_collection_trial")
        ) {
          item.status = "COMPLETED";
          item.detail =
            event.type === "question_answered"
              ? "已收到你的业务确认，继续处理。"
              : "已收到浏览器只读验证结果，继续检查。";
        }
      }
      continue;
    }
    if (
      data.parent_call_id ||
      ![
        "tool_started",
        "tool_completed",
        "model_started",
        "model_completed",
        "model_failed",
        "turn_started",
        "turn_stopped",
        "question_pending",
        "browser_task_pending",
      ].includes(event.type)
    )
      continue;
    if (event.type === "model_completed" && !callId) continue;
    const id = `${runId}:${callId ?? event.seq}`;
    const previous = items.get(id);
    const activity = data.activity;
    let status = event.type.endsWith("started") ? "RUNNING" : String(data.status ?? "COMPLETED");
    if (event.type.endsWith("pending")) status = "WAITING";
    if (event.type === "tool_completed" && status === "SUCCEEDED") status = "COMPLETED";
    if (event.type === "turn_started") status = "COMPLETED";
    if (event.type === "turn_stopped") {
      for (const item of items.values()) {
        if (item.runId === runId && item.status === "RUNNING") {
          item.status = status === "CANCELLED" ? "CANCELLED" : "INTERRUPTED";
          if (item.startedAt && event.created_at)
            item.durationMs = Math.max(
              0,
              Date.parse(event.created_at) - Date.parse(item.startedAt),
            );
        }
        if (item.runId === runId && item.status === "WAITING" && status !== "WAITING")
          item.status =
            status === "COMPLETED"
              ? "COMPLETED"
              : status === "CANCELLED"
                ? "CANCELLED"
                : "INTERRUPTED";
      }
    }
    // Model waiting is useful while active. Completed model calls remain in diagnostics,
    // while the business operation they selected carries the user-facing progress.
    if (event.type === "model_completed") {
      items.delete(id);
      continue;
    }
    const title =
      activity?.title ??
      (event.type === "turn_started" ? "开始处理本轮需求" : progressLabel([event]));
    const detail =
      activity?.detail ??
      (status === "FAILED"
        ? "操作失败，旧记录未保存业务摘要，可通过诊断编号定位日志。"
        : event.type === "turn_stopped"
          ? String(data.error ?? "本轮已结束；请核对修改结果和待办。")
          : "旧记录仅保存了执行状态，没有业务发现摘要。");
    items.set(id, {
      id,
      seq: previous?.seq ?? event.seq,
      runId,
      title,
      detail,
      nextAction: activity?.next_action,
      status,
      tool,
      callId,
      startedAt: previous?.startedAt ?? event.created_at ?? String(data.occurred_at ?? ""),
      durationMs: typeof data.duration_ms === "number" ? data.duration_ms : undefined,
      code: typeof data.code === "string" ? data.code : undefined,
    });
  }
  return [...items.values()];
}

export const activityStatus: Record<string, string> = {
  RUNNING: "进行中",
  COMPLETED: "已返回",
  SUCCEEDED: "已返回",
  WAITING: "等待处理",
  FAILED: "失败",
  INTERRUPTED: "已中断",
  CANCELLED: "已暂停",
};
