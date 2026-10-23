/**
 * 页面识别清单：后端下发的「管理端审核页地址 → 业务、地区、页面特征文案」。
 *
 * 职责边界：只做结构转换、暂存和匹配，不判断哪个业务更重要、不猜。
 *
 * 报废置换保持路径识别；声明 review_tab 的页面必须由当前激活页签及面板确认。
 * DOM 定位留在浏览器平台，目录只声明页签名称，不包含选择器或节点。
 */
import type { BusinessSelection, BusinessType, Region } from "../../../shared/contracts/review";

export interface PageIdentityDeclaration {
  business_type: string;
  region: string;
  profile_version?: string;
  title?: string;
  page_id?: string;
  /** 该页面的 URL 路径，可多条。 */
  paths: string[];
  /** 页面特征文案，要求**全部**出现才算命中；空数组表示不看特征。 */
  anchors: string[];
  /** 当前审核步骤的激活页签；有此声明时不使用整页关键词识别。 */
  review_tab?: string;
}

export interface ReviewTabState {
  label: string;
  panelReady: boolean;
}

export interface PageCatalog {
  version: string;
  identities: PageIdentityDeclaration[];
}

let applied: PageCatalog | null = null;

function isIdentity(value: unknown): value is PageIdentityDeclaration {
  const item = value as PageIdentityDeclaration | null;
  return (
    Boolean(item) &&
    typeof item?.business_type === "string" &&
    typeof item?.region === "string" &&
    Array.isArray(item?.paths) &&
    Array.isArray(item?.anchors) &&
    (item?.review_tab === undefined ||
      (typeof item.review_tab === "string" && Boolean(item.review_tab.trim())))
  );
}

function isValid(value: unknown): value is PageCatalog {
  const catalog = value as PageCatalog | null;
  return (
    Boolean(catalog) &&
    Array.isArray(catalog?.identities) &&
    catalog.identities.length > 0 &&
    catalog.identities.every(isIdentity)
  );
}

/** 应用后端下发的页面识别清单；传空则清空，不保留旧页面身份。 */
export function applyPageCatalog(catalog: PageCatalog | null | undefined): void {
  applied = isValid(catalog) ? catalog : null;
}

/** 当前生效的页面识别清单；不可用时返回 null。 */
export function appliedPageCatalog(): PageCatalog | null {
  return applied;
}

/** 页面身份只取当前服务下发的配置。 */
export function effectivePageCatalog(): PageCatalog {
  return applied ?? { version: "1.0", identities: [] };
}

