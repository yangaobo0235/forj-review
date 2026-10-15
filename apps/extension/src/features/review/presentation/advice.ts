/**
 * 功能：把建议和跨材料检查转换为界面文案。
 * 职责边界：不改变检查状态或结论。
 * 修改日期：2026-08-26
 */

import type { Recommendation, CheckResult, CheckResultStatus } from "../../../shared/contracts/review";

export function checkStatusLabel(status: CheckResultStatus): string {
  return { MATCH: "满足", CONFLICT: "不满足", INSUFFICIENT: "无法校验" }[status];
}

export function recommendationVisual(recommendation: Recommendation): {
  symbol: string;
  label: string;
} {
  return {
    PASS: { symbol: "✓", label: "审核建议：通过" },
    REVIEW_REQUIRED: { symbol: "!", label: "审核建议：需要人工复核" },
  }[recommendation];
}

export function confidenceLabel(confidence: number | null | undefined): string | null {
  return confidence == null ? null : `图片识别平均置信度：${Math.round(confidence * 100)}%`;
}

export function orderedCrossChecks(
  _businessType: string,
  checks: CheckResult[],
): CheckResult[] {
  return checks;
}
