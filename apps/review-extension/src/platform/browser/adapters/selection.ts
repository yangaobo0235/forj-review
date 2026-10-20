import type { BusinessSelection, BusinessType, Region } from "../../../shared/contracts/review";

/**
 * 页面适配器共用的路径匹配与业务选择构造。
 *
 * 职责边界：只做路径解析和结构构造，不判断具体页面——那是各适配器自己的事。
 */

/** 页面 URL 的路径部分；无法解析时返回空串。 */
export function pagePath(url: string): string {
  try {
    return new URL(url).pathname;
  } catch {
    return "";
  }
}

/** 路径是否命中该前缀。按完整路径段匹配，避免相似前缀互相误判。 */
export function pathMatches(path: string, prefix: string): boolean {
  return path === prefix || path.startsWith(`${prefix}/`);
}

/** 构造一个自动识别得到的业务选择。 */
export function pageSelection(
  businessType: BusinessType,
  region: Region,
): BusinessSelection {
  return {
    businessType,
    region,
    profileVersion: "1.0",
    workflowStage: businessType,
    selectionMode: "AUTO",
    detectionStatus: "CONFIRMED",
  };
}
