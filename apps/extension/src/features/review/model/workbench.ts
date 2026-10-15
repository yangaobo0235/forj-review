import type { ReviewTask } from "../../../shared/contracts/review";

export type View = "PENDING" | "ALL" | "EXTERNAL";
export type Decision = "MARKED_EXCEPTION";
export const statusText = {
  MATCH: "一致",
  CONFLICT: "冲突",
  INSUFFICIENT: "待复核",
} as const;
export function cleanPageValue(value: unknown) {
  return String(value ?? "").trim();
}

type ViewSpec = readonly [View, string];

/**
 * 工作台视图表。
 *
 * “页面外核验”放 EXTERNAL 任务（例如二维码官网核验）和未投影的独立
 * BUSINESS_RULE 结论。三个视图始终存在，数量来自实际可见任务。
 */
export function workbenchViews({
  pending,
  fieldCount,
  externalCount,
}: {
  pending: number;
  fieldCount: number;
  externalCount: number;
}): readonly ViewSpec[] {
  return [
    ["PENDING", `待处理 (${pending})`],
    ["ALL", `全部字段 (${fieldCount})`],
    ["EXTERNAL", `页面外核验 (${externalCount})`],
  ];
}

export function fieldFromStep(step: ReviewTask) {
  return step.page_field ?? step.page_target_field ?? null;
}
export function isExternalTask(step: ReviewTask) {
  return step.category === "EXTERNAL" || step.category === "BUSINESS_RULE";
}
export function stepTitle(step: ReviewTask) {
  return step.label;
}
