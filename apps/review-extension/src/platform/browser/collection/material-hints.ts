import { manifestHintType } from "../catalog/collect-manifest.ts";
import { documentTypeForImageLabel } from "./image-label.ts";
import { ReviewBusinessScope } from "./business-scope.ts";

/** 页面材料提示的解释器。正式采集和配置试运行必须使用同一套规则。 */
export const classifyImage = (hint: string) => {
  const group = ReviewBusinessScope.scopeForLabel(hint);
  // 整个分区的标题不是单图材料名；尤其“新车及发票信息”不能把全组判成发票。
  if (group && !["identity", "business_license"].includes(group.scope)) return group.scope;
  return manifestHintType(hint) ?? "unknown";
};

/** 仅解释当前图片卡片的面别标签，不决定业务必传规则。 */
export function identitySidesForLabel(label: string): Array<"FRONT" | "BACK"> {
  const text = label.replace(/\s|[:：]/g, "");
  if (/^身份证[（(]?(正面|人像面)[）)]?$/.test(text)) return ["FRONT"];
  if (/^身份证[（(]?(反面|国徽面)[）)]?$/.test(text)) return ["BACK"];
  return [];
}

export function imageDocumentType(candidate: {
  labelHint?: string;
  businessScope: string;
  groupOrder?: number;
  categoryHint?: string;
}): string {
  return (
    documentTypeForImageLabel(candidate.labelHint || "") ||
    ReviewBusinessScope.documentTypeFor(
      candidate.businessScope,
      candidate.groupOrder ?? 0,
      candidate.categoryHint || "unknown",
    )
  );
}
