import { manifestHintType } from "../catalog/collect-manifest.ts";
import { documentTypeForImageLabel } from "./image-label.ts";
import { ReviewBusinessScope } from "./business-scope.ts";

/** 页面材料提示的解释器。正式采集和配置试运行必须使用同一套规则。 */
export const classifyImage = (hint: string) => {
  // 先问清单：材料关键词和它对应的类型是业务声明，后端是唯一来源。
  // 内置表只兜清单不可用的情况——过户的「二手车发票」在内置表里根本没有，
  // 只会命中宽泛的"发票"，判成别的业务的材料类型。
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

export function imageDocumentType(candidate: {
  labelHint?: string; businessScope: string; groupOrder?: number; categoryHint?: string;
}): string {
  return documentTypeForImageLabel(candidate.labelHint || "")
    || ReviewBusinessScope.documentTypeFor(candidate.businessScope, candidate.groupOrder ?? 0, candidate.categoryHint || "unknown");
}
