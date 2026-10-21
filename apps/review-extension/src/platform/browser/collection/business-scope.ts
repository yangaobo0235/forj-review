
import {
  manifestDocumentType,
  manifestGroupLabelEntries,
  manifestSlotEntries,
} from "../catalog/collect-manifest.ts";

/**
 * 功能：根据页面结构判定材料的业务归属。
 * 职责边界：不把身份或无关区域归入审核材料。
 * 修改日期：2026-08-26
 */


  const normalize = (value: unknown) => String(value || "").replace(/\s+/g, "").replace(/[：:]+$/, "");

  const groupLabels = () => new Map(
    (manifestGroupLabelEntries() ?? []).map(([label, value]) => [normalize(label), value]),
  );

  const scopeForLabel = (text: string) => groupLabels().get(normalize(text)) || null;

  /** 仅使用后端显式声明的槽位；清单缺失时保留未知类型。 */
  const slotsFor = () => {
    const entries = manifestSlotEntries();
    return new Map(entries ?? []);
  };

  const documentTypeFor = (businessScope: string, groupOrder: number, categoryHint: string) =>
    manifestDocumentType(String(categoryHint || ""))
    || slotsFor().get(`${businessScope || "unknown"}:${groupOrder || 0}`)
    || "unknown";

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
        const next = declared?.find(([, group]) => (group.occurrence || 1) === occurrence)?.[1]
          || (declared?.some(([, group]) => group.occurrence) ? null : scopeForLabel(item.text));
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
