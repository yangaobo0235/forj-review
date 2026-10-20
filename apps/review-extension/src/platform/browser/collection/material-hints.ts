import { manifestHintType } from "../catalog/collect-manifest.ts";
import { documentTypeForImageLabel } from "./image-label.ts";
import { ReviewBusinessScope } from "./business-scope.ts";

/** 页面材料提示的解释器。正式采集和配置试运行必须使用同一套规则。 */
export const classifyImage = (hint: string) => {
  const group = ReviewBusinessScope.scopeForLabel(hint);
  // 整个分区的标题不是单图材料名；尤其“新车及发票信息”不能把全组判成发票。
  if (group && !["identity", "business_license"].includes(group.scope)) return group.scope;
  // 先问清单：材料关键词和它对应的类型是业务声明，后端是唯一来源。
  // 内置表只兜清单不可用的情况，避免宽泛关键词覆盖动态材料声明。
  const declared = manifestHintType(hint);
  if (declared) return declared;
  const text = hint.toLowerCase();
  if (text.includes("身份证")) return "id_card";
  if (text.includes("营业执照")) return "business_license";
  // 铭牌必须先于登记证书判断：上传说明常把“登记证书和铭牌二选一”写在同一句里，
  // 先命中的关键词会决定图片类型。
  if (text.includes("铭牌")) return "vehicle_nameplate";
  if (text.includes("行驶证")) return "vehicle_license";
  if (text.includes("登记证书") || text.includes("机动车登记证")) return "registration_certificate";
  if (text.includes("回收证明") || text.includes("报废证明")) return "scrap_certificate";
  if (text.includes("发票")) return "invoice";
  if (text.includes("报废车辆资料") || text.includes("报废车辆信息") || text.includes("报废车资料") || text.includes("旧车资料")) return "old_vehicle";
  if (text.includes("新车资料") || text.includes("新车及发票信息") || text.includes("新车及发票资料")) return "new_vehicle";
  return "unknown";
};

/** 仅解释当前图片卡片的面别标签，不决定业务必传规则。 */
export function identitySidesForLabel(label: string): Array<"FRONT" | "BACK"> {
  const text = label.replace(/\s|[:：]/g, "");
  if (/^身份证[（(]?(正面|人像面)[）)]?$/.test(text)) return ["FRONT"];
  if (/^身份证[（(]?(反面|国徽面)[）)]?$/.test(text)) return ["BACK"];
  return [];
}

export function imageDocumentType(candidate: {
  labelHint?: string; businessScope: string; groupOrder?: number; categoryHint?: string;
}): string {
  return documentTypeForImageLabel(candidate.labelHint || "")
    || ReviewBusinessScope.documentTypeFor(candidate.businessScope, candidate.groupOrder ?? 0, candidate.categoryHint || "unknown");
}
