import { buildPageAdapterRegistry } from "../adapters/index.ts";
import { appliedPageCatalog, explainPageIdentity, matchPageIdentity } from "./page-catalog.ts";
import type { BusinessSelection } from "../types/review";

/**
 * 功能：识别当前页面属于哪个审核业务，并处理人工选择与自动识别的冲突。
 * 职责边界：识别依据的页面地址与特征文案由后端下发（`browser/page-catalog.ts`）；
 * 本模块只负责取用清单、判定冲突，不自行维护地址表。
 */

const registry = buildPageAdapterRegistry();

/**
 * 按页面识别清单判定业务；清单不可用时退回页面适配器内置表。
 *
 * 清单可用但给不出唯一答案时**不退回内置表**：内置表只有地址，对共享地址
 * （一致性审核与过户审核同址）必然给出一个错误答案，退回等于把「不猜」作废。
 */
function detect(url: string, pageText = ""): BusinessSelection | null {
  const catalog = appliedPageCatalog();
  if (catalog) return matchPageIdentity(catalog, url, pageText);
  return registry.selection(url, pageText);
}

/**
 * 识别不出来时给审核员看的话。
 *
 * 共用地址上"认不出来"要说明**是缺哪个词**：只说"无法识别"的话，审核员只能
 * 猜，而排查的人拿不到任何线索——识别错和识别不出来都不报错，是这套机制里
 * 最难发现的一类失效。
 */
function detectionHint(url: string, pageText: string): string {
  const diagnostics = explainPageIdentity(appliedPageCatalog(), url, pageText);
  if (!diagnostics.shared) return "无法识别当前审核业务，请人工选择";
  const missing = Object.entries(diagnostics.missingAnchors)
    .map(([name, words]) => `${name} 缺「${words.join("」「")}」`)
    .join("；");
  return `该地址对应 ${diagnostics.byPath.length} 个审核业务，页面特征不足以区分（${missing}），请人工选择`;
}

/**
 * 解析本次审核的业务。
 *
 * 自动识别唯一且与人工选择矛盾时返回错误而不是以人工为准——否则会出现用
 * 长春的规则审青岛的单子。但**自动识别认不出来时人工说了算**：一致性审核与
 * 过户审核共用地址，自动识别本来就给不出答案，此时若还按「页面说了算」拒绝
 * 人工，审核员就永远选不进去。判断依据是「页面说自己是什么」，不是「页面
 * 认不出来所以页面有话说」。
 */
function resolve(
  url: string,
  pageText = "",
  manualSelection: BusinessSelection | null = null,
): { business: BusinessSelection | null; error: string | null } {
  const automatic = detect(url, pageText);

  if (!manualSelection) {
    return {
      business: automatic,
      error: automatic ? null : detectionHint(url, pageText),
    };
  }

  if (
    automatic
    && (automatic.businessType !== manualSelection.businessType
      || automatic.region !== manualSelection.region)
  ) {
    return { business: null, error: "人工选择的审核业务与当前页面不一致，请重新确认" };
  }

  return {
    business: {
      ...manualSelection,
      profileVersion: appliedPageCatalog()?.identities.find(i => i.business_type === manualSelection.businessType && i.region === manualSelection.region)?.profile_version || manualSelection.profileVersion,
      selectionMode: "MANUAL" as const,
      detectionStatus: "CONFIRMED",
    },
    error: null,
  };
}

export const ReviewBusinessDetector = { detect, resolve };