export async function fetchPageCatalog(
  baseUrl: string,
  fetcher: typeof fetch = fetch,
): Promise<PageCatalog | null> {
  try {
    const response = await fetcher(`${baseUrl}/api/review/page-catalog`, {
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) return null;
    const catalog = await response.json();
    return isValid(catalog) ? catalog : null;
  } catch {
    return null;
  }
}

/** Invalid URLs never match a configured page. */
function pagePath(url: string): string {
  try {
    return new URL(url).pathname;
  } catch {
    return "";
  }
}

/** 路径是否命中该前缀。按完整路径段匹配，避免相似前缀互相误判。 */
function pathMatches(path: string, prefix: string): boolean {
  return path === prefix || path.startsWith(`${prefix}/`);
}

export function pageIdentitiesForUrl(
  catalog: PageCatalog | null,
  url: string,
): PageIdentityDeclaration[] {
  const path = pagePath(url);
  return (catalog?.identities ?? []).filter((item) =>
    item.paths.some((route) => pathMatches(path, route)),
  );
}

export function reviewTabLabels(catalog: PageCatalog | null, url: string): string[] {
  return [
    ...new Set(
      pageIdentitiesForUrl(catalog, url).flatMap((item) =>
        item.review_tab ? [item.review_tab] : [],
      ),
    ),
  ];
}

/** 页面特征是否全部出现。空特征集视为命中——那表示这条声明不看内容。 */
function anchorsPresent(anchors: string[], text: string): boolean {
  if (!anchors.length) return true;
  return anchors.every((anchor) => text.includes(anchor));
}

function toSelection(identity: PageIdentityDeclaration): BusinessSelection {
  return {
    ...(identity.page_id ? { pageId: identity.page_id } : {}),
    businessType: identity.business_type as BusinessType,
    region: identity.region as Region,
    profileVersion: identity.profile_version || "1.0",
    workflowStage: identity.business_type as BusinessType,
    selectionMode: "AUTO",
    detectionStatus: "CONFIRMED",
  };
}

/** 识别诊断：路径候选、审核页签匹配结果及普通页面缺失的特征词。 */
export interface PageMatchDiagnostics {
  path: string;
  /** 地址命中的声明，按「业务/地区」列出。 */
  byPath: string[];
  /** 满足页签或普通特征条件的声明。 */
  matched: string[];
  /** 每条候选各自缺哪些特征词。 */
  missingAnchors: Record<string, string[]>;
  /** 这条地址被多个业务共用。 */
  shared: boolean;
  activeTab?: string;
}

function label(item: PageIdentityDeclaration): string {
  return `${item.business_type}/${item.region}`;
}

function missingAnchors(item: PageIdentityDeclaration, text: string): string[] {
  return item.anchors.filter((anchor) => !text.includes(anchor));
}

/** 识别过程的可读记录，用来解释"为什么认成了这个业务"。 */
export function explainPageIdentity(
  catalog: PageCatalog | null,
  url: string,
  text = "",
  reviewTab?: ReviewTabState | null,
): PageMatchDiagnostics {
  const path = pagePath(url);
  const byPath = pageIdentitiesForUrl(catalog, url);
  const usesTabs = byPath.some((item) => item.review_tab);
  const matched = byPath.filter((item) =>
    usesTabs
      ? Boolean(reviewTab?.panelReady && item.review_tab === reviewTab.label)
      : !missingAnchors(item, text).length,
  );
  return {
    path,
    byPath: byPath.map(label),
    matched: matched.map(label),
    missingAnchors: Object.fromEntries(
      byPath.map((item) => [label(item), missingAnchors(item, text)]),
    ),
    shared: byPath.length > 1,
    ...(usesTabs ? { activeTab: reviewTab?.label || "" } : {}),
  };
}

/**
 * 在清单里判定当前页面属于哪个业务；给不出唯一答案时返回 null。
 *
 * 按路径缩小候选；声明了审核页签时只认选中状态和可见面板。
 * 未声明页签的普通配置继续使用原来的路径/特征规则，报废置换行为保持不变。
 */
export function matchPageIdentity(
  catalog: PageCatalog | null,
  url: string,
  text = "",
  reviewTab?: ReviewTabState | null,
): BusinessSelection | null {
  if (!catalog?.identities.length) return null;
  const byPath = pageIdentitiesForUrl(catalog, url);

  if (byPath.some((item) => item.review_tab)) {
    if (!reviewTab?.panelReady) return null;
    const matched = byPath.filter((item) => item.review_tab === reviewTab.label);
    return matched.length === 1 ? toSelection(matched[0]) : null;
  }

  if (byPath.length === 1) return toSelection(byPath[0]);

  if (byPath.length > 1) {
    // 地址共享：靠特征在候选里筛。筛出 0 条或 2 条以上都不猜。
    const matched = byPath.filter((item) => anchorsPresent(item.anchors, text));
    return matched.length === 1 ? toSelection(matched[0]) : null;
  }

  // 地址一条都不命中（页面改版换了路径）：全表按特征兜底，同样只认唯一解。
  const byAnchor = catalog.identities.filter(
    (item) => !item.review_tab && item.anchors.length > 0 && anchorsPresent(item.anchors, text),
  );
  return byAnchor.length === 1 ? toSelection(byAnchor[0]) : null;
}
