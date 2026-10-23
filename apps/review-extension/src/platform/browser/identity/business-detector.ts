import {
  effectivePageCatalog,
  explainPageIdentity,
  matchPageIdentity,
  reviewTabLabels,
} from "../catalog/page-catalog.ts";
import type { ReviewTabState } from "../catalog/page-catalog.ts";
import type { BusinessSelection } from "../../../shared/contracts/review";

/**
 * 功能：识别当前页面属于哪个审核业务，并处理人工选择与自动识别的冲突。
 * 职责边界：页面地址、审核页签及普通页面特征由后端目录下发；
 * 本模块只负责取用清单、判定冲突，不自行维护地址表。
 */

/**
 * 按页面识别清单判定业务；清单不可用时使用后端声明生成的随包目录。
 *
 * 在线目录与随包目录都执行相同的路径、页签及面板校验，不绕过步骤确认。
 */
function detect(
  url: string,
  pageText = "",
  reviewTab?: ReviewTabState | null,
): BusinessSelection | null {
  return matchPageIdentity(effectivePageCatalog(), url, pageText, reviewTab);
}

/**
 * 识别不出来时给审核员看的话。
 *
 * 页签模式说明当前步骤或面板问题；普通配置保留缺失特征词诊断。
 */
function detectionHint(url: string, pageText: string, reviewTab?: ReviewTabState | null): string {
  const labels = reviewTabLabels(effectivePageCatalog(), url);
  if (labels.length) {
    if (!reviewTab?.label) return "未找到唯一激活的审核页签，请打开审核表单后重新采集";
    if (!labels.includes(reviewTab.label))
      return `当前处于「${reviewTab.label}」，请切换到审核页签后重新采集`;
    return "审核页签对应的内容面板尚未显示或关联无效，请等待加载完成后重新采集";
  }
  const diagnostics = explainPageIdentity(effectivePageCatalog(), url, pageText);
  if (!diagnostics.shared) return "无法识别当前审核业务，请人工选择";
  const missing = Object.entries(diagnostics.missingAnchors)
    .map(([name, words]) => `${name} 缺「${words.join("」「")}」`)
    .join("；");
  return `该地址对应 ${diagnostics.byPath.length} 个审核业务，页面特征不足以区分（${missing}），请人工选择`;
}

/**
 * 解析本次审核的业务。
 *
 * 人工选择不能覆盖已确认的页面身份，也不能绕过页签模式的步骤校验。
 * 未声明审核页签的普通配置保留原有识别失败后人工选择的行为。
 */
function resolve(
  url: string,
  pageText = "",
  manualSelection: BusinessSelection | null = null,
  reviewTab?: ReviewTabState | null,
): { business: BusinessSelection | null; error: string | null } {
  const automatic = detect(url, pageText, reviewTab);

  // 列表、详情或切换中的面板不能通过人工选择变成可审核步骤。
  if (!automatic && reviewTabLabels(effectivePageCatalog(), url).length) {
    return { business: null, error: detectionHint(url, pageText, reviewTab) };
  }

  if (!manualSelection) {
    return {
      business: automatic,
      error: automatic ? null : detectionHint(url, pageText, reviewTab),
    };
  }

  if (
    automatic &&
    (automatic.businessType !== manualSelection.businessType ||
      automatic.region !== manualSelection.region ||
      (automatic.pageId && manualSelection.pageId && automatic.pageId !== manualSelection.pageId))
  ) {
    return { business: null, error: "人工选择的审核业务与当前页面不一致，请重新确认" };
  }

  const identity = effectivePageCatalog().identities.find(
    (i) =>
      i.business_type === manualSelection.businessType &&
      i.region === manualSelection.region &&
      (!manualSelection.pageId || i.page_id === manualSelection.pageId),
  );
  return {
    business: {
      ...manualSelection,
      ...(identity?.page_id ? { pageId: identity.page_id } : {}),
      profileVersion: identity ? identity.profile_version || "1.0" : manualSelection.profileVersion,
      selectionMode: "MANUAL" as const,
      detectionStatus: "CONFIRMED",
    },
    error: null,
  };
}

export const ReviewBusinessDetector = { detect, resolve };
