/**
 * 功能：生成证据来源和冲突高亮展示计划。
 * 职责边界：不改变原始证据值。
 * 修改日期：2026-08-26
 */

import { manifestMaterialLabels } from "../../../platform/browser/catalog/collect-manifest.ts";
import type { EvidenceFact } from "../../../shared/contracts/review";

/** 材料名称来自当前清单。 */
function materialLabel(documentType: string | null | undefined): string | null {
  if (!documentType) return null;
  return (
    manifestMaterialLabels()?.[documentType] ??
    null
  );
}

export interface EvidenceHighlightPlan {
  compareTo?: string;
  markAll?: boolean;
}

/** 为每条证据生成多数基准、双向比较或整体冲突的展示计划。 */
export function evidenceHighlightPlans(
  evidence: EvidenceFact[],
): EvidenceHighlightPlan[] {
  const valid = evidence
    .map((item, index) => ({ item, index, value: String(item.value ?? "") }))
    .filter(({ value }) => value.trim());
  const majority = valid.find(({ item }) => item.conflicting === false);

  if (majority) {
    return evidence.map((item) =>
      item.conflicting === true ? { compareTo: majority.value } : {},
    );
  }

  if (valid.length === 2 && valid.every(({ item }) => item.conflicting === true)) {
    const targets = new Map([
      [valid[0].index, valid[1].value],
      [valid[1].index, valid[0].value],
    ]);
    return evidence.map((_, index) => {
      const compareTo = targets.get(index);
      return compareTo === undefined ? {} : { compareTo };
    });
  }

  if (valid.length > 2 && valid.every(({ item }) => item.conflicting === true)) {
    const validIndices = new Set(valid.map(({ index }) => index));
    return evidence.map((_, index) => validIndices.has(index) ? { markAll: true } : {});
  }

  return evidence.map(() => ({}));
}

export function comparisonEvidence(
  evidence: EvidenceFact[],
  pageValue: string | number | null | undefined,
): EvidenceFact[] {
  const valid = evidence.filter(
    (item) => item.value != null && String(item.value).trim(),
  );
  const pageSources = new Set(["页面右侧字段", "申请页面字段"]);
  const imageEvidence = valid
    .filter((item) => !pageSources.has(item.source))
    .sort((left, right) => {
      const priority = (item: EvidenceFact) =>
        item.document_type === "invoice" ? 0
          : item.document_type === "registration_certificate" ? 1
            : 2;
      return priority(left) - priority(right);
    });
  const pageEvidence = valid.filter(
    (item) => pageSources.has(item.source),
  );
  if (pageEvidence.length) return [...pageEvidence, ...imageEvidence];
  return [
    {
      source: "页面右侧字段",
      value:
        pageValue != null && String(pageValue).trim() ? pageValue : "未采集",
    },
    ...imageEvidence,
  ];
}

export function evidencePresentation(evidence: Pick<EvidenceFact, "source" | "value" | "document_type" | "business_scope" | "group_order">) {
  const value = String(evidence.value);
  if (evidence.source === "页面右侧字段" || evidence.source === "申请页面字段") {
    return { kind: "page" as const, label: "申请页面", value };
  }
  if (evidence.source === "二维码官网字段") {
    return { kind: "page" as const, label: "二维码官网", value };
  }
  // 材料名以清单为准；清单里没有这个名字（例如后端刚新增了材料类型而清单
  // 还没重新拉取）时退回内置表，再退回「图片证据」，不编造名字。
  return {
    kind: "image" as const,
    label: materialLabel(evidence.document_type) ?? "图片证据",
    value,
  };
}
