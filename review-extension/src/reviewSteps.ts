import type { ReviewTask } from "./types/review";

/** Business visibility is projected by the backend. Retired material tasks
 * from older servers or restored responses are excluded before any UI counts. */
export function isFieldFirstTaskVisible(
  step: ReviewTask,
): boolean {
  return step.visible !== false
    && step.category !== "MATERIAL"
    && !isMaterialDiagnostic(step.step_id.replace(/^BUSINESS-/, ""));
}

/** IDs of the retired completeness summary and its diagnostic findings. */
export function isMaterialDiagnostic(checkId: string): boolean {
  return checkId.startsWith("MATERIAL-");
}

export function isQrTask(step: ReviewTask): boolean {
  return step.step_id === "QR-GROUP";
}
