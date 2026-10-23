import { manifestDocumentType, manifestGroupLabelEntries } from "../catalog/collect-manifest.ts";

/**
 * 功能：根据页面结构判定材料的业务归属。
 * 职责边界：不把身份或无关区域归入审核材料。
 * 修改日期：2026-08-26
 */

const normalize = (value: unknown) =>
  String(value || "")
    .replace(/\s+/g, "")
    .replace(/[：:]+$/, "");

const groupLabels = () =>
  new Map((manifestGroupLabelEntries() ?? []).map(([label, value]) => [normalize(label), value]));

const scopeForLabel = (text: string) => groupLabels().get(normalize(text)) || null;

/** 序号只用于追踪；材料提示只能来自明确标签。实际类型由图片内容确认。 */
const documentTypeFor = (_businessScope: string, _groupOrder: number, categoryHint: string) =>
  manifestDocumentType(String(categoryHint || "")) || "unknown";

const assign = (items: ({ kind: "label"; text: string } | { kind: "image"; index: number })[]) => {
  let active = { scope: "unknown", title: "未分类资料" };
  const counters = new Map();
  const labelCounters = new Map<string, number>();
  const assigned = [];
  for (const item of items) {
    if (item.kind === "label") {
      const label = normalize(item.text);
      const occurrence = (labelCounters.get(label) || 0) + 1;
      labelCounters.set(label, occurrence);
      const declared = manifestGroupLabelEntries()?.filter(([text]) => normalize(text) === label);
      const next =
        declared?.find(([, group]) => (group.occurrence || 1) === occurrence)?.[1] ||
        (declared?.some(([, group]) => group.occurrence) ? null : scopeForLabel(item.text));
      if (next) active = next;
      continue;
    }
    if (item.kind !== "image") continue;
    const order = (counters.get(active.scope) || 0) + 1;
    counters.set(active.scope, order);
    assigned.push({
      index: item.index,
      businessScope: active.scope,
      groupTitle: active.title,
      groupOrder: order,
      imageId: `${active.scope}-${String(order).padStart(2, "0")}`,
    });
  }
  return assigned;
};

export const ReviewBusinessScope = { scopeForLabel, assign, documentTypeFor };
