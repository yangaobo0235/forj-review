/**
 * 功能：呈现当前材料的证据来源标签。
 * 职责边界：不改变原始证据值。
 * 修改日期：2026-08-26
 */

import { manifestMaterialLabels } from "../../../platform/browser/catalog/collect-manifest.ts";
import type { EvidenceFact } from "../../../shared/contracts/review";

/** 材料名称来自当前清单。 */
function materialLabel(documentType: string | null | undefined): string | null {
  if (!documentType) return null;
  return manifestMaterialLabels()?.[documentType] ?? null;
}

export function evidencePresentation(
  evidence: Pick<
    EvidenceFact,
    "source" | "value" | "document_type" | "business_scope" | "group_order"
  >,
) {
  const value = String(evidence.value);
  if (evidence.source === "页面右侧字段" || evidence.source === "申请页面字段") {
    return { kind: "page" as const, label: "申请页面", value };
  }
  if (evidence.source === "二维码官网字段") {
    return { kind: "page" as const, label: "二维码官网", value };
  }
  // 材料名以清单为准；清单里没有这个名字（例如后端刚新增了材料类型而清单
  // 还没重新拉取）时显示「图片证据」，不编造材料名字。
  return {
    kind: "image" as const,
    label: materialLabel(evidence.document_type) ?? "图片证据",
    value,
  };
}
